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
from app.models import User, UserRole
from app.schemas.auth import LoginRequest, MessageResponse, UserOut
from app.services.audit import record_audit
from app.services.demo_accounts import DEMO_MEMBER_EMAIL

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


@router.post("/demo-login", response_model=UserOut)
def demo_login(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """비밀번호 없이 시드된 데모 계정(org_member)으로 세션을 발급한다.

    공개 시연 서버에서 방문자가 계정 없이 둘러보게 하는 용도다. 비밀번호를
    클라이언트에 노출하지 않으려고 서버가 직접 세션을 심는다.

    `DEMO_LOGIN_ENABLED` 가 꺼져 있으면 403 이 아니라 **404** 로 답한다. 403 은
    "그런 기능이 있는데 막혔다"를 알려 주는 셈이라, 기능 자체가 없는 것처럼 보이게 한다.
    """
    settings = get_settings()
    if not settings.demo_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not Found")

    user = db.execute(select(User).where(User.email == DEMO_MEMBER_EMAIL)).scalar_one_or_none()
    # 시드 전이거나(계정 없음) 시드 구성이 바뀌어 역할이 다르면 체험을 열지 않는다.
    # 후자는 서버 오류가 아니라 "체험 데이터가 없는 상태"이므로 500 이 아닌 404 다.
    if user is None or user.role is not UserRole.ORG_MEMBER:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="데모 데이터가 준비되지 않았습니다. 관리자에게 문의하세요.",
        )

    record_audit(
        db,
        action="login",
        org_id=user.org_id,
        user_id=user.id,
        target=user.email,
        meta={"result": "success", "demo": True},
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
