"""프로젝트 라우터.

모든 조회에 org_id 필터를 건다. 단건 조회는 먼저 org 스코프를 확인하고,
권한이 없으면 존재 여부를 흘리지 않도록 404 를 돌려준다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import (
    CurrentUser,
    assert_org_access,
    load_scoped_project,
    require_roles,
    resolve_org_scope,
)
from app.models import Project, User, UserRole
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/projects", tags=["projects"])

OrgAdminUser = Annotated[User, Depends(require_roles(UserRole.ORG_ADMIN))]


def _get_project_or_404(db: Session, project_id: uuid.UUID, org_id: uuid.UUID) -> Project:
    """org_id 스코프 안에서만 프로젝트를 읽는다."""
    project = db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == org_id)
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")
    return project


def _load_scoped_project(db: Session, user: User, project_id: uuid.UUID) -> Project:
    """현재 사용자가 접근 가능한 프로젝트만 읽는다(구현은 rbac 에 하나만 둔다)."""
    return load_scoped_project(db, user, project_id)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    user: OrgAdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> Project:
    """프로젝트를 만든다. 조직 관리자만 가능하며 자기 조직에만 만들 수 있다."""
    if user.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="소속 조직이 없는 계정이다")

    project = Project(
        org_id=user.org_id,
        name=payload.name,
        cert_type=payload.cert_type,
        is_simplified=payload.is_simplified,
        scope_text=payload.scope_text,
        audit_due_date=payload.audit_due_date,
    )
    db.add(project)
    db.flush()
    record_audit(
        db,
        action="project.create",
        org_id=project.org_id,
        user_id=user.id,
        target=str(project.id),
    )
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    org_id: Annotated[uuid.UUID | None, Query(description="운영자 전용 조직 필터")] = None,
) -> list[Project]:
    """프로젝트 목록. 항상 하나의 org_id 로 스코프된다."""
    scope_org_id = resolve_org_scope(user, org_id)
    rows = db.execute(
        select(Project).where(Project.org_id == scope_org_id).order_by(Project.created_at)
    ).scalars()
    return list(rows)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Project:
    """프로젝트 1건."""
    return _load_scoped_project(db, user, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    user: OrgAdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> Project:
    """프로젝트 수정. 조직 관리자만, 자기 조직 프로젝트만 가능하다."""
    if user.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="소속 조직이 없는 계정이다")

    project = _get_project_or_404(db, project_id, user.org_id)
    assert_org_access(user, project.org_id)

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(project, field, value)

    record_audit(
        db,
        action="project.update",
        org_id=project.org_id,
        user_id=user.id,
        target=str(project.id),
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(project)
    return project
