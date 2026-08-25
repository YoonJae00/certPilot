"""검수 워크플로 API (PRD §7 F6).

심사원(reviewer)은 조직에 속하지 않는다. 조직 스코프 API 는 전부 403 이고, 여기 있는
`review_tasks` 경유 경로만이 고객 데이터에 닿는 합법적인 통로다(`app/core/rbac.py`).

- 초안 생성 시 **미배정** 과제가 큐에 올라간다(`app/api/drafts.py`).
- 심사원이 과제를 열면 그 자리에서 자기에게 배정된다(claim). 남이 잡은 과제는 403.
- 승인해야만 `drafts.status=approved` 가 되고 고객 다운로드가 열린다. 이 게이트를
  우회하는 경로는 만들지 않는다(데모 기준 D5).

운영자(operator)는 전 조직 열람 권한이 있으므로 큐와 상세를 볼 수 있지만, 편집·승인·
반려는 할 수 없다. 초안을 확정하는 사람은 심사원뿐이다.
"""

import copy
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import require_roles
from app.models import (
    AlertType,
    DraftKind,
    DraftStatus,
    ReviewTaskStatus,
    User,
    UserRole,
)
from app.schemas.review import (
    ReviewApproval,
    ReviewContentPatch,
    ReviewDraftSummary,
    ReviewReturn,
    ReviewTaskDetailOut,
    ReviewTaskOut,
)
from app.services.audit import record_audit
from app.services.draft_docx import DraftRenderError
from app.services.review import (
    AUDIT_APPROVE_ACTION,
    AUDIT_EDIT_ACTION,
    AUDIT_RETURN_ACTION,
    ReviewContentError,
    ReviewRow,
    add_alert,
    apply_row_edits,
    apply_section_edits,
    approval_message,
    claim,
    is_owner,
    list_queue,
    load_row,
    now_utc,
    recompute_stats,
    regenerate_docx,
    return_message,
)
from app.services.storage import StorageError

router = APIRouter(prefix="/reviews", tags=["reviews"])

# 검수 큐는 심사원 전용이다. 운영자는 전 조직 열람 권한으로 읽기만 한다.
ReviewViewer = Annotated[User, Depends(require_roles(UserRole.REVIEWER, UserRole.OPERATOR))]
DbSession = Annotated[Session, Depends(get_db)]

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")
_NOT_MINE = HTTPException(status.HTTP_403_FORBIDDEN, detail="다른 심사원에게 배정된 과제다")
_DECIDED = HTTPException(status.HTTP_409_CONFLICT, detail="이미 결정된 검수 과제다")
_REVIEWER_ONLY = HTTPException(
    status.HTTP_403_FORBIDDEN, detail="검수 결정은 심사원만 할 수 있다"
)

RETURN_COMMENT_REQUIRED = "반려 사유를 입력해야 합니다"


def _draft_summary(row: ReviewRow) -> ReviewDraftSummary:
    """초안 요약. 심사원이 조직 API 를 못 쓰므로 조직·프로젝트 이름을 함께 담는다."""
    content = row.draft.content_json or {}
    stats = content.get("stats")
    return ReviewDraftSummary(
        id=row.draft.id,
        project_id=row.project.id,
        project_name=row.project.name,
        org_id=row.organization.id,
        org_name=row.organization.name,
        kind=row.draft.kind,
        version=row.draft.version,
        status=row.draft.status,
        created_at=row.draft.created_at,
        stats=stats if isinstance(stats, dict) else {},
    )


def _to_out(row: ReviewRow, user: User) -> ReviewTaskOut:
    """검수 과제 요약 응답."""
    task = row.task
    return ReviewTaskOut(
        id=task.id,
        status=task.status,
        reviewer_id=task.reviewer_id,
        comment=task.comment,
        decided_at=task.decided_at,
        created_at=task.created_at,
        assigned_to_me=task.reviewer_id is not None and task.reviewer_id == user.id,
        draft=_draft_summary(row),
    )


def _to_detail(row: ReviewRow, user: User) -> ReviewTaskDetailOut:
    """검수 과제 상세 응답. 편집 대상 본문을 통째로 싣는다."""
    return ReviewTaskDetailOut(
        **_to_out(row, user).model_dump(),
        content_json=row.draft.content_json or {},
    )


def _load(db: Session, task_id: uuid.UUID, user: User) -> ReviewRow:
    """과제를 읽고 접근 권한을 확인한다.

    심사원: 미배정 대기 과제면 이 자리에서 배정(claim)하고, 남이 잡은 과제는 403.
    운영자: 배정과 무관하게 열람만 한다.
    """
    row = load_row(db, task_id)
    if row is None:
        raise _NOT_FOUND

    if user.role is UserRole.REVIEWER:
        if row.task.reviewer_id is None:
            # 결정이 끝난 과제는 미배정으로 남지 않으므로 배정은 여기서만 일어난다.
            if row.task.status is not ReviewTaskStatus.PENDING:
                raise _NOT_MINE
            claim(row.task, user)
            db.commit()
            db.refresh(row.task)
        elif row.task.reviewer_id != user.id:
            raise _NOT_MINE
    return row


def _require_decidable(row: ReviewRow, user: User) -> None:
    """편집·결정 전 공통 확인. 심사원 본인의 대기 중 과제만 통과한다."""
    if user.role is not UserRole.REVIEWER:
        raise _REVIEWER_ONLY
    if not is_owner(row.task, user):
        raise _NOT_MINE
    if row.task.status is not ReviewTaskStatus.PENDING:
        raise _DECIDED


@router.get("", response_model=list[ReviewTaskOut])
@router.get("/queue", response_model=list[ReviewTaskOut])
def list_review_queue(user: ReviewViewer, db: DbSession) -> list[ReviewTaskOut]:
    """검수 큐: 미배정 + 내게 배정된 대기 과제, 그리고 내가 처리한 이력(최신순)."""
    return [_to_out(row, user) for row in list_queue(db, user)]


@router.get("/{task_id}", response_model=ReviewTaskDetailOut)
def get_review_task(
    task_id: uuid.UUID, user: ReviewViewer, db: DbSession
) -> ReviewTaskDetailOut:
    """검수 과제 상세. 미배정 과제를 열면 그 순간 나에게 배정된다."""
    return _to_detail(_load(db, task_id, user), user)


@router.patch("/{task_id}/content", response_model=ReviewTaskDetailOut)
def edit_review_content(
    task_id: uuid.UUID,
    payload: ReviewContentPatch,
    user: ReviewViewer,
    db: DbSession,
) -> ReviewTaskDetailOut:
    """초안 본문을 고치고 DOCX 를 다시 만든다.

    운영명세서는 행 단위(운영 현황·담당 부서·비고), 정책은 조항 본문 단위로 고친다.
    편집 결과를 곧바로 같은 S3 키에 반영해, 승인 후 내려받는 파일과 화면이 어긋나지
    않게 한다.
    """
    row = _load(db, task_id, user)
    _require_decidable(row, user)

    draft = row.draft
    # JSONB 컬럼은 제자리 수정을 감지하지 못한다. 사본을 고쳐 통째로 대입한다.
    content: dict[str, Any] = copy.deepcopy(dict(draft.content_json or {}))

    try:
        if draft.kind is DraftKind.SOW:
            if payload.sections:
                raise ReviewContentError("운영명세서에는 조항 편집을 적용할 수 없다")
            touched = apply_row_edits(content, payload.rows)
        else:
            if payload.rows:
                raise ReviewContentError("정책 초안에는 행 편집을 적용할 수 없다")
            touched = apply_section_edits(content, payload.sections)
        stats = recompute_stats(draft.kind, content)
    except ReviewContentError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    try:
        s3_key = regenerate_docx(row.project, draft, content)
    except DraftRenderError as error:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="초안을 문서로 변환하지 못했다"
        ) from error
    except StorageError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 저장소에 접근할 수 없다"
        ) from error

    draft.content_json = content
    draft.docx_s3_key = s3_key

    record_audit(
        db,
        action=AUDIT_EDIT_ACTION,
        org_id=row.organization.id,
        user_id=user.id,
        target=str(task_id),
        # 문서 본문은 감사 로그에 남기지 않는다. 어디를 고쳤는지만 남긴다.
        meta={
            "draft_id": str(draft.id),
            "kind": draft.kind.value,
            "version": draft.version,
            "rows": touched if draft.kind is DraftKind.SOW else [],
            "sections": [] if draft.kind is DraftKind.SOW else touched,
            "needs_review": stats.get("needs_review"),
        },
    )
    db.commit()
    db.refresh(draft)
    return _to_detail(row, user)


@router.post("/{task_id}/approve", response_model=ReviewTaskDetailOut)
def approve_review_task(
    task_id: uuid.UUID,
    payload: ReviewApproval,
    user: ReviewViewer,
    db: DbSession,
) -> ReviewTaskDetailOut:
    """초안을 승인한다. 여기서만 `drafts.status=approved` 가 되고 다운로드가 열린다."""
    row = _load(db, task_id, user)
    _require_decidable(row, user)

    task, draft = row.task, row.draft
    task.status = ReviewTaskStatus.APPROVED
    task.decided_at = now_utc()
    comment = (payload.comment or "").strip()
    task.comment = comment or None
    draft.status = DraftStatus.APPROVED

    record_audit(
        db,
        action=AUDIT_APPROVE_ACTION,
        org_id=row.organization.id,
        user_id=user.id,
        target=str(task.id),
        meta={
            "draft_id": str(draft.id),
            "project_id": str(draft.project_id),
            "kind": draft.kind.value,
            "version": draft.version,
        },
    )
    # 조직 담당자에게 "이제 내려받을 수 있다"를 알린다.
    add_alert(db, draft.project_id, AlertType.DUE, approval_message(draft.kind))
    db.commit()
    db.refresh(task)
    db.refresh(draft)
    return _to_detail(row, user)


@router.post("/{task_id}/return", response_model=ReviewTaskDetailOut)
def return_review_task(
    task_id: uuid.UUID,
    payload: ReviewReturn,
    user: ReviewViewer,
    db: DbSession,
) -> ReviewTaskDetailOut:
    """초안을 반려한다. 코멘트가 없으면 400 이다(고칠 것을 적지 않고 되돌리지 않는다)."""
    row = _load(db, task_id, user)
    _require_decidable(row, user)

    comment = (payload.comment or "").strip()
    if not comment:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=RETURN_COMMENT_REQUIRED)

    task, draft = row.task, row.draft
    task.status = ReviewTaskStatus.RETURNED
    task.decided_at = now_utc()
    task.comment = comment
    # 반려된 초안은 재생성(새 버전)으로만 다시 검수에 오른다.
    draft.status = DraftStatus.RETURNED

    record_audit(
        db,
        action=AUDIT_RETURN_ACTION,
        org_id=row.organization.id,
        user_id=user.id,
        target=str(task.id),
        meta={
            "draft_id": str(draft.id),
            "project_id": str(draft.project_id),
            "kind": draft.kind.value,
            "version": draft.version,
            "comment": comment,
        },
    )
    add_alert(db, draft.project_id, AlertType.DEFECT, return_message(draft.kind, comment))
    db.commit()
    db.refresh(task)
    db.refresh(draft)
    return _to_detail(row, user)
