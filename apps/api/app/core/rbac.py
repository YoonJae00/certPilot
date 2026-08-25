"""인증·인가 의존성.

역할 규칙(PRD §3):

- `operator`  : 모든 조직 열람. 조직·사용자 생성.
- `org_admin` : 자기 조직만. 프로젝트 CRUD, 업로드, 커넥터, 모의심사, 다운로드.
- `org_member`: 자기 조직만. 업로드와 열람.
- `reviewer`  : 조직에 속하지 않는다. 시제품에서는 `review_tasks` 를 경유한
                검수 화면으로만 접근하므로, 조직 스코프 API 는 전부 막는다.

테넌트 격리는 `assert_org_access` / `resolve_org_scope` 두 함수를 반드시 통과해야
한다. 새 쿼리를 쓰면 여기를 거치고 크로스 테넌트 테스트를 함께 쓴다.
"""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import read_session_token
from app.models import Project, User, UserRole

# 리소스 존재 여부를 흘리지 않기 위해, 권한이 없는 조직 리소스는 404 로 답한다.
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """세션 쿠키에서 현재 사용자를 로드한다. 없거나 위조면 401."""
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요하다")

    invalid_session = HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었거나 유효하지 않다"
    )

    user_id = read_session_token(token)
    if user_id is None:
        raise invalid_session

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        raise invalid_session
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    """지정한 역할만 통과시키는 의존성을 만든다."""
    allowed = frozenset(roles)

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="이 작업을 수행할 권한이 없다"
            )
        return user

    return dependency


def assert_org_access(user: User, org_id: uuid.UUID) -> None:
    """조직 스코프 리소스에 접근할 수 있는지 확인한다.

    operator 는 전체 허용, reviewer 는 전면 차단, 나머지는 소속 조직만 허용한다.
    """
    if user.role is UserRole.OPERATOR:
        return
    if user.role is UserRole.REVIEWER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="심사원은 검수 과제를 통해서만 접근할 수 있다",
        )
    if user.org_id is None or user.org_id != org_id:
        raise _NOT_FOUND


def resolve_org_scope(user: User, requested_org_id: uuid.UUID | None) -> uuid.UUID:
    """목록 API 에서 쓸 org_id 를 확정한다.

    operator 는 `org_id` 를 명시해야 한다(전체 조회를 실수로 여는 것을 막는다).
    조직 사용자는 자기 조직만 볼 수 있고, 다른 조직을 요청하면 404 다.
    """
    if user.role is UserRole.REVIEWER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="심사원은 검수 과제를 통해서만 접근할 수 있다",
        )
    if user.role is UserRole.OPERATOR:
        if requested_org_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="운영자는 org_id 를 지정해야 한다"
            )
        return requested_org_id
    if user.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="소속 조직이 없는 계정이다")
    if requested_org_id is not None and requested_org_id != user.org_id:
        raise _NOT_FOUND
    return user.org_id


def load_scoped_project(db: Session, user: User, project_id: uuid.UUID) -> Project:
    """현재 사용자가 접근 가능한 프로젝트만 읽는다. 아니면 404.

    프로젝트 하위 리소스(문서·청크·모의심사 등) 라우터는 전부 이 함수를 먼저 거쳐
    org 스코프를 확정한다. 다른 조직의 프로젝트는 존재 여부를 흘리지 않도록 404 다.
    """
    if user.role is UserRole.OPERATOR:
        project = db.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        if project is None:
            raise _NOT_FOUND
        return project

    org_id = resolve_org_scope(user, None)
    project = db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == org_id)
    ).scalar_one_or_none()
    if project is None:
        raise _NOT_FOUND
    return project
