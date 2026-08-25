"""문서·청크 검색 스키마."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import DocumentStatus


class DocumentOut(BaseModel):
    """업로드 문서 응답. `s3_key` 는 내부 경로라 노출하지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    mime: str
    status: DocumentStatus
    page_count: int | None
    sha256: str
    created_at: datetime
    # status=failed 일 때만 채워진다. 감사 로그에서 읽어 온다.
    failure_reason: str | None = None


class ChunkSearchHit(BaseModel):
    """청크 검색 결과 1건."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None
    # 미리보기. 전체 본문이 필요하면 청크 단건 조회를 쓴다.
    snippet: str
    score: float


class ChunkSearchResponse(BaseModel):
    """청크 검색 응답."""

    # 항목 코드로 검색한 경우에만 채워진다.
    criterion: str | None = None
    criterion_title: str | None = None
    # 실제로 임베딩에 넣은 쿼리 텍스트(디버깅·재현용).
    query: str
    results: list[ChunkSearchHit] = Field(default_factory=list)
