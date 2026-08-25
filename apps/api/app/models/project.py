"""프로젝트(인증범위) 모델."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import CertType, enum_column


class Project(Base):
    """인증 준비 단위. 문서·증적·모의심사가 모두 이 아래에 매달린다."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cert_type: Mapped[CertType] = mapped_column(enum_column(CertType, "cert_type"), nullable=False)
    is_simplified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
