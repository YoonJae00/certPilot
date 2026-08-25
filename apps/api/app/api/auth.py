"""인증 라우터.

시제품에는 회원가입이 없다. 운영자(또는 시드 스크립트)가 계정을 만들고,
사용자는 이메일+비밀번호로 로그인해 서명 세션 쿠키를 받는다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.rbac import CurrentUser
from app.core.security import create_session_token, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, MessageResponse, UserOut
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """이메일+비밀번호로 로그인하고 세션 쿠키를 심는다.

    성공·실패 모두 `audit_logs` 에 action="login" 으로 남긴다. 계정 존재 여부를
    흘리지 않도록 실패 응답은 한 가지 문구만 쓴다.
    """
    settings = get_settings()
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        record_audit(
            db,
            action="login",
            org_id=user.org_id if user is not None else None,
            user_id=user.id if user is not None else None,
            target=payload.email,
            meta={"result": "failure"},
        )
        db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="이메일 또는 비밀번호가 올바르지 않다"
        )

    record_audit(
        db,
        action="login",
        org_id=user.org_id,
        user_id=user.id,
        target=payload.email,
        meta={"result": "success"},
    )
    db.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=create_session_token(user.id),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return user


@router.post("/logout", response_model=MessageResponse)
def logout(
    user: CurrentUser,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    """세션 쿠키를 지운다."""
    settings = get_settings()
    record_audit(db, action="logout", org_id=user.org_id, user_id=user.id, target=user.email)
    db.commit()
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return MessageResponse(message="로그아웃되었다")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    """현재 로그인한 사용자를 돌려준다."""
    return user
