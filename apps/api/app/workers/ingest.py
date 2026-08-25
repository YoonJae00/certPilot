"""문서 인제스트 파이프라인.

S3 원문 → 텍스트 추출 → **마스킹** → 청킹 → 임베딩 → `chunks` 저장 → `parsed`.

마스킹은 청킹 직전에 정확히 한 번 적용한다. 그 뒤로는 DB 에 들어가는 텍스트도,
임베딩에 들어가는 텍스트도 전부 마스킹된 값이다. 원문은 S3 에만 남는다.
"""

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_session_factory
from app.llm.embeddings import get_embedding_provider
from app.models import Chunk, Document, DocumentStatus, Project
from app.services.audit import record_audit
from app.services.chunking import chunk_document
from app.services.extraction import ExtractedDocument, ExtractedPage, ExtractionError
from app.services.extraction import extract_document as extract
from app.services.masking import mask_text
from app.services.storage import StorageError, get_storage
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# 실패 사유를 남기는 감사 로그 액션. `documents` 테이블에 사유 컬럼을 새로 만들면
# 스키마 변경이라 승인이 필요하므로(CLAUDE.md 규칙 6), 사유는 감사 로그에 적고
# 문서 조회 API 가 가장 최근 것을 읽어 응답에 실어 준다.
INGEST_FAILED_ACTION = "document.ingest_failed"

# 사유 문자열 최대 길이.
MAX_REASON_LENGTH = 300


class IngestResult:
    """인제스트 결과 요약. 테스트와 로그에서 쓴다."""

    def __init__(self, *, document_id: uuid.UUID, chunk_count: int, page_count: int) -> None:
        self.document_id = document_id
        self.chunk_count = chunk_count
        self.page_count = page_count

    def __repr__(self) -> str:
        """디버깅용 표현."""
        return (
            f"IngestResult(document_id={self.document_id}, "
            f"chunk_count={self.chunk_count}, page_count={self.page_count})"
        )


def _mask_document(document: ExtractedDocument) -> ExtractedDocument:
    """추출 결과 전체에 마스킹을 적용한다. 이 뒤로는 원문이 흐르지 않는다."""
    return ExtractedDocument(
        pages=[
            ExtractedPage(page=page.page, text=mask_text(page.text)) for page in document.pages
        ]
    )


def _store_failure(db: Session, document: Document, reason: str) -> None:
    """문서를 실패 처리하고 사유를 감사 로그에 남긴다."""
    document.status = DocumentStatus.FAILED
    org_id = db.execute(
        select(Project.org_id).where(Project.id == document.project_id)
    ).scalar_one_or_none()
    record_audit(
        db,
        action=INGEST_FAILED_ACTION,
        org_id=org_id,
        target=str(document.id),
        meta={"reason": reason[:MAX_REASON_LENGTH]},
    )
    db.commit()


def run_ingest(document_id: uuid.UUID, *, db: Session | None = None) -> IngestResult:
    """문서 1건을 인제스트한다(동기).

    Celery 없이도 그대로 호출할 수 있다. 세션을 넘기지 않으면 여기서 열고 닫는다.
    """
    owns_session = db is None
    session = db or get_session_factory()()
    try:
        document = session.execute(
            select(Document).where(Document.id == document_id)
        ).scalar_one_or_none()
        if document is None:
            raise ValueError(f"문서를 찾을 수 없다: {document_id}")

        try:
            raw = get_storage().get_object(document.s3_key)
        except StorageError:
            logger.exception("인제스트 실패(스토리지): document_id=%s", document_id)
            _store_failure(session, document, "원본 파일을 읽을 수 없다")
            raise

        try:
            extracted = extract(document.filename, raw)
        except ExtractionError as error:
            logger.warning(
                "인제스트 실패(추출): document_id=%s 사유=%s", document_id, error
            )
            _store_failure(session, document, str(error))
            return IngestResult(document_id=document_id, chunk_count=0, page_count=0)

        masked = _mask_document(extracted)
        chunks = chunk_document(masked)
        if not chunks:
            _store_failure(session, document, "본문이 비어 있어 청크를 만들 수 없다")
            return IngestResult(document_id=document_id, chunk_count=0, page_count=0)

        provider = get_embedding_provider()
        vectors = provider.embed([chunk.text for chunk in chunks])

        # 재인제스트를 대비해 기존 청크를 먼저 비운다.
        session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        session.add_all(
            [
                Chunk(
                    document_id=document_id,
                    seq=chunk.seq,
                    text=chunk.text,
                    page=chunk.page,
                    embedding=vector,
                    token_count=chunk.token_count,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )

        document.status = DocumentStatus.PARSED
        document.page_count = masked.page_count
        session.commit()

        logger.info(
            "인제스트 완료: document_id=%s 청크=%d 페이지=%d",
            document_id,
            len(chunks),
            masked.page_count,
        )
        return IngestResult(
            document_id=document_id, chunk_count=len(chunks), page_count=masked.page_count
        )
    except Exception:
        # 예외를 삼키지 않는다. 상태만 기록하고 그대로 올린다.
        session.rollback()
        logger.exception("인제스트 중 처리하지 못한 예외: document_id=%s", document_id)
        raise
    finally:
        if owns_session:
            session.close()


@celery_app.task(name="certpilot.ingest_document")
def ingest_document(document_id: str) -> dict[str, int | str]:
    """Celery 태스크. 동기 함수 `run_ingest` 를 감싸기만 한다."""
    result = run_ingest(uuid.UUID(document_id))
    return {
        "document_id": str(result.document_id),
        "chunk_count": result.chunk_count,
        "page_count": result.page_count,
    }


def enqueue_ingest(document_id: uuid.UUID) -> None:
    """인제스트 잡을 큐에 넣는다. 브로커가 없으면 로그만 남기고 넘어간다.

    업로드 API 는 브로커 장애로 500 을 내지 않는다. 문서는 `uploaded` 로 남아
    나중에 재처리할 수 있다.
    """
    try:
        ingest_document.delay(str(document_id))
    except Exception:  # noqa: BLE001 - 큐잉 실패가 업로드를 막으면 안 된다
        logger.exception("인제스트 큐잉 실패: document_id=%s", document_id)
