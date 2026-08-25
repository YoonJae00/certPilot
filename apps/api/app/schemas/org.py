"""조직·사용자 생성 스키마. 운영자 전용 API 에서 쓴다."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import OrgPlan, UserRole
from app.schemas.auth import normalize_email


class OrgCreate(BaseModel):
    """조직 생성 요청."""

    name: str = Field(min_length=1, max_length=200)
    plan: OrgPlan = OrgPlan.SIMPLIFIED


class OrgOut(BaseModel):
    """조직 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    plan: OrgPlan
    created_at: datetime


class UserCreate(BaseModel):
    """조직 소속 사용자 생성 요청. 역할은 조직 역할 두 가지만 허용한다."""

    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=72)
    role: UserRole = UserRole.ORG_MEMBER

    @field_validator("email")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("role")
    @classmethod
    def _org_role_only(cls, value: UserRole) -> UserRole:
        if value not in (UserRole.ORG_ADMIN, UserRole.ORG_MEMBER):
            raise ValueError("조직 소속 사용자는 org_admin 또는 org_member 여야 한다")
        return value
