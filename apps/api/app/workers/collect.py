"""증적 수집 잡 (PRD §7 F5).

흐름: 커넥터 설정 복호화 → 점검 10개 실행 → 하나의 `snapshot_id` 로 `evidence` 저장
→ 직전 스냅샷과 diff → 변화가 있으면 `alerts(type=drift)` 생성 → `last_collected_at` 갱신.

Celery 없이도 그대로 돌아가야 한다. 실제 로직은 동기 함수 `run_collect` 에 있고
태스크는 그걸 감싸기만 한다(`ingest.py` 와 같은 규칙).
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from celery.schedules import crontab
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.connectors.aws import ClientFactory, ConnectorError, build_client_factory, run_all_checks
from app.connectors.credentials import load_auth
from app.connectors.drift import DriftChange, SnapshotItem, detect_drift
from app.connectors.mapping import CheckMapping, load_check_mappings
from app.core.db import get_session_factory
from app.models import (
    Alert,
    AlertType,
    Connector,
    ConnectorStatus,
    ConnectorType,
    Evidence,
    EvidenceStatus,
    Project,
)
from app.services.audit import record_audit
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# 감사 로그 액션(PRD §10: 커넥터 변경은 반드시 남긴다).
AUDIT_COLLECT_ACTION = "connector.collect"

# 하루 1회 수집 스케줄(한국 시간 새벽 3시). `celery_app.conf.beat_schedule` 에 붙는다.
DAILY_COLLECT_SCHEDULE_NAME = "collect-connectors-daily"
DAILY_COLLECT_HOUR = 3
DAILY_COLLECT_MINUTE = 0


@dataclass
class CollectResult:
    """수집 1회 결과 요약."""

    connector_id: uuid.UUID
    snapshot_id: str
    evidence_count: int
    alert_count: int
    status_counts: dict[str, int] = field(default_factory=dict)

    def __repr__(self) -> str:
        """디버깅용 표현."""
        return (
            f"CollectResult(connector_id={self.connector_id}, snapshot_id={self.snapshot_id}, "
            f"evidence_count={self.evidence_count}, alert_count={self.alert_count})"
        )


def _load_connector(db: Session, connector_id: uuid.UUID) -> Connector:
    """커넥터를 읽는다. 없으면 `ValueError`."""
    connector = db.execute(
        select(Connector).where(Connector.id == connector_id)
    ).scalar_one_or_none()
    if connector is None:
        raise ValueError(f"커넥터를 찾을 수 없다: {connector_id}")
    return connector


def _latest_snapshot(db: Session, connector_id: uuid.UUID) -> dict[str, SnapshotItem]:
    """커넥터의 직전 스냅샷을 check_id 별로 읽는다. 없으면 빈 딕셔너리."""
    snapshot_id = db.execute(
        select(Evidence.snapshot_id)
        .where(Evidence.connector_id == connector_id, Evidence.snapshot_id.isnot(None))
        .order_by(desc(Evidence.collected_at), desc(Evidence.id))
        .limit(1)
    ).scalar_one_or_none()
    if not snapshot_id:
        return {}

    rows = db.execute(
        select(Evidence).where(
            Evidence.connector_id == connector_id, Evidence.snapshot_id == snapshot_id
        )
    ).scalars()
    return {
        row.check_id: SnapshotItem(
            check_id=row.check_id, status=row.status, payload=dict(row.payload_json or {})
        )
        for row in rows
    }


def _mark_error(db: Session, connector: Connector, reason: str, org_id: uuid.UUID | None) -> None:
    """연결 실패를 커넥터 상태와 감사 로그에 남긴다. 사유에 자격증명은 없다."""
    connector.status = ConnectorStatus.ERROR
    record_audit(
        db,
        action=AUDIT_COLLECT_ACTION,
        org_id=org_id,
        target=str(connector.id),
        meta={"result": "error", "reason": reason},
    )
    db.commit()


def run_collect(
    connector_id: uuid.UUID,
    *,
    db: Session | None = None,
    clients: ClientFactory | None = None,
) -> CollectResult:
    """커넥터 1개의 증적을 수집한다(동기).

    `clients` 를 넘기면 그 팩토리를 그대로 쓴다(테스트용). 넘기지 않으면 저장된
    설정을 복호화해 만든다.
    """
    owns_session = db is None
    session = db or get_session_factory()()
    try:
        connector = _load_connector(session, connector_id)
        org_id = session.execute(
            select(Project.org_id).where(Project.id == connector.project_id)
        ).scalar_one_or_none()

        if connector.type is not ConnectorType.AWS:
            raise ValueError(f"아직 지원하지 않는 커넥터 종류다: {connector.type.value}")

        factory = clients
        if factory is None:
            try:
                factory = build_client_factory(load_auth(dict(connector.config_json or {})))
            except ConnectorError as error:
                logger.warning("커넥터 연결 실패: connector_id=%s 사유=%s", connector_id, error)
                _mark_error(session, connector, str(error), org_id)
                raise

        mappings: dict[str, CheckMapping] = load_check_mappings()
        outcomes = run_all_checks(factory)

        snapshot_id = uuid.uuid4().hex
        collected_at = datetime.now(UTC)

        current: dict[str, SnapshotItem] = {}
        rows: list[Evidence] = []
        status_counts: dict[str, int] = {status.value: 0 for status in EvidenceStatus}
        for outcome in outcomes:
            mapping = mappings.get(outcome.check_id)
            if mapping is None:
                # 매핑이 없는 점검은 저장하지 않는다(항목 매핑 없는 증적은 쓸모가 없다).
                logger.warning("매핑 없는 점검 결과를 건너뛴다: check_id=%s", outcome.check_id)
                continue
            rows.append(
                Evidence(
                    project_id=connector.project_id,
                    connector_id=connector.id,
                    source=mapping.source,
                    check_id=outcome.check_id,
                    criterion_codes=list(mapping.criterion_codes),
                    status=outcome.status,
                    payload_json=outcome.payload,
                    collected_at=collected_at,
                    snapshot_id=snapshot_id,
                )
            )
            current[outcome.check_id] = SnapshotItem(
                check_id=outcome.check_id, status=outcome.status, payload=outcome.payload
            )
            status_counts[outcome.status.value] += 1

        previous = _latest_snapshot(session, connector.id)
        session.add_all(rows)
        session.flush()

        changes: list[DriftChange] = detect_drift(previous, current, mappings=mappings)
        evidence_by_check = {row.check_id: row for row in rows}
        for change in changes:
            evidence = evidence_by_check.get(change.check_id)
            session.add(
                Alert(
                    project_id=connector.project_id,
                    type=AlertType.DRIFT,
                    message=change.message,
                    evidence_id=evidence.id if evidence is not None else None,
                )
            )

        connector.last_collected_at = collected_at
        connector.status = ConnectorStatus.CONNECTED
        record_audit(
            session,
            action=AUDIT_COLLECT_ACTION,
            org_id=org_id,
            target=str(connector.id),
            meta={
                "result": "ok",
                "snapshot_id": snapshot_id,
                "evidence_count": len(rows),
                "alert_count": len(changes),
                "status_counts": status_counts,
            },
        )
        session.commit()

        logger.info(
            "증적 수집 완료: connector_id=%s 스냅샷=%s 증적=%d 알림=%d",
            connector_id,
            snapshot_id,
            len(rows),
            len(changes),
        )
        return CollectResult(
            connector_id=connector_id,
            snapshot_id=snapshot_id,
            evidence_count=len(rows),
            alert_count=len(changes),
            status_counts=status_counts,
        )
    except ConnectorError:
        # 상태·감사 로그는 이미 남겼다. 예외는 삼키지 않고 그대로 올린다.
        raise
    except Exception:
        session.rollback()
        logger.exception("증적 수집 중 처리하지 못한 예외: connector_id=%s", connector_id)
        raise
    finally:
        if owns_session:
            session.close()


def connected_connector_ids(db: Session) -> list[uuid.UUID]:
    """스케줄 수집 대상(연결됐거나 이전에 수집한 적 있는 AWS 커넥터)."""
    rows = db.execute(
        select(Connector.id).where(
            Connector.type == ConnectorType.AWS,
            Connector.status.in_([ConnectorStatus.CONNECTED, ConnectorStatus.ERROR]),
        )
    ).scalars()
    return list(rows)


@celery_app.task(name="certpilot.collect_connector")
def collect_connector(connector_id: str) -> dict[str, Any]:
    """Celery 태스크. 동기 함수 `run_collect` 를 감싸기만 한다(수동 실행용)."""
    result = run_collect(uuid.UUID(connector_id))
    return {
        "connector_id": str(result.connector_id),
        "snapshot_id": result.snapshot_id,
        "evidence_count": result.evidence_count,
        "alert_count": result.alert_count,
    }


@celery_app.task(name="certpilot.collect_all_connectors")
def collect_all_connectors() -> dict[str, int]:
    """하루 1회 스케줄 태스크. 대상 커넥터마다 수집 잡을 큐에 넣는다."""
    session = get_session_factory()()
    try:
        connector_ids = connected_connector_ids(session)
    finally:
        session.close()

    for connector_id in connector_ids:
        collect_connector.apply_async(args=[str(connector_id)], retry=False)
    logger.info("스케줄 수집 큐잉: 커넥터=%d", len(connector_ids))
    return {"queued": len(connector_ids)}


def enqueue_collect(connector_id: uuid.UUID) -> bool:
    """수집 잡을 큐에 넣는다. 워커·브로커가 없으면 False 를 돌려준다.

    호출 측(API)은 False 를 받으면 요청 스레드에서 동기로 실행한다.
    """
    try:
        replies = celery_app.control.ping(timeout=0.5)
    except Exception:  # noqa: BLE001 - 브로커가 죽어 있어도 API 는 계속 동작해야 한다
        logger.info("Celery 브로커에 접속할 수 없다. 동기 수집으로 폴백한다", exc_info=True)
        return False
    if not replies:
        logger.info("Celery 워커가 없어 동기 수집으로 폴백한다: connector_id=%s", connector_id)
        return False

    try:
        collect_connector.apply_async(args=[str(connector_id)], retry=False)
        return True
    except Exception:  # noqa: BLE001 - 큐잉 실패가 API 를 500 으로 만들면 안 된다
        logger.warning(
            "수집 큐잉 실패, 동기 수집으로 폴백한다: connector_id=%s", connector_id, exc_info=True
        )
        return False


# 하루 1회 스케줄 등록. beat 를 띄우면 적용된다:
#   uv run celery -A app.workers.celery_app:celery_app beat -l info
celery_app.conf.beat_schedule = {
    **(celery_app.conf.beat_schedule or {}),
    DAILY_COLLECT_SCHEDULE_NAME: {
        "task": "certpilot.collect_all_connectors",
        "schedule": crontab(hour=DAILY_COLLECT_HOUR, minute=DAILY_COLLECT_MINUTE),
    },
}
