"""텍스트 청킹.

PRD §7 F2: 약 500토큰 청크, 100토큰 오버랩.

**토큰 수는 근사값이다.** 실제 BPE 토크나이저를 쓰지 않고 어절(공백·구두점으로
분리한 단어) 수를 토큰 수로 본다. 한국어는 어절 하나가 보통 2~4 BPE 토큰이므로
실제 토큰 수는 여기 값보다 크다. 프롬프트 예산을 계산할 때는 이 값을 그대로 믿지 말고
여유를 둔다. 정확한 토크나이저로 바꾸려면 `count_tokens` 만 교체하면 된다.
"""

from dataclasses import dataclass

from app.llm.embeddings import tokenize
from app.services.extraction import ExtractedDocument

# 청크 크기·오버랩(근사 토큰 = 어절 수).
# PRD의 "500토큰"은 LLM(BPE) 토큰 기준이고 한국어 어절은 보통 2~3 BPE 토큰이므로,
# 어절 200개(≈ BPE 400~600토큰)로 잡아야 스펙 의도와 맞는다.
CHUNK_SIZE_TOKENS = 200
CHUNK_OVERLAP_TOKENS = 40


@dataclass(frozen=True)
class TextChunk:
    """청크 1개. `page` 는 청크가 시작된 페이지다."""

    seq: int
    text: str
    page: int | None
    token_count: int


def count_tokens(text: str) -> int:
    """근사 토큰 수(어절 수)."""
    return len(tokenize(text))


@dataclass(frozen=True)
class _Word:
    """페이지 정보를 달고 다니는 낱말."""

    text: str
    page: int


def _flatten(document: ExtractedDocument) -> list[_Word]:
    """페이지별 텍스트를 페이지 표시가 붙은 낱말 열로 편다."""
    words: list[_Word] = []
    for page in document.pages:
        for word in page.text.split():
            words.append(_Word(text=word, page=page.page))
    return words


def chunk_document(
    document: ExtractedDocument,
    *,
    chunk_size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> list[TextChunk]:
    """문서를 오버랩이 있는 청크 목록으로 나눈다.

    페이지 경계를 넘어서 이어 붙이되, 각 청크의 `page` 에는 시작 낱말의 페이지를
    기록한다. 근거 하이라이트에서 "몇 쪽을 보라"고 안내하기 위한 값이다.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 는 1 이상이어야 한다")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap 은 0 이상, chunk_size 미만이어야 한다")

    words = _flatten(document)
    if not words:
        return []

    step = chunk_size - overlap
    chunks: list[TextChunk] = []
    start = 0
    seq = 0
    while start < len(words):
        window = words[start : start + chunk_size]
        text = " ".join(word.text for word in window)
        chunks.append(
            TextChunk(seq=seq, text=text, page=window[0].page, token_count=len(window))
        )
        seq += 1
        if start + chunk_size >= len(words):
            break
        start += step
    return chunks
