"""인증 관련 스키마."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import UserRole


def normalize_email(value: str) -> str:
    """이메일을 소문자·공백 제거로 정규화하고 최소 형식만 확인한다.

    `email-validator` 의존성을 추가하지 않기 위해 검증은 의도적으로 느슨하다.
    """
    email = value.strip().lower()
    local, sep, domain = email.partition("@")
    if not sep or not local or "." not in domain:
        raise ValueError("이메일 형식이 올바르지 않다")
    return email


class LoginRequest(BaseModel):
    """로그인 요청. 시제품에는 회원가입이 없고 운영자가 계정을 만든다."""

    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_email(value)


class UserOut(BaseModel):
    """사용자 응답. 비밀번호 해시는 절대 내보내지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    email: str
    role: UserRole
    created_at: datetime


class MessageResponse(BaseModel):
    """단순 메시지 응답."""

    message: str
