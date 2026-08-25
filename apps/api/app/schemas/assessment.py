"""모의심사·판정 스키마."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import AssessmentStatus, DecidedBy, EvidenceStatus, FindingStatus


class AssessmentOut(BaseModel):
    """모의심사 실행 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: AssessmentStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model: str | None = None
    # Numeric(10,4) 를 float 로 내린다(프런트에서 쓰기 편하다).
    cost_usd: float | None = None
    summary_json: dict[str, Any] | None = None
    created_at: datetime


class FindingOut(BaseModel):
    """항목별 판정 1건. 인증기준 제목·분류를 붙여 준다."""

    id: uuid.UUID
    criterion_code: str
    chapter: int
    section: str
    title: str
    status: FindingStatus
    confidence: float
    rationale: str
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    predicted_defect: str | None = None
    recommendation: str | None = None
    decided_by: DecidedBy
    created_at: datetime


class FindingChunkOut(BaseModel):
    """판정 근거가 된 문서 청크. 본문 전체를 준다(하이라이트용)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None = None
    text: str


class FindingEvidenceOut(BaseModel):
    """판정 근거가 된 커넥터 증적."""

    evidence_id: uuid.UUID
    source: str
    check_id: str
    status: EvidenceStatus
    collected_at: datetime
    payload_json: dict[str, Any] = Field(default_factory=dict)


class FindingDetailOut(FindingOut):
    """판정 상세. 근거 청크 본문과 증적 payload 를 함께 싣는다."""

    criterion_requirement: str
    chunks: list[FindingChunkOut] = Field(default_factory=list)
    evidence: list[FindingEvidenceOut] = Field(default_factory=list)
