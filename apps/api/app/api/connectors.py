"""증적 커넥터 API (PRD §7 F5).

모든 엔드포인트는 `load_scoped_project` 로 org 스코프를 먼저 확정한다. 다른 조직의
프로젝트 ID 를 넣으면 존재 여부를 흘리지 않도록 404 다. 생성·수집은 org_admin 만
할 수 있고, 심사원(reviewer)은 조직 스코프 API 에 접근할 수 없다(403).

응답에 자격증명은 어떤 형태로도 나가지 않는다. 설정은 마스킹된 요약만 돌려준다.
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.connectors.aws import (
    AwsAuth,
    ConnectorError,
    build_client_factory,
    test_connection,
)
from app.connectors.credentials import build_stored_config, summarize_config
from app.connectors.mapping import CheckMapping, load_check_mappings
from app.core.db import get_db
from app.core.rbac import CurrentUser, load_scoped_project, require_roles
from app.models import Connector, ConnectorStatus, ConnectorType, Evidence, User, UserRole
from app.schemas.connector import (
    AwsConnectorConfigIn,
    CollectResponse,
    ConnectorConfigSummary,
    ConnectorCreate,
    ConnectorOut,
    EvidenceOut,
    LatestEvidenceResponse,
)
from app.services.audit import record_audit
from app.workers.collect import enqueue_collect, run_collect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/connectors", tags=["connectors"])

ConnectorAdmin = Annotated[User, Depends(require_roles(UserRole.ORG_ADMIN))]

AUDIT_CREATE_ACTION = "connector.create"

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")

# 증적 목록 응답 상한. 점검이 10개뿐이라 넉넉하다.
MAX_EVIDENCE_LIMIT = 500


def _to_auth(config: AwsConnectorConfigIn) -> AwsAuth:
    """요청 스키마를 내부 연결 정보로 옮긴다(여기서만 평문을 만진다)."""
    return AwsAuth(
        auth_type=config.auth_type,
        region=config.region.strip(),
        role_arn=(config.role_arn or "").strip() or None,
        external_id=(
            config.external_id.get_secret_value().strip() if config.external_id else None
        ),
        access_key_id=(config.access_key_id or "").strip() or None,
        secret_access_key=(
            config.secret_access_key.get_secret_value() if config.secret_access_key else None
        ),
    )


def _to_out(connector: Connector, *, error_reason: str | None = None) -> ConnectorOut:
    """ORM 커넥터를 응답 모델로 옮긴다. 설정은 마스킹 요약만 나간다."""
    summary = summarize_config(dict(connector.config_json or {}))
    return ConnectorOut(
        id=connector.id,
        project_id=connector.project_id,
        type=connector.type,
        status=connector.status,
        last_collected_at=connector.last_collected_at,
        created_at=connector.created_at,
        config=ConnectorConfigSummary(
            auth_type=str(summary.get("auth_type") or ""),
            region=str(summary.get("region") or ""),
            role_arn_masked=summary.get("role_arn_masked"),
            access_key_id_masked=summary.get("access_key_id_masked"),
            account_id_masked=summary.get("account_id_masked"),
        ),
        error_reason=error_reason,
    )


def _to_evidence_out(row: Evidence, mappings: dict[str, CheckMapping]) -> EvidenceOut:
    """증적 행에 매핑 파일의 표시명·pass 조건을 붙여 응답 모델로 옮긴다."""
    mapping = mappings.get(row.check_id)
    return EvidenceOut(
        id=row.id,
        connector_id=row.connector_id,
        source=row.source,
        check_id=row.check_id,
        criterion_codes=[str(code) for code in row.criterion_codes or []],
        status=row.status,
        payload_json=dict(row.payload_json or {}),
        collected_at=row.collected_at,
        snapshot_id=row.snapshot_id,
        title=mapping.title if mapping else None,
        pass_condition=mapping.pass_condition if mapping else None,
    )


def _get_connector(db: Session, project_id: uuid.UUID, connector_id: uuid.UUID) -> Connector:
    """프로젝트 스코프 안에서만 커넥터를 읽는다."""
    connector = db.execute(
        select(Connector).where(
            Connector.id == connector_id, Connector.project_id == project_id
        )
    ).scalar_one_or_none()
    if connector is None:
        raise _NOT_FOUND
    return connector


@router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
def create_connector(
    project_id: uuid.UUID,
    payload: ConnectorCreate,
    user: ConnectorAdmin,
    db: Annotated[Session, Depends(get_db)],
) -> ConnectorOut:
    """커넥터를 만든다. 자격증명은 암호화해 저장하고 연결 테스트 결과를 상태로 남긴다."""
    project = load_scoped_project(db, user, project_id)

    if payload.type is not ConnectorType.AWS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="아직 AWS 커넥터만 지원한다",
        )

    auth = _to_auth(payload.config)

    # 연결 테스트: 읽기 전용 호출(sts:GetCallerIdentity)만 쓴다.
    account_id_masked: str | None = None
    error_reason: str | None = None
    try:
        account_id_masked = test_connection(build_client_factory(auth))
        connector_status = ConnectorStatus.CONNECTED
    except ConnectorError as error:
        # 사유에는 AWS 오류 코드만 들어간다(자격증명·원본 메시지 없음).
        error_reason = str(error)
        connector_status = ConnectorStatus.ERROR
        logger.warning("커넥터 연결 테스트 실패: project_id=%s 사유=%s", project.id, error_reason)

    connector = Connector(
        project_id=project.id,
        type=ConnectorType.AWS,
        config_json=build_stored_config(auth, account_id_masked=account_id_masked),
        status=connector_status,
    )
    db.add(connector)
    db.flush()
    record_audit(
        db,
        action=AUDIT_CREATE_ACTION,
        org_id=project.org_id,
        user_id=user.id,
        target=str(connector.id),
        # 자격증명은 남기지 않는다. 연결 방식·리전·결과만 남긴다.
        meta={
            "type": ConnectorType.AWS.value,
            "auth_type": auth.auth_type,
            "region": auth.region,
            "result": connector_status.value,
        },
    )
    db.commit()
    db.refresh(connector)

    return _to_out(connector, error_reason=error_reason)


@router.get("", response_model=list[ConnectorOut])
def list_connectors(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[ConnectorOut]:
    """프로젝트의 커넥터 목록(최신순). 설정은 마스킹 요약만 나간다."""
    project = load_scoped_project(db, user, project_id)
    rows = list(
        db.execute(
            select(Connector)
            .where(Connector.project_id == project.id)
            .order_by(desc(Connector.created_at), desc(Connector.id))
        ).scalars()
    )
    return [_to_out(row) for row in rows]


@router.get("/evidence/latest", response_model=LatestEvidenceResponse)
def latest_evidence(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> LatestEvidenceResponse:
    """프로젝트의 최신 증적을 check_id 별로 하나씩 돌려준다(항목 매핑 포함)."""
    project = load_scoped_project(db, user, project_id)

    rows = list(
        db.execute(
            select(Evidence)
            .where(Evidence.project_id == project.id, Evidence.connector_id.isnot(None))
            # check_id 별 최신 1건(Postgres DISTINCT ON).
            .distinct(Evidence.check_id)
            .order_by(Evidence.check_id, desc(Evidence.collected_at), desc(Evidence.id))
        ).scalars()
    )

    mappings = load_check_mappings()
    items = [_to_evidence_out(row, mappings) for row in rows]
    newest = max(rows, key=lambda row: row.collected_at) if rows else None
    return LatestEvidenceResponse(
        project_id=project.id,
        snapshot_id=newest.snapshot_id if newest else None,
        collected_at=newest.collected_at if newest else None,
        items=items,
    )


@router.post(
    "/{connector_id}/collect",
    response_model=CollectResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def collect_now(
    project_id: uuid.UUID,
    connector_id: uuid.UUID,
    user: ConnectorAdmin,
    db: Annotated[Session, Depends(get_db)],
) -> CollectResponse:
    """수동 수집. Celery 워커가 없으면 요청 스레드에서 동기로 실행한다."""
    project = load_scoped_project(db, user, project_id)
    connector = _get_connector(db, project.id, connector_id)

    if enqueue_collect(connector.id):
        return CollectResponse(connector_id=connector.id, state="queued")

    try:
        result = run_collect(connector.id, db=db)
    except ConnectorError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    return CollectResponse(
        connector_id=connector.id,
        state="done",
        snapshot_id=result.snapshot_id,
        evidence_count=result.evidence_count,
        alert_count=result.alert_count,
        status_counts=result.status_counts,
    )


@router.get("/{connector_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(
    project_id: uuid.UUID,
    connector_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    snapshot: Annotated[str | None, Query(description="스냅샷 ID 필터")] = None,
    check_id: Annotated[str | None, Query(description="점검 ID 필터")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_EVIDENCE_LIMIT)] = 100,
) -> list[EvidenceOut]:
    """커넥터가 수집한 증적 목록(최신순)."""
    project = load_scoped_project(db, user, project_id)
    connector = _get_connector(db, project.id, connector_id)

    filters: list[Any] = [
        Evidence.project_id == project.id,
        Evidence.connector_id == connector.id,
    ]
    if snapshot:
        filters.append(Evidence.snapshot_id == snapshot)
    if check_id:
        filters.append(Evidence.check_id == check_id)

    rows = list(
        db.execute(
            select(Evidence)
            .where(*filters)
            .order_by(desc(Evidence.collected_at), Evidence.check_id)
            .limit(limit)
        ).scalars()
    )
    mappings = load_check_mappings()
    return [_to_evidence_out(row, mappings) for row in rows]
