"""문서 코파일럿 라우터 (PRD §7 F4).

운영명세서·정책 초안을 만들고 조회하고, **승인된 초안만** DOCX 로 내려준다.

초안 생성은 요청 안에서 동기로 끝낸다. 101행 조립은 판정을 다시 읽어 문장을 붙이는
작업이라 수 초면 끝나고, 큐를 태우면 상태 폴링만 늘어난다(모의심사와 달리 LLM 을
항목마다 부르지 않는다).

생성 결과는 언제나 `in_review` 다. `approved` 로 바꾸는 길은 검수 API 뿐이고,
그 전에는 다운로드가 403 이다(CLAUDE.md: LLM 은 초안만, 최종본은 심사원 승인).
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import CurrentUser, load_scoped_project, require_roles
from app.models import Draft, DraftKind, DraftStatus, Project, User, UserRole
from app.schemas.draft import DraftCreate, DraftDetailOut, DraftOut
from app.services.audit import record_audit
from app.services.draft_common import DraftSourceError
from app.services.draft_docx import (
    DOCX_MEDIA_TYPE,
    DraftRenderError,
    draft_docx_key,
    draft_filename,
    load_draft_docx,
    render_draft_docx,
    store_draft_docx,
)
from app.services.draft_policy import build_policy_content
from app.services.draft_sow import build_sow_content
from app.services.storage import StorageError

router = APIRouter(prefix="/projects/{project_id}/drafts", tags=["drafts"])

DraftAuthor = Annotated[User, Depends(require_roles(UserRole.ORG_ADMIN))]

AUDIT_CREATE_ACTION = "draft.create"
AUDIT_DOWNLOAD_ACTION = "draft.download"

NOT_APPROVED_MESSAGE = "심사원 승인 후 다운로드할 수 있습니다"

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")


def _to_out(draft: Draft) -> DraftOut:
    """초안 요약 응답으로 옮긴다."""
    content = draft.content_json or {}
    stats = content.get("stats")
    return DraftOut(
        id=draft.id,
        project_id=draft.project_id,
        kind=draft.kind,
        version=draft.version,
        status=draft.status,
        created_by=draft.created_by,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        downloadable=draft.status is DraftStatus.APPROVED,
        stats=stats if isinstance(stats, dict) else {},
    )


def _get_draft(db: Session, project_id: uuid.UUID, draft_id: uuid.UUID) -> Draft:
    """프로젝트 스코프 안에서만 초안을 읽는다."""
    draft = db.execute(
        select(Draft).where(Draft.id == draft_id, Draft.project_id == project_id)
    ).scalar_one_or_none()
    if draft is None:
        raise _NOT_FOUND
    return draft


def _next_version(db: Session, project_id: uuid.UUID, kind: DraftKind) -> int:
    """같은 종류 초안의 최대 버전 + 1."""
    current = db.execute(
        select(func.max(Draft.version)).where(
            Draft.project_id == project_id, Draft.kind == kind
        )
    ).scalar_one_or_none()
    return int(current or 0) + 1


def _build_content(db: Session, project: Project, kind: DraftKind) -> dict[str, object]:
    """초안 종류에 맞는 `content_json` 을 만든다. 재료가 없으면 400."""
    try:
        if kind is DraftKind.SOW:
            return build_sow_content(db, project)
        return build_policy_content(project)
    except DraftSourceError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.post("", response_model=DraftDetailOut, status_code=status.HTTP_201_CREATED)
def create_draft(
    project_id: uuid.UUID,
    payload: DraftCreate,
    user: DraftAuthor,
    db: Annotated[Session, Depends(get_db)],
) -> DraftDetailOut:
    """초안을 생성한다(동기). 결과는 언제나 `in_review` 상태다."""
    project = load_scoped_project(db, user, project_id)

    content = _build_content(db, project, payload.kind)
    version = _next_version(db, project.id, payload.kind)
    draft_id = uuid.uuid4()

    try:
        docx_bytes = render_draft_docx(payload.kind, project, content, version)
    except DraftRenderError as error:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="초안을 문서로 변환하지 못했다"
        ) from error

    s3_key = draft_docx_key(
        org_id=project.org_id,
        project_id=project.id,
        draft_id=draft_id,
        kind=payload.kind,
        version=version,
    )
    try:
        store_draft_docx(s3_key, docx_bytes)
    except StorageError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 저장소에 접근할 수 없다"
        ) from error

    draft = Draft(
        id=draft_id,
        project_id=project.id,
        kind=payload.kind,
        version=version,
        # 생성 직후 상태는 고정이다. 클라이언트가 정할 수 없다.
        status=DraftStatus.IN_REVIEW,
        content_json=content,
        docx_s3_key=s3_key,
        created_by=user.id,
    )
    db.add(draft)
    stats = content.get("stats") if isinstance(content.get("stats"), dict) else {}
    record_audit(
        db,
        action=AUDIT_CREATE_ACTION,
        org_id=project.org_id,
        user_id=user.id,
        target=str(draft_id),
        # 문서 본문은 남기지 않는다. 통계만 남긴다.
        meta={
            "project_id": str(project.id),
            "kind": payload.kind.value,
            "version": version,
            "stats": stats,
        },
    )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        # (project_id, kind, version) 유니크 충돌 = 같은 초안을 동시에 만들었다.
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="같은 초안이 방금 생성됐다. 다시 시도한다"
        ) from error
    db.refresh(draft)

    return DraftDetailOut(**_to_out(draft).model_dump(), content_json=draft.content_json or {})


@router.get("", response_model=list[DraftOut])
def list_drafts(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[DraftOut]:
    """초안 목록(최신순)."""
    project = load_scoped_project(db, user, project_id)
    rows = list(
        db.execute(
            select(Draft)
            .where(Draft.project_id == project.id)
            .order_by(desc(Draft.created_at), desc(Draft.version))
        ).scalars()
    )
    return [_to_out(row) for row in rows]


@router.get("/{draft_id}", response_model=DraftDetailOut)
def get_draft(
    project_id: uuid.UUID,
    draft_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> DraftDetailOut:
    """초안 상세. 본문(`content_json`)을 함께 준다."""
    project = load_scoped_project(db, user, project_id)
    draft = _get_draft(db, project.id, draft_id)
    return DraftDetailOut(**_to_out(draft).model_dump(), content_json=draft.content_json or {})


@router.get("/{draft_id}/download")
def download_draft(
    project_id: uuid.UUID,
    draft_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    """승인된 초안만 DOCX 로 내려준다. 승인 전에는 403 이다."""
    project = load_scoped_project(db, user, project_id)
    draft = _get_draft(db, project.id, draft_id)

    # 승인 전 다운로드 차단은 시제품의 핵심 요구사항이다. 우회로를 만들지 않는다.
    if draft.status is not DraftStatus.APPROVED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=NOT_APPROVED_MESSAGE)
    if not draft.docx_s3_key:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="초안 문서 파일이 아직 만들어지지 않았다"
        )

    try:
        payload = load_draft_docx(draft.docx_s3_key)
    except StorageError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 저장소에 접근할 수 없다"
        ) from error

    record_audit(
        db,
        action=AUDIT_DOWNLOAD_ACTION,
        org_id=project.org_id,
        user_id=user.id,
        target=str(draft.id),
        meta={"kind": draft.kind.value, "version": draft.version, "bytes": len(payload)},
    )
    db.commit()

    filename = draft_filename(draft.kind, draft.version)

    def stream() -> Iterator[bytes]:
        yield payload

    return StreamingResponse(
        stream(),
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
