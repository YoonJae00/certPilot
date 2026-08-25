"""프로젝트 스키마."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import CertType


class ProjectCreate(BaseModel):
    """프로젝트 생성 요청. org_id 는 서버가 세션에서 정한다(클라이언트가 못 정한다)."""

    name: str = Field(min_length=1, max_length=200)
    cert_type: CertType
    is_simplified: bool = False
    scope_text: str | None = None
    audit_due_date: date | None = None


class ProjectUpdate(BaseModel):
    """프로젝트 부분 수정 요청. 보낸 필드만 바뀐다."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    cert_type: CertType | None = None
    is_simplified: bool | None = None
    scope_text: str | None = None
    audit_due_date: date | None = None


class ProjectOut(BaseModel):
    """프로젝트 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    cert_type: CertType
    is_simplified: bool
    scope_text: str | None
    audit_due_date: date | None
    created_at: datetime
    updated_at: datetime
