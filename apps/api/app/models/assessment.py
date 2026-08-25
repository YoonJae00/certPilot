"""모의심사와 항목별 판정 모델."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import AssessmentStatus, DecidedBy, FindingStatus, enum_column


class Assessment(Base):
    """모의심사 1회 실행."""

    __tablename__ = "assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[AssessmentStatus] = mapped_column(
        enum_column(AssessmentStatus, "assessment_status"),
        nullable=False,
        default=AssessmentStatus.QUEUED,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Finding(Base):
    """인증기준 항목 하나에 대한 판정 결과.

    근거(`evidence_chunk_ids`/`evidence_ids`)가 모두 비면 서비스 계층이
    `status` 를 `unknown` 으로 강제한다(CLAUDE.md 절대 규칙 2).
    """

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    criterion_code: Mapped[str] = mapped_column(
        String(16), ForeignKey("criteria.code", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[FindingStatus] = mapped_column(
        enum_column(FindingStatus, "finding_status"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_chunk_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    predicted_defect: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[DecidedBy] = mapped_column(
        enum_column(DecidedBy, "decided_by"), nullable=False, default=DecidedBy.LLM
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
