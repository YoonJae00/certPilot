"""증적 커넥터와 수집된 증적 모델."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import ConnectorStatus, ConnectorType, EvidenceStatus, enum_column


class Connector(Base):
    """외부 시스템 연결 설정.

    `config_json` 에는 자격증명 원문을 넣지 않는다. Role ARN·외부 ID 같은 값도
    암호화(개발 환경은 Fernet)해서 저장하고, 로그·에러 메시지에 출력하지 않는다(PRD §10).
    """

    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[ConnectorType] = mapped_column(
        enum_column(ConnectorType, "connector_type"), nullable=False
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[ConnectorStatus] = mapped_column(
        enum_column(ConnectorStatus, "connector_status"),
        nullable=False,
        default=ConnectorStatus.PENDING,
    )
    last_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Evidence(Base):
    """수집된 증적 1건. `connector_id` 가 NULL 이면 문서에서 나온 증적이다."""

    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 예: "aws.iam", "aws.s3", "doc". 소스가 계속 늘어나므로 enum 대신 문자열이다.
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    check_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    criterion_codes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[EvidenceStatus] = mapped_column(
        enum_column(EvidenceStatus, "evidence_status"),
        nullable=False,
        default=EvidenceStatus.UNKNOWN,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
