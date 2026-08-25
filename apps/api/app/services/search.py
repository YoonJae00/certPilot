"""청크 의미 검색.

청크 검색 API(`GET /projects/{id}/chunks/search`)와 모의심사 파이프라인이 같은
구현을 쓴다. 검색은 **항상 project_id 로 스코프**된다(CLAUDE.md 절대 규칙 5).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.embeddings import get_embedding_provider
from app.models import Chunk, Criterion, Document


@dataclass(frozen=True)
class ChunkHit:
    """청크 검색 결과 1건. 본문 전체를 담는다(잘라내기는 호출자 몫)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None
    text: str
    score: float


def build_criterion_query(criterion: Criterion) -> str:
    """항목을 검색 쿼리 텍스트로 만든다(제목 + 인증기준 + 주요 확인사항)."""
    parts = [criterion.title, criterion.requirement]
    parts.extend(str(checkpoint) for checkpoint in criterion.checkpoints)
    return "\n".join(part for part in parts if part)


def search_project_chunks(
    db: Session,
    project_id: uuid.UUID,
    query_text: str,
    *,
    k: int,
    min_score: float | None = None,
) -> list[ChunkHit]:
    """프로젝트 문서 청크를 코사인 유사도로 검색한다.

    `min_score` 를 주면 그 미만의 청크는 버린다. 벡터 검색은 관련이 없어도 항상
    k 건을 돌려주므로, 근거로 쓰려면 하한선이 필요하다. 하한선이 없으면 모든
    항목이 '근거 있음'이 되어 판단불가(unknown)가 사라진다.
    """
    vector = get_embedding_provider().embed([query_text])[0]
    distance = Chunk.embedding.cosine_distance(vector).label("distance")

    rows = db.execute(
        select(Chunk.id, Chunk.document_id, Chunk.page, Chunk.text, Document.filename, distance)
        .join(Document, Document.id == Chunk.document_id)
        # 프로젝트(=조직) 밖의 청크는 절대 나오지 않는다.
        .where(Document.project_id == project_id)
        .order_by(distance, Chunk.id)
        .limit(k)
    ).all()

    hits = [
        ChunkHit(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            page=row.page,
            text=row.text,
            # 코사인 거리(0~2)를 유사도로 뒤집는다. 1.0 이 완전 일치다.
            score=round(1.0 - float(row.distance), 6),
        )
        for row in rows
    ]
    if min_score is None:
        return hits
    return [hit for hit in hits if hit.score >= min_score]
