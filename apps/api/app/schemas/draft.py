"""산출물 초안 스키마 (PRD §7 F4)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import DraftKind, DraftStatus


class DraftCreate(BaseModel):
    """초안 생성 요청. 버전·상태·작성자는 서버가 정한다."""

    kind: DraftKind


class DraftOut(BaseModel):
    """초안 요약 응답. 본문(`content_json`)은 상세 조회에서만 내린다."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    kind: DraftKind
    version: int
    status: DraftStatus
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    # 심사원 승인 전에는 False 다(PRD §7 F4, 데모 기준 D5).
    downloadable: bool = False
    # `[확인 필요]` 개수 등 초안 통계. content_json.stats 를 그대로 올린다.
    stats: dict[str, Any] = Field(default_factory=dict)


class DraftDetailOut(DraftOut):
    """초안 상세. 검수 화면이 편집할 본문을 포함한다."""

    content_json: dict[str, Any] = Field(default_factory=dict)
