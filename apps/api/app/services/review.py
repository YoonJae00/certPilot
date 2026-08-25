"""검수 워크플로 서비스 (PRD §7 F6).

검수는 조직 스코프 밖에서 도는 유일한 흐름이다. 심사원은 조직에 속하지 않으므로
(`app/core/rbac.py` 참고) `review_tasks` → `drafts` → `projects` → `organizations` 로
이어지는 조인만이 조직 데이터에 닿는 합법적인 경로다. 이 모듈이 그 조인을 한곳에
모아 두고, 라우터는 여기서 얻은 행만 다룬다.

편집은 `content_json` 을 고치고 같은 S3 키에 DOCX 를 다시 올린다. 승인된 문서와
사용자가 내려받는 파일이 어긋나지 않게, 편집과 재생성은 항상 같은 요청에서 끝낸다.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, case, desc, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Alert,
    AlertType,
    Draft,
    DraftKind,
    Organization,
    Project,
    ReviewTask,
    ReviewTaskStatus,
    User,
    UserRole,
)
from app.schemas.review import PolicySectionEdit, SowRowEdit
from app.services.draft_common import count_needs_review
from app.services.draft_docx import draft_docx_key, render_draft_docx, store_draft_docx

# 알림·감사 로그에 쓰는 초안 종류 표기.
KIND_LABELS: dict[DraftKind, str] = {
    DraftKind.SOW: "운영명세서",
    DraftKind.POLICY: "정보보호 정책",
}

AUDIT_EDIT_ACTION = "review.edit"
AUDIT_APPROVE_ACTION = "review.approve"
AUDIT_RETURN_ACTION = "review.return"


class ReviewContentError(ValueError):
    """편집 요청이 초안 구조와 맞지 않는다. API 계층이 400 으로 바꾼다."""


@dataclass(frozen=True)
class ReviewRow:
    """검수 과제 1건과 그에 딸린 조직 데이터."""

    task: ReviewTask
    draft: Draft
    project: Project
    organization: Organization


def ensure_review_task(db: Session, draft: Draft) -> ReviewTask:
    """초안에 대기 중인 검수 과제를 보장한다.

    이미 대기 중인 과제가 있으면 그것을 그대로 돌려준다(같은 초안에 과제가 둘 생기면
    두 심사원이 각자 승인·반려해 결과가 엇갈린다). 커밋은 호출자가 한다.
    """
    existing = (
        db.execute(
            select(ReviewTask).where(
                ReviewTask.draft_id == draft.id,
                ReviewTask.status == ReviewTaskStatus.PENDING,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    task = ReviewTask(draft_id=draft.id, reviewer_id=None, status=ReviewTaskStatus.PENDING)
    db.add(task)
    return task


def _base_query() -> Select[tuple[ReviewTask, Draft, Project, Organization]]:
    """검수 과제 → 초안 → 프로젝트 → 조직 조인. 심사원이 조직 데이터에 닿는 유일한 경로."""
    return (
        select(ReviewTask, Draft, Project, Organization)
        .join(Draft, Draft.id == ReviewTask.draft_id)
        .join(Project, Project.id == Draft.project_id)
        .join(Organization, Organization.id == Project.org_id)
    )


def _visible_to(
    query: Select[tuple[ReviewTask, Draft, Project, Organization]], user: User
) -> Select[tuple[ReviewTask, Draft, Project, Organization]]:
    """심사원에게는 미배정 과제와 자기 과제만 보인다. 운영자는 전체를 열람한다."""
    if user.role is UserRole.REVIEWER:
        return query.where(
            or_(ReviewTask.reviewer_id.is_(None), ReviewTask.reviewer_id == user.id)
        )
    return query


def list_queue(db: Session, user: User) -> list[ReviewRow]:
    """검수 큐. 대기 중인 과제를 먼저, 그다음 처리 이력을 최신순으로 준다."""
    query = _visible_to(_base_query(), user).order_by(
        case((ReviewTask.status == ReviewTaskStatus.PENDING, 0), else_=1),
        desc(ReviewTask.created_at),
    )
    return [
        ReviewRow(task=task, draft=draft, project=project, organization=org)
        for task, draft, project, org in db.execute(query).all()
    ]


def load_row(db: Session, task_id: uuid.UUID) -> ReviewRow | None:
    """검수 과제 1건을 조직 데이터와 함께 읽는다. 없으면 None."""
    record = db.execute(_base_query().where(ReviewTask.id == task_id)).first()
    if record is None:
        return None
    task, draft, project, org = record
    return ReviewRow(task=task, draft=draft, project=project, organization=org)


def claim(task: ReviewTask, user: User) -> bool:
    """미배정 과제를 현재 심사원에게 배정한다. 배정이 일어났으면 True."""
    if task.reviewer_id is not None or user.role is not UserRole.REVIEWER:
        return False
    task.reviewer_id = user.id
    return True


def is_owner(task: ReviewTask, user: User) -> bool:
    """이 과제를 편집·결정할 수 있는 심사원인지."""
    return user.role is UserRole.REVIEWER and task.reviewer_id == user.id


# ---------------------------------------------------------------------------
# 본문 편집
# ---------------------------------------------------------------------------


def _rows_of(content: dict[str, Any]) -> list[dict[str, Any]]:
    """운영명세서 행 배열을 꺼낸다."""
    rows = content.get("rows")
    if not isinstance(rows, list):
        raise ReviewContentError("운영명세서 초안이 아니다")
    return rows


def _sections_of(content: dict[str, Any]) -> list[dict[str, Any]]:
    """정책 조항 배열을 꺼낸다."""
    sections = content.get("sections")
    if not isinstance(sections, list):
        raise ReviewContentError("정책 초안이 아니다")
    return sections


def apply_row_edits(content: dict[str, Any], edits: list[SowRowEdit]) -> list[int]:
    """운영명세서 행을 고친다. 고친 행 번호를 돌려준다."""
    rows = _rows_of(content)
    touched: list[int] = []
    for edit in edits:
        if edit.row_index >= len(rows):
            raise ReviewContentError(f"{edit.row_index}번 행이 없다")
        row = rows[edit.row_index]
        if not isinstance(row, dict):
            raise ReviewContentError(f"{edit.row_index}번 행 형식이 어긋난다")
        # 지정한 칸만 덮어쓴다. 나머지 칸(항목 코드·근거 등)은 그대로 둔다.
        for name, value in edit.fields.model_dump(exclude_none=True).items():
            row[name] = value
        touched.append(edit.row_index)
    return touched


def apply_section_edits(content: dict[str, Any], edits: list[PolicySectionEdit]) -> list[int]:
    """정책 조항 본문을 고친다. 고친 조항 번호를 돌려준다."""
    sections = _sections_of(content)
    touched: list[int] = []
    for edit in edits:
        if edit.section_index >= len(sections):
            raise ReviewContentError(f"{edit.section_index}번 조항이 없다")
        section = sections[edit.section_index]
        if not isinstance(section, dict):
            raise ReviewContentError(f"{edit.section_index}번 조항 형식이 어긋난다")
        section["body"] = edit.body
        touched.append(edit.section_index)
    return touched


def recompute_stats(kind: DraftKind, content: dict[str, Any]) -> dict[str, Any]:
    """편집 후 `[확인 필요]` 통계를 다시 센다.

    심사원이 칸을 채우면 그 수가 줄어야 한다. 화면 상단 카운트가 이 값을 그대로 쓴다.
    """
    stats = content.get("stats")
    updated: dict[str, Any] = dict(stats) if isinstance(stats, dict) else {}

    if kind is DraftKind.SOW:
        rows = _rows_of(content)
        updated["total"] = len(rows)
        updated["needs_review"] = count_needs_review(rows)
        updated["needs_review_rows"] = sum(1 for row in rows if count_needs_review(row) > 0)
    else:
        sections = _sections_of(content)
        updated["total"] = len(sections)
        updated["needs_review"] = count_needs_review(sections)

    content["stats"] = updated
    return updated


def regenerate_docx(project: Project, draft: Draft, content: dict[str, Any]) -> str:
    """편집 결과로 DOCX 를 다시 만들어 같은 키에 올린다. 저장한 키를 돌려준다.

    키를 바꾸지 않으므로 승인 후 다운로드 경로가 그대로 유지된다.
    `DraftRenderError` / `StorageError` 는 그대로 올려보내 API 가 상태 코드를 정한다.
    """
    payload = render_draft_docx(draft.kind, project, content, draft.version)
    key = draft.docx_s3_key or draft_docx_key(
        org_id=project.org_id,
        project_id=project.id,
        draft_id=draft.id,
        kind=draft.kind,
        version=draft.version,
    )
    store_draft_docx(key, payload)
    return key


# ---------------------------------------------------------------------------
# 결정(승인·반려)과 알림
# ---------------------------------------------------------------------------


def now_utc() -> datetime:
    """결정 시각. DB 에 timezone-aware 로 저장한다."""
    return datetime.now(UTC)


def kind_label(kind: DraftKind) -> str:
    """사람이 읽는 초안 종류 이름."""
    return KIND_LABELS.get(kind, kind.value)


def approval_message(kind: DraftKind) -> str:
    """승인 알림 문구."""
    return f"{kind_label(kind)} 승인 완료 — 다운로드 가능"


def return_message(kind: DraftKind, comment: str) -> str:
    """반려 알림 문구. 심사원 코멘트를 그대로 붙인다."""
    return f"{kind_label(kind)} 반려 — {comment}"


def add_alert(db: Session, project_id: uuid.UUID, alert_type: AlertType, message: str) -> Alert:
    """조직 대시보드 알림 1건을 세션에 추가한다. 커밋은 호출자가 한다.

    `alerts.type` 은 drift/due/defect 세 가지뿐이라 검수 결과 전용 타입이 없다.
    승인은 "기한 안에 해야 할 일이 열렸다"는 뜻으로 `due`, 반려는 고쳐야 할 것이
    생겼다는 뜻으로 `defect` 를 쓴다(PRD §7 F8 알림 카드에 그대로 뜬다).
    """
    alert = Alert(project_id=project_id, type=alert_type, message=message)
    db.add(alert)
    return alert
