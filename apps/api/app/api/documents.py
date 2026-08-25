"""문서 업로드·조회와 청크 검색 라우터.

모든 엔드포인트는 `load_scoped_project` 로 org 스코프를 먼저 확정한다. 다른 조직의
프로젝트 ID 를 넣으면 존재 여부를 흘리지 않도록 404 다.
"""

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import CurrentUser, load_scoped_project, require_roles
from app.llm.embeddings import get_embedding_provider
from app.models import AuditLog, Chunk, Criterion, Document, DocumentStatus, User, UserRole
from app.schemas.document import ChunkSearchHit, ChunkSearchResponse, DocumentOut
from app.services.audit import record_audit
from app.services.extraction import SUPPORTED_EXTENSIONS, extension_of
from app.services.storage import StorageError, get_storage
from app.workers.ingest import INGEST_FAILED_ACTION, enqueue_ingest

router = APIRouter(prefix="/projects", tags=["documents"])

UploaderUser = Annotated[User, Depends(require_roles(UserRole.ORG_ADMIN, UserRole.ORG_MEMBER))]

# PRD §7 F2: 20MB 초과 업로드는 거부한다.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# 업로드 스트림을 읽는 단위. 20MB 를 넘으면 전부 읽기 전에 끊는다.
_READ_CHUNK_BYTES = 1024 * 1024

# 검색 결과 미리보기 길이.
SNIPPET_LENGTH = 300
DEFAULT_TOP_K = 5
MAX_TOP_K = 50

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")


def _read_upload(file: UploadFile) -> bytes:
    """업로드 본문을 읽는다. 20MB 를 넘으면 413."""
    buffer = bytearray()
    while True:
        block = file.file.read(_READ_CHUNK_BYTES)
        if not block:
            break
        buffer.extend(block)
        if len(buffer) > MAX_UPLOAD_BYTES:
            limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"파일이 너무 크다. 최대 {limit_mb}MB 까지 올릴 수 있다",
            )
    if not buffer:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="빈 파일은 올릴 수 없다")
    return bytes(buffer)


def _failure_reasons(db: Session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """문서별 최신 인제스트 실패 사유를 읽는다(감사 로그에 기록돼 있다)."""
    if not document_ids:
        return {}
    targets = [str(document_id) for document_id in document_ids]
    rows = db.execute(
        select(AuditLog.target, AuditLog.meta_json)
        .where(AuditLog.action == INGEST_FAILED_ACTION, AuditLog.target.in_(targets))
        .order_by(desc(AuditLog.created_at))
    ).all()

    reasons: dict[uuid.UUID, str] = {}
    for target, meta in rows:
        key = uuid.UUID(target)
        # 최신 것부터 오므로 이미 채워진 문서는 건너뛴다.
        if key in reasons:
            continue
        reason = (meta or {}).get("reason")
        if isinstance(reason, str):
            reasons[key] = reason
    return reasons


def _to_out(document: Document, reason: str | None) -> DocumentOut:
    """ORM 문서를 응답 모델로 옮긴다."""
    out = DocumentOut.model_validate(document)
    if document.status is DocumentStatus.FAILED:
        out.failure_reason = reason
    return out


@router.post(
    "/{project_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    project_id: uuid.UUID,
    user: UploaderUser,
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(description="pdf/docx/xlsx/md, 최대 20MB")],
) -> DocumentOut:
    """문서를 업로드한다. 원문은 S3 에만 저장하고 인제스트 잡을 큐에 넣는다."""
    project = load_scoped_project(db, user, project_id)

    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="파일명이 없다")

    extension = extension_of(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"지원하지 않는 파일 형식이다. 허용: {allowed}",
        )

    data = _read_upload(file)
    sha256 = hashlib.sha256(data).hexdigest()

    duplicate = db.execute(
        select(Document.id).where(
            Document.project_id == project.id, Document.sha256 == sha256
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="같은 내용의 문서가 이미 업로드돼 있다"
        )

    document_id = uuid.uuid4()
    s3_key = f"orgs/{project.org_id}/projects/{project.id}/documents/{document_id}/{filename}"
    mime = SUPPORTED_EXTENSIONS[extension]

    try:
        get_storage().put_object(s3_key, data, mime)
    except StorageError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 저장소에 접근할 수 없다"
        ) from error

    document = Document(
        id=document_id,
        project_id=project.id,
        filename=filename,
        s3_key=s3_key,
        mime=mime,
        status=DocumentStatus.UPLOADED,
        sha256=sha256,
    )
    db.add(document)
    record_audit(
        db,
        action="document.upload",
        org_id=project.org_id,
        user_id=user.id,
        target=str(document_id),
        # 파일 내용이나 개인정보는 남기지 않는다.
        meta={"filename": filename, "bytes": len(data)},
    )
    db.commit()
    db.refresh(document)

    enqueue_ingest(document_id)
    return _to_out(document, None)


@router.get("/{project_id}/documents", response_model=list[DocumentOut])
def list_documents(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[DocumentOut]:
    """프로젝트의 문서 목록."""
    project = load_scoped_project(db, user, project_id)
    documents = list(
        db.execute(
            select(Document)
            .where(Document.project_id == project.id)
            .order_by(desc(Document.created_at))
        ).scalars()
    )
    reasons = _failure_reasons(db, [document.id for document in documents])
    return [_to_out(document, reasons.get(document.id)) for document in documents]


@router.get("/{project_id}/documents/{document_id}", response_model=DocumentOut)
def get_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> DocumentOut:
    """문서 1건."""
    project = load_scoped_project(db, user, project_id)
    document = db.execute(
        select(Document).where(Document.id == document_id, Document.project_id == project.id)
    ).scalar_one_or_none()
    if document is None:
        raise _NOT_FOUND
    reasons = _failure_reasons(db, [document.id])
    return _to_out(document, reasons.get(document.id))


def build_criterion_query(criterion: Criterion) -> str:
    """항목을 검색 쿼리 텍스트로 만든다(요구사항 + 주요 확인사항)."""
    parts = [criterion.title, criterion.requirement]
    parts.extend(str(checkpoint) for checkpoint in criterion.checkpoints)
    return "\n".join(part for part in parts if part)


@router.get("/{project_id}/chunks/search", response_model=ChunkSearchResponse)
def search_chunks(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    criterion: Annotated[str | None, Query(description="인증기준 코드(예: 2.5.3)")] = None,
    q: Annotated[str | None, Query(description="자유 텍스트 질의")] = None,
    k: Annotated[int, Query(ge=1, le=MAX_TOP_K, description="결과 개수")] = DEFAULT_TOP_K,
) -> ChunkSearchResponse:
    """프로젝트 문서 청크를 의미 검색한다. `criterion` 또는 `q` 중 하나는 필수다."""
    project = load_scoped_project(db, user, project_id)

    if not criterion and not q:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="criterion 또는 q 중 하나는 있어야 한다"
        )

    criterion_row: Criterion | None = None
    if criterion:
        criterion_row = db.execute(
            select(Criterion).where(Criterion.code == criterion)
        ).scalar_one_or_none()
        if criterion_row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"인증기준 항목을 찾을 수 없다: {criterion}"
            )
        query_text = build_criterion_query(criterion_row)
    else:
        query_text = q or ""

    vector = get_embedding_provider().embed([query_text])[0]
    distance = Chunk.embedding.cosine_distance(vector).label("distance")

    rows = db.execute(
        select(Chunk.id, Chunk.document_id, Chunk.page, Chunk.text, Document.filename, distance)
        .join(Document, Document.id == Chunk.document_id)
        # 프로젝트(=조직) 밖의 청크는 절대 나오지 않는다.
        .where(Document.project_id == project.id)
        .order_by(distance)
        .limit(k)
    ).all()

    results = [
        ChunkSearchHit(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            page=row.page,
            snippet=row.text[:SNIPPET_LENGTH],
            # 코사인 거리(0~2)를 유사도로 뒤집는다. 1.0 이 완전 일치다.
            score=round(1.0 - float(row.distance), 6),
        )
        for row in rows
    ]

    return ChunkSearchResponse(
        criterion=criterion_row.code if criterion_row else None,
        criterion_title=criterion_row.title if criterion_row else None,
        query=query_text,
        results=results,
    )
