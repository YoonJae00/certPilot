"""검수 워크플로 스키마 (PRD §7 F6).

심사원은 조직 스코프 API 를 쓸 수 없다(`resolve_org_scope` 가 403). 그래서 검수 화면이
필요로 하는 조직명·프로젝트명은 전부 이 응답에 담아 내린다.
"""

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import DraftKind, DraftStatus, ReviewTaskStatus


class ReviewDraftSummary(BaseModel):
    """검수 대상 초안 요약."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    org_id: uuid.UUID
    org_name: str
    kind: DraftKind
    version: int
    status: DraftStatus
    created_at: datetime
    # `[확인 필요]` 개수 등 초안 통계. content_json.stats 를 그대로 올린다.
    stats: dict[str, Any] = Field(default_factory=dict)


class ReviewTaskOut(BaseModel):
    """검수 과제 요약(큐 항목)."""

    id: uuid.UUID
    status: ReviewTaskStatus
    # NULL 이면 아직 아무도 잡지 않은 과제다.
    reviewer_id: uuid.UUID | None = None
    comment: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    # 내가 잡은 과제인지. 미배정 과제는 열람 시 나에게 배정된다.
    assigned_to_me: bool = False
    draft: ReviewDraftSummary


class ReviewTaskDetailOut(ReviewTaskOut):
    """검수 과제 상세. 편집 대상 본문을 통째로 포함한다."""

    content_json: dict[str, Any] = Field(default_factory=dict)


class SowRowFields(BaseModel):
    """운영명세서 행에서 심사원이 고칠 수 있는 칸.

    항목 코드·항목명·관련 문서는 지식베이스와 판정에서 온 값이라 편집 대상이 아니다.
    """

    operation_status: str | None = None
    owner_dept: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        """빈 편집은 받지 않는다."""
        if self.operation_status is None and self.owner_dept is None and self.note is None:
            raise ValueError("수정할 칸을 하나 이상 지정해야 한다")
        return self


class SowRowEdit(BaseModel):
    """운영명세서 행 1개 수정."""

    row_index: int = Field(ge=0)
    fields: SowRowFields


class PolicySectionEdit(BaseModel):
    """정책 초안 조항 1개 수정. 본문만 고친다(조항 제목은 템플릿 값이다)."""

    section_index: int = Field(ge=0)
    body: str


class ReviewContentPatch(BaseModel):
    """초안 편집 요청. 초안 종류에 맞는 배열만 채운다."""

    rows: list[SowRowEdit] = Field(default_factory=list)
    sections: list[PolicySectionEdit] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        """아무것도 고치지 않는 요청은 거절한다."""
        if not self.rows and not self.sections:
            raise ValueError("수정할 내용이 없다")
        return self


class ReviewApproval(BaseModel):
    """승인 요청. 코멘트는 선택이다."""

    comment: str | None = None


class ReviewReturn(BaseModel):
    """반려 요청. 코멘트는 필수이며 빈 문자열은 API 계층에서 400 으로 막는다."""

    comment: str | None = None
