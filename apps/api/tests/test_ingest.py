"""문서 업로드·인제스트 테스트 (PRD §7 F2).

AC: 샘플 문서 12개 파싱 성공률 100%, 청크 생성, 마스킹 적용, 암호 PDF 는 failed.

S3 는 moto 로 가짜 버킷을 띄운다(conftest 의 `storage` 픽스처). 실제 MinIO 에는
아무것도 남지 않으므로 테스트를 몇 번 돌려도 결과가 같다.
"""

import io
import uuid
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.models import Chunk, Document, DocumentStatus
from app.workers.ingest import run_ingest
from tests.conftest import login

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
}


def sample_paths() -> list[Path]:
    """`data/samples/` 의 샘플 문서 12개 경로."""
    return sorted(
        path for path in SAMPLES_DIR.iterdir() if path.suffix.lstrip(".") in MIME_BY_EXTENSION
    )


@pytest.fixture(autouse=True)
def _no_celery(monkeypatch):
    """업로드가 실제 브로커에 잡을 넣지 않게 한다. 인제스트는 테스트가 직접 부른다."""
    queued: list[uuid.UUID] = []
    monkeypatch.setattr("app.api.documents.enqueue_ingest", queued.append)
    return queued


def upload(client, project_id, path: Path, *, filename: str | None = None):
    """샘플 파일 1개를 업로드한다."""
    name = filename or path.name
    mime = MIME_BY_EXTENSION[name.rsplit(".", 1)[-1]]
    return client.post(
        f"/projects/{project_id}/documents",
        files={"file": (name, path.read_bytes(), mime)},
    )


def upload_bytes(client, project_id, filename: str, data: bytes, mime: str = "application/pdf"):
    """바이트를 그대로 업로드한다."""
    return client.post(
        f"/projects/{project_id}/documents",
        files={"file": (filename, data, mime)},
    )


def test_samples_directory_has_twelve_documents():
    """부록 D 의 샘플 문서 12개가 커밋돼 있어야 한다."""
    paths = sample_paths()
    assert len(paths) == 12
    extensions = sorted(path.suffix.lstrip(".") for path in paths)
    assert extensions.count("pdf") == 3
    assert extensions.count("docx") == 3
    assert extensions.count("xlsx") == 2
    assert extensions.count("md") == 4


def test_all_samples_ingest_successfully(client, db, tenants, storage):
    """샘플 12개 전부 parsed 가 되고 청크가 1개 이상 생긴다(파싱 성공률 100%)."""
    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id

    for path in sample_paths():
        response = upload(client, project_id, path)
        assert response.status_code == 201, f"{path.name}: {response.text}"
        document_id = uuid.UUID(response.json()["id"])

        result = run_ingest(document_id, db=db)
        assert result.chunk_count > 0, f"{path.name} 에서 청크가 만들어지지 않았다"

        document = db.execute(
            select(Document).where(Document.id == document_id)
        ).scalar_one()
        assert document.status is DocumentStatus.PARSED, f"{path.name} 파싱 실패"
        assert document.page_count is not None and document.page_count >= 1

    documents = list(db.execute(select(Document)).scalars())
    assert len(documents) == 12
    assert all(document.status is DocumentStatus.PARSED for document in documents)

    chunk_count = len(list(db.execute(select(Chunk)).scalars()))
    assert chunk_count >= 12


def test_chunk_text_is_masked_and_embedded(client, db, tenants, storage):
    """청크 본문에는 마스킹된 텍스트만 저장되고 임베딩이 채워진다."""
    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id

    path = SAMPLES_DIR / "02_정보보호조직도_CISO지정공문.pdf"
    document_id = uuid.UUID(upload(client, project_id, path).json()["id"])
    run_ingest(document_id, db=db)

    chunks = list(
        db.execute(select(Chunk).where(Chunk.document_id == document_id)).scalars()
    )
    assert chunks
    body = "\n".join(chunk.text for chunk in chunks)

    # 샘플에 심어 둔 가짜 연락처가 그대로 남아 있으면 안 된다.
    assert "010-1234-5678" not in body
    assert "ciso@demofintech.example" not in body
    assert "[MASKED:phone]" in body
    assert "[MASKED:email]" in body

    for chunk in chunks:
        assert chunk.embedding is not None
        assert len(chunk.embedding) == 1536
        assert chunk.token_count and chunk.token_count > 0


def test_original_stays_in_object_storage_only(client, db, tenants, storage):
    """원문은 S3 에만 있고 DB 청크에는 마스킹본만 남는다."""
    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id

    path = SAMPLES_DIR / "02_정보보호조직도_CISO지정공문.pdf"
    document_id = uuid.UUID(upload(client, project_id, path).json()["id"])
    run_ingest(document_id, db=db)

    document = db.execute(select(Document).where(Document.id == document_id)).scalar_one()
    stored = storage.get_object(document.s3_key)
    assert stored == path.read_bytes()


def test_reingest_replaces_chunks(client, db, tenants, storage):
    """같은 문서를 다시 인제스트해도 청크가 중복되지 않는다."""
    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id

    path = SAMPLES_DIR / "10_백업정책_복구테스트결과.md"
    document_id = uuid.UUID(upload(client, project_id, path).json()["id"])

    first = run_ingest(document_id, db=db)
    second = run_ingest(document_id, db=db)
    assert first.chunk_count == second.chunk_count

    stored = len(
        list(db.execute(select(Chunk).where(Chunk.document_id == document_id)).scalars())
    )
    assert stored == second.chunk_count


def _encrypted_pdf_bytes() -> bytes:
    """암호로 보호된 PDF 픽스처를 그 자리에서 만든다(가짜 암호)."""
    source = SAMPLES_DIR / "01_정보보호정책_v2.1.pdf"
    writer = PdfWriter(clone_from=io.BytesIO(source.read_bytes()))
    writer.encrypt("fixture-only-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_encrypted_pdf_fails_with_reason(client, db, tenants, storage):
    """암호 걸린 PDF 는 status=failed 이고 사유가 남는다."""
    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id

    response = upload_bytes(client, project_id, "암호문서.pdf", _encrypted_pdf_bytes())
    assert response.status_code == 201
    document_id = uuid.UUID(response.json()["id"])

    result = run_ingest(document_id, db=db)
    assert result.chunk_count == 0

    db.expire_all()
    document = db.execute(select(Document).where(Document.id == document_id)).scalar_one()
    assert document.status is DocumentStatus.FAILED

    detail = client.get(f"/projects/{project_id}/documents/{document_id}").json()
    assert detail["status"] == "failed"
    assert "암호" in detail["failure_reason"]


def test_rejects_oversize_upload(client, tenants, storage):
    """20MB 를 넘으면 413."""
    login(client, "admin-a@example.com")
    payload = b"x" * (20 * 1024 * 1024 + 1024)
    response = upload_bytes(client, tenants["project_a"].id, "big.md", payload, "text/markdown")
    assert response.status_code == 413


def test_rejects_unsupported_extension(client, tenants, storage):
    """허용 확장자(pdf/docx/xlsx/md) 외에는 400."""
    login(client, "admin-a@example.com")
    response = upload_bytes(
        client, tenants["project_a"].id, "evil.exe", b"MZ", "application/octet-stream"
    )
    assert response.status_code == 400


def test_rejects_duplicate_sha256(client, tenants, storage):
    """같은 내용을 다시 올리면 409."""
    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id
    path = SAMPLES_DIR / "05_개인정보처리방침.md"

    assert upload(client, project_id, path).status_code == 201
    # 파일명을 바꿔도 내용이 같으면 중복이다.
    assert upload(client, project_id, path, filename="사본.md").status_code == 409


def test_member_can_upload_reviewer_cannot(client, tenants, storage):
    """org_member 는 업로드 가능, reviewer 는 403."""
    project_id = tenants["project_a"].id
    path = SAMPLES_DIR / "12_침해사고대응절차서.md"

    login(client, "member-a@example.com")
    assert upload(client, project_id, path).status_code == 201

    login(client, "reviewer@example.com")
    assert upload(client, project_id, path, filename="다른이름.md").status_code == 403


def test_upload_to_other_org_project_is_404(client, tenants, storage):
    """다른 조직 프로젝트에는 업로드할 수 없고 존재 여부도 흘리지 않는다."""
    login(client, "admin-a@example.com")
    path = SAMPLES_DIR / "12_침해사고대응절차서.md"
    response = upload(client, tenants["project_b"].id, path)
    assert response.status_code == 404


def test_list_and_detail_are_org_scoped(client, db, tenants, storage):
    """목록·상세는 자기 조직 프로젝트에서만 보인다."""
    login(client, "admin-a@example.com")
    project_a = tenants["project_a"].id
    document_id = upload(client, project_a, SAMPLES_DIR / "11_변경관리절차서.docx").json()["id"]

    listed = client.get(f"/projects/{project_a}/documents")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [document_id]
    assert "s3_key" not in listed.json()[0]

    detail = client.get(f"/projects/{project_a}/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "uploaded"

    login(client, "admin-b@example.com")
    assert client.get(f"/projects/{project_a}/documents").status_code == 404
    assert client.get(f"/projects/{project_a}/documents/{document_id}").status_code == 404


def test_upload_queues_ingest_job(client, tenants, storage, _no_celery):
    """업로드가 인제스트 잡을 큐잉한다."""
    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id
    document_id = upload(client, project_id, SAMPLES_DIR / "06_개인정보흐름도_v1.0.md").json()["id"]
    assert _no_celery == [uuid.UUID(document_id)]
