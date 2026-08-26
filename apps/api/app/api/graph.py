"""지식 그래프 API (PRD §7 F3·F5·F8).

프로젝트 하나를 인증기준 계층·판정·근거 문서·클라우드 증적·알림의 그래프로 본다.
읽기 전용이며, 조립 로직은 전부 `app/services/graph.py` 에 있다.

`load_scoped_project` 로 org 스코프를 먼저 확정한다. 다른 조직의 프로젝트 ID 를
넣으면 404, 심사원(reviewer)은 403 이다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import CurrentUser, load_scoped_project
from app.schemas.graph import GraphOut
from app.services.graph import build_graph

router = APIRouter(prefix="/projects/{project_id}", tags=["graph"])


@router.get("/graph", response_model=GraphOut)
def get_graph(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> GraphOut:
    """프로젝트의 지식 그래프를 돌려준다.

    모의심사를 아직 돌리지 않았어도 200 이다. 이때는 판정 없이 계층·문서·증적·알림
    골격만 나간다.
    """
    project = load_scoped_project(db, user, project_id)
    return build_graph(db, project)
