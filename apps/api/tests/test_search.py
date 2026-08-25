"""청크 검색 API 테스트 (PRD §7 F2 AC: 항목 코드로 top-5 검색).

인증기준은 `data/criteria/criteria.json` 에서 시드한다(지어내지 않는다).
"""

import uuid
from pathlib import Path

import pytest

from app.services.criteria_loader import count_criteria, seed_criteria
from app.workers.ingest import run_ingest
from tests.conftest import login

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

# 검색 픽스처에 넣을 샘플. 12개 전부 넣을 필요는 없어 대표 문서만 인제스트한다.
FIXTURE_SAMPLES = [
    "01_정보보호정책_v2.1.pdf",
    "07_접근권한검토이력.xlsx",
    "10_백업정책_복구테스트결과.md",
    "12_침해사고대응절차서.md",
]

MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
}


@pytest.fixture(autouse=True)
def _no_celery(monkeypatch):
    """업로드가 실제 브로커를 건드리지 않게 한다."""
    monkeypatch.setattr("app.api.documents.enqueue_ingest", lambda document_id: None)


@pytest.fixture
def indexed_project(client, db, tenants, storage):
    """인증기준을 시드하고 A조직 프로젝트에 샘플 문서를 인제스트한다."""
    seed_criteria(db)
    db.commit()
    assert count_criteria(db) == 101

    login(client, "admin-a@example.com")
    project_id = tenants["project_a"].id
    for name in FIXTURE_SAMPLES:
        path = SAMPLES_DIR / name
        mime = MIME_BY_EXTENSION[path.suffix.lstrip(".")]
        response = client.post(
            f"/projects/{project_id}/documents",
            files={"file": (path.name, path.read_bytes(), mime)},
        )
        assert response.status_code == 201, response.text
        run_ingest(uuid.UUID(response.json()["id"]), db=db)
    return project_id


def test_search_by_criterion_returns_top_k(client, indexed_project):
    """항목 코드로 검색하면 top-5 청크를 돌려준다."""
    response = client.get(
        f"/projects/{indexed_project}/chunks/search", params={"criterion": "2.5.3", "k": 5}
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["criterion"] == "2.5.3"
    assert payload["criterion_title"] == "사용자 인증"
    # 쿼리 텍스트는 항목 본문 + 주요 확인사항으로 만든다.
    assert len(payload["query"]) > 50

    results = payload["results"]
    assert 0 < len(results) <= 5
    for hit in results:
        assert uuid.UUID(hit["chunk_id"])
        assert uuid.UUID(hit["document_id"])
        assert hit["filename"] in FIXTURE_SAMPLES
        assert 0 < len(hit["snippet"]) <= 300
        assert -1.0 <= hit["score"] <= 1.0

    # 유사도 내림차순으로 정렬돼 있어야 한다.
    scores = [hit["score"] for hit in results]
    assert scores == sorted(scores, reverse=True)


def test_search_respects_k(client, indexed_project):
    """k 를 줄이면 결과 개수도 줄어든다."""
    response = client.get(
        f"/projects/{indexed_project}/chunks/search", params={"criterion": "2.5.3", "k": 2}
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) <= 2


def test_search_by_free_text(client, indexed_project):
    """`q=` 자유 텍스트 검색도 동작한다."""
    response = client.get(
        f"/projects/{indexed_project}/chunks/search",
        params={"q": "백업 복구 테스트 수행 기록", "k": 3},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["criterion"] is None
    assert payload["query"] == "백업 복구 테스트 수행 기록"
    assert payload["results"]
    # 어휘가 겹치는 백업 정책 문서가 결과에 있어야 한다.
    assert any(
        hit["filename"] == "10_백업정책_복구테스트결과.md" for hit in payload["results"]
    )


def test_search_requires_criterion_or_query(client, indexed_project):
    """criterion 도 q 도 없으면 400."""
    response = client.get(f"/projects/{indexed_project}/chunks/search")
    assert response.status_code == 400


def test_search_unknown_criterion_is_404(client, indexed_project):
    """지식베이스에 없는 항목 코드는 404 (항목을 지어내지 않는다)."""
    response = client.get(
        f"/projects/{indexed_project}/chunks/search", params={"criterion": "9.9.9"}
    )
    assert response.status_code == 404


def test_search_other_org_project_is_404(client, tenants, indexed_project):
    """다른 조직 프로젝트의 청크는 검색할 수 없다(크로스 테넌트)."""
    login(client, "admin-b@example.com")
    response = client.get(
        f"/projects/{indexed_project}/chunks/search", params={"criterion": "2.5.3"}
    )
    assert response.status_code == 404

    # 반대 방향도 막힌다.
    login(client, "admin-a@example.com")
    response = client.get(
        f"/projects/{tenants['project_b'].id}/chunks/search", params={"criterion": "2.5.3"}
    )
    assert response.status_code == 404


def test_search_only_returns_own_project_chunks(client, db, tenants, indexed_project, storage):
    """B조직 프로젝트에 같은 문서를 넣어도 A조직 검색 결과에 섞이지 않는다."""
    login(client, "admin-b@example.com")
    project_b = tenants["project_b"].id
    path = SAMPLES_DIR / "10_백업정책_복구테스트결과.md"
    response = client.post(
        f"/projects/{project_b}/documents",
        files={"file": (path.name, path.read_bytes(), "text/markdown")},
    )
    assert response.status_code == 201
    document_b = uuid.UUID(response.json()["id"])
    run_ingest(document_b, db=db)

    login(client, "admin-a@example.com")
    payload = client.get(
        f"/projects/{indexed_project}/chunks/search", params={"q": "백업 복구", "k": 20}
    ).json()
    assert payload["results"]
    assert all(hit["document_id"] != str(document_b) for hit in payload["results"])


def test_search_requires_login(client, indexed_project):
    """비로그인 요청은 401."""
    client.cookies.clear()
    response = client.get(
        f"/projects/{indexed_project}/chunks/search", params={"criterion": "2.5.3"}
    )
    assert response.status_code == 401


def test_seed_criteria_is_idempotent(db):
    """시드를 두 번 실행해도 101행을 유지한다."""
    assert seed_criteria(db) == 101
    db.commit()
    assert seed_criteria(db) == 101
    db.commit()
    assert count_criteria(db) == 101
