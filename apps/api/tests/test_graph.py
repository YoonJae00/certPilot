"""지식 그래프 API 테스트 (PRD §7 F3·F5·F8).

확인하는 것:

- 계층(장 → 절 → 항목)은 `parent_id` 로만 표현되고 `contains` 류 엣지가 없다.
- 판정은 최신 완료 모의심사 것만 접어 넣는다(진행 중 실행은 무시).
- 청크는 노드가 아니라 `cites_document` 엣지로 접힌다.
- 증적은 점검 단위 노드이고 최신 스냅샷 1행만 노드가 된다.
- 판정 JSONB 에 다른 조직의 청크·증적 id 가 들어 있어도 엣지가 생기지 않는다.

픽스처의 파일명·메시지·수치는 전부 가짜다. 실제 개인정보·자격증명을 넣지 않는다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.connectors.mapping import get_mapping
from app.models import (
    Alert,
    AlertType,
    Assessment,
    AssessmentStatus,
    Chunk,
    Criterion,
    DecidedBy,
    Document,
    DocumentStatus,
    Evidence,
    EvidenceStatus,
    Finding,
    FindingStatus,
)
from app.services.criteria_loader import seed_criteria
from tests.conftest import login

# 데모 시드와 무관한, 이 파일 전용 가짜 값.
CHECK_ID = "aws.iam.user_mfa"
OTHER_CHECK_ID = "aws.s3.public_block"
CODE = "2.5.3"

# criteria.json 의 고정 개수(tests/test_kb.py 가 별도로 강제한다).
CHAPTER_COUNT = 3
SECTION_COUNT = 21
CRITERION_COUNT = 101

NOW = datetime.now(UTC)


@pytest.fixture
def graph_base(client, db, tenants) -> dict:
    """인증기준을 적재하고 A조직 관리자로 로그인한 상태."""
    seed_criteria(db)
    db.commit()
    login(client, "admin-a@example.com")
    return {
        "project_a": tenants["project_a"],
        "project_b": tenants["project_b"],
        "tenants": tenants,
    }


def _fetch(client, project_id) -> dict:
    """그래프를 읽어 온다. 200 이 아니면 본문을 그대로 보여 준다."""
    response = client.get(f"/projects/{project_id}/graph")
    assert response.status_code == 200, response.text
    return response.json()


def _nodes_of(payload: dict, node_type: str) -> list[dict]:
    """종류별 노드 목록(응답 순서 유지)."""
    return [node for node in payload["nodes"] if node["type"] == node_type]


def _edges_of(payload: dict, edge_type: str) -> list[dict]:
    """종류별 엣지 목록(응답 순서 유지)."""
    return [edge for edge in payload["edges"] if edge["type"] == edge_type]


def _node_by_id(payload: dict, node_id: str) -> dict:
    """id 로 노드 1개를 찾는다."""
    for node in payload["nodes"]:
        if node["id"] == node_id:
            return node
    raise AssertionError(f"노드가 없다: {node_id}")


def _make_document(db, project_id, filename: str) -> Document:
    """가상 문서 1건."""
    document = Document(
        project_id=project_id,
        filename=filename,
        s3_key=f"projects/{project_id}/{filename}",
        mime="application/pdf",
        status=DocumentStatus.PARSED,
        page_count=3,
        sha256="0" * 64,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _make_chunks(db, document_id, count: int) -> list[Chunk]:
    """문서 청크 여러 개(임베딩 없음 — 그래프는 벡터를 쓰지 않는다)."""
    chunks = [
        Chunk(document_id=document_id, seq=index, text=f"가상 청크 본문 {index}", page=1)
        for index in range(count)
    ]
    db.add_all(chunks)
    db.commit()
    for chunk in chunks:
        db.refresh(chunk)
    return chunks


def _make_assessment(
    db, project_id, *, status=AssessmentStatus.DONE, finished_at=None
) -> Assessment:
    """모의심사 1회."""
    assessment = Assessment(
        project_id=project_id,
        status=status,
        started_at=NOW - timedelta(minutes=30),
        finished_at=finished_at,
        model="stub-model",
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


def _make_finding(
    db,
    assessment_id,
    code: str,
    *,
    status=FindingStatus.UNMET,
    chunk_ids=None,
    evidence_ids=None,
    confidence: float = 0.72,
    decided_by=DecidedBy.LLM,
) -> Finding:
    """항목 1개의 판정."""
    finding = Finding(
        assessment_id=assessment_id,
        criterion_code=code,
        status=status,
        confidence=confidence,
        rationale=f"{code} 판정 근거(픽스처).",
        evidence_chunk_ids=list(chunk_ids or []),
        evidence_ids=list(evidence_ids or []),
        decided_by=decided_by,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding


def _make_evidence(
    db,
    project_id,
    *,
    check_id: str = CHECK_ID,
    source: str = "aws.iam",
    criterion_codes=None,
    collected_at=None,
    status=EvidenceStatus.FAIL,
) -> Evidence:
    """수집된 증적 1행(스냅샷 1벌)."""
    evidence = Evidence(
        project_id=project_id,
        source=source,
        check_id=check_id,
        criterion_codes=list(criterion_codes or [CODE]),
        status=status,
        payload_json={"console_users": 7, "mfa_enabled": 3},
        collected_at=collected_at or NOW,
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


def _make_alert(db, project_id, *, message: str, evidence_id=None, created_at=None) -> Alert:
    """대시보드 알림 1건."""
    alert = Alert(
        project_id=project_id,
        type=AlertType.DRIFT,
        message=message,
        evidence_id=evidence_id,
        created_at=created_at or NOW,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def test_graph_hierarchy(client, db, graph_base):
    """장·절·항목이 parent_id 로만 이어지고 contains 류 엣지가 없다."""
    payload = _fetch(client, graph_base["project_a"].id)

    assert len(_nodes_of(payload, "chapter")) == CHAPTER_COUNT
    assert len(_nodes_of(payload, "section")) == SECTION_COUNT
    assert len(_nodes_of(payload, "criterion")) == CRITERION_COUNT

    criterion = _node_by_id(payload, f"cri:{CODE}")
    assert criterion["parent_id"] == "sec:2.5"
    assert criterion["code"] == CODE

    section = _node_by_id(payload, "sec:2.5")
    assert section["parent_id"] == "ch:2"

    chapter = _node_by_id(payload, "ch:2")
    assert chapter["parent_id"] is None
    assert chapter["label"] == "2장"
    assert chapter["chapter"] == 2

    # 라벨·개수는 지어내지 않고 criteria 테이블에서 나온다.
    rows = list(
        db.execute(select(Criterion).where(Criterion.code.like("2.5.%"))).scalars()
    )
    assert section["label"] == rows[0].section
    assert section["criteria_count"] == len(rows)
    assert criterion["label"] == {row.code: row.title for row in rows}[CODE]

    chapter_two_total = int(
        db.execute(
            select(func.count()).select_from(Criterion).where(Criterion.chapter == 2)
        ).scalar_one()
    )
    assert chapter["criteria_count"] == chapter_two_total

    # 계층은 엣지로 표현하지 않는다.
    known_edge_types = {"cites_document", "cites_evidence", "maps_to", "triggered"}
    assert {edge["type"] for edge in payload["edges"]} <= known_edge_types


def test_graph_folds_latest_finding(client, db, graph_base):
    """항목 노드의 판정은 최신 완료 심사의 것이고, 진행 중 심사는 무시된다."""
    project = graph_base["project_a"]

    older = _make_assessment(db, project.id, finished_at=NOW - timedelta(hours=2))
    _make_finding(db, older.id, CODE, status=FindingStatus.MET, confidence=0.9)

    newer = _make_assessment(db, project.id, finished_at=NOW - timedelta(minutes=10))
    latest_finding = _make_finding(
        db,
        newer.id,
        CODE,
        status=FindingStatus.UNMET,
        confidence=0.61,
        decided_by=DecidedBy.RULE,
    )

    # 아직 끝나지 않은 실행. 가장 최근이지만 판정에 반영되면 안 된다.
    running = _make_assessment(
        db, project.id, status=AssessmentStatus.RUNNING, finished_at=None
    )
    _make_finding(db, running.id, CODE, status=FindingStatus.MET, confidence=0.99)

    payload = _fetch(client, project.id)

    assert payload["assessment_id"] == str(newer.id)
    finding = _node_by_id(payload, f"cri:{CODE}")["finding"]
    assert finding["finding_id"] == str(latest_finding.id)
    assert finding["status"] == "unmet"
    assert finding["confidence"] == pytest.approx(0.61)
    assert finding["decided_by"] == "rule"


def test_graph_cites_document_edge_folds_chunks(client, db, graph_base):
    """같은 문서의 청크 3개를 인용하면 엣지 1개로 접히고 청크 노드는 없다."""
    project = graph_base["project_a"]
    document = _make_document(db, project.id, "정보보호정책.pdf")
    chunks = _make_chunks(db, document.id, 3)

    assessment = _make_assessment(db, project.id, finished_at=NOW)
    _make_finding(db, assessment.id, CODE, chunk_ids=[str(chunk.id) for chunk in chunks])

    payload = _fetch(client, project.id)

    edges = _edges_of(payload, "cites_document")
    assert len(edges) == 1
    edge = edges[0]
    assert edge["id"] == f"cd:{CODE}:{document.id}"
    assert edge["source"] == f"cri:{CODE}"
    assert edge["target"] == f"doc:{document.id}"
    assert edge["chunk_count"] == 3
    assert sorted(edge["chunk_ids"]) == sorted(str(chunk.id) for chunk in chunks)

    # 청크는 노드로 내려오지 않는다.
    assert {node["type"] for node in payload["nodes"]}.isdisjoint({"chunk"})
    assert len(_nodes_of(payload, "document")) == 1


def test_graph_evidence_latest_snapshot_only(client, db, graph_base):
    """스냅샷이 2벌이어도 점검 노드는 1개(최신 행)이고 maps_to 는 최신 행 기준이다."""
    project = graph_base["project_a"]
    _make_evidence(
        db,
        project.id,
        collected_at=NOW - timedelta(days=2),
        criterion_codes=[CODE],
        status=EvidenceStatus.PASS,
    )
    newest = _make_evidence(
        db,
        project.id,
        collected_at=NOW - timedelta(hours=1),
        # 없는 코드는 매핑되지 않아야 한다(criteria 테이블 실존 코드만).
        criterion_codes=[CODE, "2.5.4", "9.9.9"],
        status=EvidenceStatus.FAIL,
    )

    payload = _fetch(client, project.id)

    nodes = _nodes_of(payload, "evidence")
    assert len(nodes) == 1
    node = nodes[0]
    assert node["id"] == f"ev:{CHECK_ID}"
    assert node["evidence_id"] == str(newest.id)
    assert node["check_id"] == CHECK_ID
    assert node["source"] == "aws.iam"
    assert node["status"] == "fail"
    # 라벨은 aws_rules.yaml 의 표시명. 코드에서 지어내지 않는다.
    mapping = get_mapping(CHECK_ID)
    assert mapping is not None
    assert node["label"] == mapping.title

    edge_ids = {edge["id"] for edge in _edges_of(payload, "maps_to")}
    assert edge_ids == {f"mt:{CHECK_ID}:{CODE}", f"mt:{CHECK_ID}:2.5.4"}


def test_graph_cites_evidence_resolves_old_snapshot(client, db, graph_base):
    """구 스냅샷 행을 인용해도 같은 점검 노드로 귀결되고 원본 행 id 가 남는다."""
    project = graph_base["project_a"]
    old_row = _make_evidence(db, project.id, collected_at=NOW - timedelta(days=3))
    _make_evidence(db, project.id, collected_at=NOW)

    assessment = _make_assessment(db, project.id, finished_at=NOW)
    _make_finding(db, assessment.id, CODE, evidence_ids=[str(old_row.id)])

    payload = _fetch(client, project.id)

    assert len(_nodes_of(payload, "evidence")) == 1
    edges = _edges_of(payload, "cites_evidence")
    assert len(edges) == 1
    edge = edges[0]
    assert edge["id"] == f"ce:{CODE}:{CHECK_ID}"
    assert edge["source"] == f"cri:{CODE}"
    assert edge["target"] == f"ev:{CHECK_ID}"
    assert edge["evidence_ids"] == [str(old_row.id)]


def test_graph_alert_edges(client, db, graph_base):
    """증적이 붙은 알림만 triggered 엣지를 만든다. 나머지는 노드만 남는다."""
    project = graph_base["project_a"]
    evidence = _make_evidence(db, project.id)
    linked = _make_alert(
        db,
        project.id,
        message="[IAM 사용자 MFA] 판정이 충족 → 미충족으로 바뀌었다",
        evidence_id=evidence.id,
        created_at=NOW - timedelta(hours=1),
    )
    orphan = _make_alert(
        db, project.id, message="사후심사가 45일 남았다", created_at=NOW - timedelta(hours=3)
    )

    payload = _fetch(client, project.id)

    alert_ids = [node["id"] for node in _nodes_of(payload, "alert")]
    # 최신순 정렬.
    assert alert_ids == [f"al:{linked.id}", f"al:{orphan.id}"]
    assert _node_by_id(payload, f"al:{linked.id}")["read"] is False
    assert _node_by_id(payload, f"al:{linked.id}")["alert_type"] == "drift"

    edges = _edges_of(payload, "triggered")
    assert len(edges) == 1
    assert edges[0]["id"] == f"tr:{linked.id}"
    assert edges[0]["source"] == f"al:{linked.id}"
    assert edges[0]["target"] == f"ev:{CHECK_ID}"


def test_graph_skeleton_without_assessment(client, db, graph_base):
    """모의심사 전에도 200 이다. 판정만 비고 골격은 다 나온다."""
    project = graph_base["project_a"]
    _make_document(db, project.id, "자산목록.xlsx")
    _make_evidence(db, project.id)
    _make_alert(db, project.id, message="가상 알림 메시지")

    payload = _fetch(client, project.id)

    assert payload["assessment_id"] is None
    assert all(node["finding"] is None for node in _nodes_of(payload, "criterion"))
    assert _edges_of(payload, "cites_document") == []
    assert _edges_of(payload, "cites_evidence") == []

    assert len(_nodes_of(payload, "chapter")) == CHAPTER_COUNT
    assert len(_nodes_of(payload, "document")) == 1
    assert len(_nodes_of(payload, "evidence")) == 1
    assert len(_nodes_of(payload, "alert")) == 1
    assert len(_edges_of(payload, "maps_to")) == 1


def test_graph_empty_project(client, graph_base):
    """인증기준만 있는 빈 프로젝트는 계층 125노드 · 엣지 0 이다."""
    payload = _fetch(client, graph_base["project_a"].id)

    assert len(payload["nodes"]) == CHAPTER_COUNT + SECTION_COUNT + CRITERION_COUNT
    assert _nodes_of(payload, "document") == []
    assert _nodes_of(payload, "evidence") == []
    assert _nodes_of(payload, "alert") == []
    assert payload["edges"] == []


def test_graph_cross_tenant_404(client, db, graph_base):
    """다른 조직 프로젝트는 404 이고, 응답에 그 조직 데이터가 새지 않는다."""
    project = graph_base["project_a"]
    secret_filename = "A조직-내부-대외비.pdf"
    secret_message = "A조직 전용 변경 감지 알림"
    _make_document(db, project.id, secret_filename)
    _make_alert(db, project.id, message=secret_message)

    login(client, "admin-b@example.com")
    response = client.get(f"/projects/{project.id}/graph")

    assert response.status_code == 404, response.text
    assert secret_filename not in response.text
    assert secret_message not in response.text


def test_graph_ignores_foreign_chunk_and_evidence_refs(client, db, graph_base):
    """판정 JSONB 에 다른 조직 청크·증적 id 가 있어도 엣지가 생기지 않는다."""
    project_a = graph_base["project_a"]
    project_b = graph_base["project_b"]

    own_document = _make_document(db, project_a.id, "A조직-정책.pdf")
    own_chunk = _make_chunks(db, own_document.id, 1)[0]
    own_evidence = _make_evidence(db, project_a.id)

    foreign_document = _make_document(db, project_b.id, "B조직-정책.pdf")
    foreign_chunk = _make_chunks(db, foreign_document.id, 1)[0]
    foreign_evidence = _make_evidence(db, project_b.id, check_id=OTHER_CHECK_ID, source="aws.s3")

    assessment = _make_assessment(db, project_a.id, finished_at=NOW)
    _make_finding(
        db,
        assessment.id,
        CODE,
        chunk_ids=[str(own_chunk.id), str(foreign_chunk.id), "형식이-아닌-값"],
        evidence_ids=[str(own_evidence.id), str(foreign_evidence.id), str(uuid.uuid4())],
    )

    payload = _fetch(client, project_a.id)

    document_edges = _edges_of(payload, "cites_document")
    assert [edge["target"] for edge in document_edges] == [f"doc:{own_document.id}"]
    assert document_edges[0]["chunk_count"] == 1
    assert document_edges[0]["chunk_ids"] == [str(own_chunk.id)]

    evidence_edges = _edges_of(payload, "cites_evidence")
    assert [edge["target"] for edge in evidence_edges] == [f"ev:{CHECK_ID}"]
    assert evidence_edges[0]["evidence_ids"] == [str(own_evidence.id)]

    # B조직 문서·증적은 노드로도 나오지 않는다.
    assert f"doc:{foreign_document.id}" not in {node["id"] for node in payload["nodes"]}
    assert f"ev:{OTHER_CHECK_ID}" not in {node["id"] for node in payload["nodes"]}


def test_graph_reviewer_403(client, db, graph_base):
    """심사원은 조직 스코프 API 에 접근할 수 없다."""
    login(client, "reviewer@example.com")
    response = client.get(f"/projects/{graph_base['project_a'].id}/graph")
    assert response.status_code == 403, response.text


def test_graph_requires_login_401(client, graph_base):
    """미로그인 요청은 401 이다."""
    client.cookies.clear()
    response = client.get(f"/projects/{graph_base['project_a'].id}/graph")
    assert response.status_code == 401, response.text
