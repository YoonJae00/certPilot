"""유지 대시보드·알림 API (PRD §7 F8).

수치는 전부 `findings`/`alerts` 행에서 직접 집계한다. `assessments.summary_json` 을
그대로 베끼지 않는다 — 대시보드와 DB 집계가 어긋나는 것을 테스트로 잡을 수 있어야
한다. 준비도 산식은 `app/services/scoring.py` 한 곳에만 있다.

모든 엔드포인트는 `load_scoped_project` 로 org 스코프를 먼저 확정한다. 다른 조직의
프로젝트 ID 를 넣으면 404, 심사원(reviewer)은 403 이다.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import CurrentUser, load_scoped_project
from app.models import (
    Alert,
    AlertType,
    Assessment,
    AssessmentStatus,
    Criterion,
    Document,
    Draft,
    DraftStatus,
    Evidence,
    Finding,
    FindingStatus,
    Project,
)
from app.schemas.dashboard import (
    AlertOut,
    AlertReadAllOut,
    AuditDueOut,
    ChapterReadinessOut,
    DashboardOut,
    LastAssessmentOut,
    ReadinessOut,
    TopUnmetOut,
)
from app.services.scoring import readiness_of

router = APIRouter(prefix="/projects/{project_id}", tags=["dashboard"])

# 대시보드 카드에 싣는 개수.
TOP_UNMET_LIMIT = 5
RECENT_ALERT_LIMIT = 5

# 알림 목록 기본·최대 개수.
DEFAULT_ALERT_LIMIT = 20
MAX_ALERT_LIMIT = 100

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")

_ZERO_COUNTS = {"total": 0, "met": 0, "partial": 0, "unmet": 0, "unknown": 0}


def _latest_assessment(
    db: Session, project_id: uuid.UUID, *, done_only: bool = False
) -> Assessment | None:
    """가장 최근 모의심사. `done_only` 면 완료된 실행만 본다."""
    statement = select(Assessment).where(Assessment.project_id == project_id)
    if done_only:
        statement = statement.where(Assessment.status == AssessmentStatus.DONE)
    statement = statement.order_by(
        # 아직 끝나지 않은 실행은 finished_at 이 비어 있으므로 뒤로 보낸다.
        desc(Assessment.finished_at).nulls_last(),
        desc(Assessment.created_at),
        desc(Assessment.id),
    ).limit(1)
    return db.execute(statement).scalars().first()


def _build_readiness(db: Session, assessment_id: uuid.UUID) -> ReadinessOut | None:
    """판정 행을 장별로 집계해 준비도를 만든다. 판정이 없으면 None."""
    rows = db.execute(
        select(Criterion.chapter, Finding.status, func.count(Finding.id))
        .join(Criterion, Criterion.code == Finding.criterion_code)
        .where(Finding.assessment_id == assessment_id)
        .group_by(Criterion.chapter, Finding.status)
    ).all()
    if not rows:
        return None

    by_chapter: dict[str, dict[str, int]] = {}
    totals = dict(_ZERO_COUNTS)
    for chapter, finding_status, count in rows:
        bucket = by_chapter.setdefault(str(chapter), dict(_ZERO_COUNTS))
        bucket[finding_status.value] += count
        bucket["total"] += count
        totals[finding_status.value] += count
        totals["total"] += count

    return ReadinessOut(
        overall=readiness_of(
            met=totals["met"],
            partial=totals["partial"],
            unknown=totals["unknown"],
            total=totals["total"],
        ),
        by_chapter={
            chapter: ChapterReadinessOut(
                total=bucket["total"],
                met=bucket["met"],
                partial=bucket["partial"],
                unmet=bucket["unmet"],
                unknown=bucket["unknown"],
                readiness=readiness_of(
                    met=bucket["met"],
                    partial=bucket["partial"],
                    unknown=bucket["unknown"],
                    total=bucket["total"],
                ),
            )
            for chapter, bucket in sorted(by_chapter.items())
        },
    )


def _top_unmet(db: Session, assessment_id: uuid.UUID) -> list[TopUnmetOut]:
    """미충족 항목 Top N(확신도 높은 순)."""
    rows = db.execute(
        select(
            Finding.criterion_code,
            Criterion.title,
            Finding.confidence,
            Finding.predicted_defect,
        )
        .join(Criterion, Criterion.code == Finding.criterion_code)
        .where(
            Finding.assessment_id == assessment_id,
            Finding.status == FindingStatus.UNMET,
        )
        .order_by(desc(Finding.confidence), asc(Finding.criterion_code))
        .limit(TOP_UNMET_LIMIT)
    ).all()
    return [
        TopUnmetOut(
            criterion_code=code,
            title=title,
            confidence=round(float(confidence), 4),
            predicted_defect=predicted_defect,
        )
        for code, title, confidence, predicted_defect in rows
    ]


def _audit_due(project: Project) -> AuditDueOut | None:
    """사후심사 D-day. 예정일이 없으면 None(화면에서 '미설정')."""
    if project.audit_due_date is None:
        return None
    return AuditDueOut(
        date=project.audit_due_date,
        d_day=(project.audit_due_date - date.today()).days,
    )


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> DashboardOut:
    """유지 대시보드 한 화면에 필요한 수치를 한 번에 돌려준다."""
    project = load_scoped_project(db, user, project_id)

    latest_done = _latest_assessment(db, project.id, done_only=True)
    readiness = _build_readiness(db, latest_done.id) if latest_done else None
    top_unmet = _top_unmet(db, latest_done.id) if latest_done else []

    recent_alerts = list(
        db.execute(
            select(Alert)
            .where(Alert.project_id == project.id)
            .order_by(desc(Alert.created_at), desc(Alert.id))
            .limit(RECENT_ALERT_LIMIT)
        ).scalars()
    )
    unread_alert_count = int(
        db.execute(
            select(func.count(Alert.id)).where(
                Alert.project_id == project.id, Alert.read_at.is_(None)
            )
        ).scalar_one()
    )
    pending_review_count = int(
        db.execute(
            select(func.count(Draft.id)).where(
                Draft.project_id == project.id, Draft.status == DraftStatus.IN_REVIEW
            )
        ).scalar_one()
    )
    document_count = int(
        db.execute(
            select(func.count(Document.id)).where(Document.project_id == project.id)
        ).scalar_one()
    )
    last_collected_at = db.execute(
        select(func.max(Evidence.collected_at)).where(Evidence.project_id == project.id)
    ).scalar_one()

    latest_any = _latest_assessment(db, project.id)

    return DashboardOut(
        readiness=readiness,
        top_unmet=top_unmet,
        recent_alerts=[AlertOut.model_validate(alert) for alert in recent_alerts],
        unread_alert_count=unread_alert_count,
        audit_due=_audit_due(project),
        pending_review_count=pending_review_count,
        last_collected_at=last_collected_at,
        document_count=document_count,
        last_assessment=(
            LastAssessmentOut.model_validate(latest_any) if latest_any else None
        ),
    )


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    alert_type: Annotated[AlertType | None, Query(alias="type", description="알림 종류")] = None,
    unread_only: Annotated[bool, Query(description="읽지 않은 알림만")] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_ALERT_LIMIT, description="최대 개수")] = (
        DEFAULT_ALERT_LIMIT
    ),
) -> list[AlertOut]:
    """알림 목록(최신순)."""
    project = load_scoped_project(db, user, project_id)

    statement = select(Alert).where(Alert.project_id == project.id)
    if alert_type is not None:
        statement = statement.where(Alert.type == alert_type)
    if unread_only:
        statement = statement.where(Alert.read_at.is_(None))
    statement = statement.order_by(desc(Alert.created_at), desc(Alert.id)).limit(limit)

    return [AlertOut.model_validate(alert) for alert in db.execute(statement).scalars()]


@router.patch("/alerts/read-all", response_model=AlertReadAllOut)
def mark_all_alerts_read(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> AlertReadAllOut:
    """읽지 않은 알림을 모두 읽음 처리한다. 이미 다 읽었으면 0 건이다."""
    project = load_scoped_project(db, user, project_id)

    unread = list(
        db.execute(
            select(Alert).where(Alert.project_id == project.id, Alert.read_at.is_(None))
        ).scalars()
    )
    now = datetime.now(UTC)
    for alert in unread:
        alert.read_at = now
    db.commit()
    return AlertReadAllOut(updated=len(unread))


@router.patch("/alerts/{alert_id}/read", response_model=AlertOut)
def mark_alert_read(
    project_id: uuid.UUID,
    alert_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> AlertOut:
    """알림 1건을 읽음 처리한다(멱등 — 이미 읽었으면 시각을 덮어쓰지 않는다)."""
    project = load_scoped_project(db, user, project_id)

    alert = db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.project_id == project.id)
    ).scalar_one_or_none()
    if alert is None:
        raise _NOT_FOUND

    if alert.read_at is None:
        alert.read_at = datetime.now(UTC)
        db.commit()
        db.refresh(alert)

    return AlertOut.model_validate(alert)
