"""조직 라우터. 조직·사용자 생성은 운영자 전용이다(시제품에는 셀프 가입이 없다)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import CurrentUser, assert_org_access, require_roles
from app.core.security import PasswordTooLongError, hash_password
from app.models import Organization, User, UserRole
from app.schemas.auth import UserOut
from app.schemas.org import OrgCreate, OrgOut, UserCreate
from app.services.audit import record_audit

router = APIRouter(prefix="/orgs", tags=["orgs"])

OperatorUser = Annotated[User, Depends(require_roles(UserRole.OPERATOR))]


def _get_org_or_404(db: Session, org_id: uuid.UUID) -> Organization:
    """조직을 읽고 없으면 404."""
    org = db.execute(
        select(Organization).where(Organization.id == org_id)
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")
    return org


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
def create_org(
    payload: OrgCreate,
    user: OperatorUser,
    db: Annotated[Session, Depends(get_db)],
) -> Organization:
    """조직을 만든다. 운영자만 가능하다."""
    org = Organization(name=payload.name, plan=payload.plan)
    db.add(org)
    db.flush()
    record_audit(
        db, action="org.create", org_id=org.id, user_id=user.id, target=str(org.id)
    )
    db.commit()
    db.refresh(org)
    return org


@router.get("", response_model=list[OrgOut])
def list_orgs(
    user: OperatorUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[Organization]:
    """전체 조직 목록. 운영자만 볼 수 있다."""
    return list(db.execute(select(Organization).order_by(Organization.created_at)).scalars())


@router.get("/{org_id}", response_model=OrgOut)
def get_org(
    org_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Organization:
    """조직 1건. 운영자이거나 그 조직 소속이어야 한다."""
    assert_org_access(user, org_id)
    return _get_org_or_404(db, org_id)


@router.post("/{org_id}/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_org_user(
    org_id: uuid.UUID,
    payload: UserCreate,
    user: OperatorUser,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """조직 소속 사용자를 만든다. 운영자만 가능하다."""
    org = _get_org_or_404(db, org_id)

    exists = db.execute(select(User.id).where(User.email == payload.email)).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용 중인 이메일이다")

    try:
        password_hash = hash_password(payload.password)
    except PasswordTooLongError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    created = User(
        org_id=org.id,
        email=payload.email,
        role=payload.role,
        password_hash=password_hash,
    )
    db.add(created)
    db.flush()
    record_audit(
        db,
        action="user.create",
        org_id=org.id,
        user_id=user.id,
        target=str(created.id),
        meta={"role": created.role.value},
    )
    db.commit()
    db.refresh(created)
    return created
