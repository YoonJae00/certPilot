"""유지 대시보드·알림 스키마 (PRD §7 F8).

`datetime` 모듈을 통째로 임포트한다. `AuditDueOut.date` 처럼 필드 이름과 타입
이름이 겹치는 곳에서 이름이 가려지는 것을 피하기 위해서다.
"""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import AlertType, AssessmentStatus


class ChapterReadinessOut(BaseModel):
    """장별 판정 집계와 준비도."""

    total: int
    met: int
    partial: int
    unmet: int
    unknown: int
    # 0~1 비율. 산식은 app/services/scoring.py 한 곳에만 있다.
    readiness: float


class ReadinessOut(BaseModel):
    """전체·장별 준비도. 키는 장 번호 문자열("1"|"2"|"3")."""

    overall: float
    by_chapter: dict[str, ChapterReadinessOut] = Field(default_factory=dict)


class TopUnmetOut(BaseModel):
    """미충족 Top 항목 1건."""

    criterion_code: str
    title: str
    confidence: float
    predicted_defect: str | None = None


class AlertOut(BaseModel):
    """대시보드 알림 1건."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: AlertType
    message: str
    evidence_id: uuid.UUID | None = None
    read_at: datetime.datetime | None = None
    created_at: datetime.datetime


class AlertReadAllOut(BaseModel):
    """모두 읽음 처리 결과."""

    updated: int


class AuditDueOut(BaseModel):
    """사후심사 예정일과 남은 일수. 지났으면 `d_day` 가 음수다."""

    date: datetime.date
    d_day: int


class LastAssessmentOut(BaseModel):
    """가장 최근 모의심사 실행(상태 무관)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: AssessmentStatus
    finished_at: datetime.datetime | None = None


class DashboardOut(BaseModel):
    """유지 대시보드 응답 (PRD §7 F8)."""

    # 완료된 모의심사가 하나도 없으면 null 이다.
    readiness: ReadinessOut | None = None
    top_unmet: list[TopUnmetOut] = Field(default_factory=list)
    recent_alerts: list[AlertOut] = Field(default_factory=list)
    unread_alert_count: int
    audit_due: AuditDueOut | None = None
    pending_review_count: int
    last_collected_at: datetime.datetime | None = None
    document_count: int
    last_assessment: LastAssessmentOut | None = None
