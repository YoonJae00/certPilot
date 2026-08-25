"""모의심사 파이프라인 테스트 (PRD §7 F3).

AC: 데모 시드로 101항목 판정, 판정 분포에 `unknown` 존재, 재실행 시 판정 일치,
근거 참조 검증 실패 시 재시도 후 폐기.

인증기준은 `data/criteria/criteria.json` 에서 시드한다(지어내지 않는다).
"""

import json
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.llm.assess_prompt import CriterionPrompt, build_assess_prompt
from app.llm.provider import FakeProvider, LLMResult, estimate_cost_usd
from app.models import (
    Assessment,
    AssessmentStatus,
    AuditLog,
    DecidedBy,
    Evidence,
    EvidenceStatus,
    Finding,
    FindingStatus,
)
from app.services.criteria_loader import count_criteria, seed_criteria
from app.services.rules import evaluate_rules, load_rule_results
from app.workers.assess import (
    AUDIT_DONE_ACTION,
    AUDIT_FAILED_ACTION,
    MAX_ATTEMPTS,
    readiness_of,
    run_assessment,
)
from app.workers.ingest import run_ingest
from tests.conftest import login

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

# 심사 픽스처에 넣을 샘플 문서. 12개 전부 넣지 않아도 판정 분포는 충분히 갈린다.
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

# 규칙 판정을 확인할 항목. 2.5.3 은 fail 증적, 2.9.4 는 pass 증적을 붙인다.
RULE_FAIL_CODE = "2.5.3"
RULE_PASS_CODE = "2.9.4"

_HEADING_RE = re.compile(r"^##\s*항목\s+(\S+)\s+(.*)$", re.MULTILINE)


def _code_of(user_prompt: str) -> str:
    """프롬프트에서 항목 코드를 읽는다(스텁 프로바이더용)."""
    match = _HEADING_RE.search(user_prompt)
    return match.group(1) if match else "0.0.0"


class HallucinatingProvider:
    """항상 **존재하지 않는** 청크를 인용하는 스텁. 근거 검증 장치를 시험한다."""

    model = "stub-hallucinating"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str) -> LLMResult:
        self.calls += 1
        payload = {
            "criterion_code": _code_of(user),
            "status": "met",
            "confidence": 0.99,
            "rationale": "정책 문서에 다 나와 있다(chunk:c_00000000-0000-0000-0000-000000000001).",
            "evidence_chunk_ids": ["c_00000000-0000-0000-0000-000000000001"],
            "evidence_ids": [],
            "predicted_defect": None,
            "recommendation": None,
            "missing_info": [],
        }
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal("0"),
        )


class EmptyReferenceProvider:
    """근거를 하나도 인용하지 않으면서 `met` 이라고 우기는 스텁."""

    model = "stub-empty-reference"

    def complete(self, system: str, user: str) -> LLMResult:
        payload = {
            "criterion_code": _code_of(user),
            "status": "met",
            "confidence": 0.95,
            "rationale": "충족한다고 본다.",
            "evidence_chunk_ids": [],
            "evidence_ids": [],
            "predicted_defect": None,
            "recommendation": None,
            "missing_info": [],
        }
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal("0"),
        )


class ExpensiveProvider:
    """호출 1회에 1달러가 드는 스텁. 비용 상한 차단을 시험한다."""

    model = "stub-expensive"

    def complete(self, system: str, user: str) -> LLMResult:
        payload = {
            "criterion_code": _code_of(user),
            "status": "unknown",
            "confidence": 0.1,
            "rationale": "근거가 없다.",
            "evidence_chunk_ids": [],
            "evidence_ids": [],
            "predicted_defect": None,
            "recommendation": None,
            "missing_info": [],
        }
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            input_tokens=1000,
            output_tokens=500,
            cost_usd=Decimal("1.00"),
        )


@pytest.fixture(autouse=True)
def _no_celery(monkeypatch):
    """업로드가 실제 브로커를 건드리지 않게 한다."""
    monkeypatch.setattr("app.api.documents.enqueue_ingest", lambda document_id: None)


def seed_project(client, db, tenants, *, with_evidence: bool = True) -> uuid.UUID:
    """인증기준을 시드하고 A조직 프로젝트에 문서·증적을 채운다."""
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

    if with_evidence:
        db.add_all(
            [
                Evidence(
                    project_id=project_id,
                    source="aws.iam",
                    check_id="mfa_enabled",
                    criterion_codes=[RULE_FAIL_CODE],
                    status=EvidenceStatus.FAIL,
                    # 가상 계정의 가짜 숫자다. 실제 자격증명·개인정보가 아니다.
                    payload_json={"users": 7, "mfa_enabled": 3},
                ),
                Evidence(
                    project_id=project_id,
                    source="aws.cloudtrail",
                    check_id="multi_region_trail",
                    criterion_codes=[RULE_PASS_CODE],
                    status=EvidenceStatus.PASS,
                    payload_json={"trails": 1, "multi_region": True},
                ),
            ]
        )
        db.commit()
    return project_id


def create_assessment(db, project_id: uuid.UUID) -> uuid.UUID:
    """queued 상태 모의심사 1건을 만든다."""
    assessment = Assessment(project_id=project_id, status=AssessmentStatus.QUEUED)
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment.id


def findings_of(db, assessment_id: uuid.UUID) -> dict[str, Finding]:
    """판정을 항목 코드로 인덱싱해 읽는다."""
    db.expire_all()
    rows = db.execute(
        select(Finding).where(Finding.assessment_id == assessment_id)
    ).scalars()
    return {row.criterion_code: row for row in rows}


@pytest.fixture
def assessed(client, db, tenants, storage):
    """FakeProvider 로 101항목을 실제로 판정한 결과."""
    project_id = seed_project(client, db, tenants)
    assessment_id = create_assessment(db, project_id)
    result = run_assessment(assessment_id, provider=FakeProvider())
    return {"project_id": project_id, "assessment_id": assessment_id, "result": result}


def test_full_run_covers_all_criteria(db, assessed):
    """101항목 전부 판정되고 실행은 done 으로 끝난다."""
    assessment_id = assessed["assessment_id"]
    result = assessed["result"]

    assert result.status is AssessmentStatus.DONE
    assert result.finding_count == 101

    db.expire_all()
    assessment = db.execute(
        select(Assessment).where(Assessment.id == assessment_id)
    ).scalar_one()
    assert assessment.status is AssessmentStatus.DONE
    assert assessment.started_at is not None
    assert assessment.finished_at is not None
    assert assessment.model == FakeProvider.model_name
    assert assessment.cost_usd == Decimal("0.0000")

    findings = findings_of(db, assessment_id)
    assert len(findings) == 101
    assert len({row.criterion_code for row in findings.values()}) == 101


def test_distribution_contains_unknown(db, assessed):
    """근거가 없는 항목은 판단불가로 남는다(환각 방지 1차 장치)."""
    findings = findings_of(db, assessed["assessment_id"])
    unknowns = [row for row in findings.values() if row.status is FindingStatus.UNKNOWN]

    assert unknowns, "판단불가 항목이 하나도 없다 — 근거 없는 항목이 met 으로 새고 있다"
    for row in unknowns:
        assert row.evidence_chunk_ids == []
        assert row.evidence_ids == []

    # 판정이 한 값으로 쏠리지 않아야 데모에서 의미가 있다.
    statuses = {row.status for row in findings.values()}
    assert len(statuses) >= 3


def test_every_cited_reference_exists(db, assessed):
    """저장된 근거 id 는 전부 실제 청크·증적이다(근거 참조 유효율 100%)."""
    from app.models import Chunk, Document

    project_id = assessed["project_id"]
    chunk_ids = {
        str(row)
        for row in db.execute(
            select(Chunk.id).join(Document, Document.id == Chunk.document_id).where(
                Document.project_id == project_id
            )
        ).scalars()
    }
    evidence_ids = {
        str(row)
        for row in db.execute(
            select(Evidence.id).where(Evidence.project_id == project_id)
        ).scalars()
    }

    for finding in findings_of(db, assessed["assessment_id"]).values():
        assert set(finding.evidence_chunk_ids) <= chunk_ids
        assert set(finding.evidence_ids) <= evidence_ids


def test_rule_fail_overrides_llm(db, assessed):
    """규칙 fail 항목은 LLM 의견과 무관하게 unmet + decided_by=rule 이다."""
    finding = findings_of(db, assessed["assessment_id"])[RULE_FAIL_CODE]

    assert finding.status is FindingStatus.UNMET
    assert finding.decided_by is DecidedBy.RULE
    assert finding.evidence_ids, "규칙 판정 근거 증적이 붙어 있어야 한다"
    assert "aws.iam.mfa_enabled" in finding.rationale
    assert finding.predicted_defect


def test_rule_pass_item_cites_evidence(db, assessed):
    """pass 증적이 붙은 항목은 그 증적을 근거로 인용한다."""
    finding = findings_of(db, assessed["assessment_id"])[RULE_PASS_CODE]

    assert finding.evidence_ids
    assert finding.decided_by is DecidedBy.LLM
    assert finding.status is not FindingStatus.UNKNOWN


def test_summary_matches_findings(db, assessed):
    """summary_json 집계가 findings 실제 집계와 일치한다."""
    db.expire_all()
    assessment = db.execute(
        select(Assessment).where(Assessment.id == assessed["assessment_id"])
    ).scalar_one()
    summary = assessment.summary_json or {}
    findings = findings_of(db, assessed["assessment_id"])

    counts = summary["counts"]
    assert sum(counts.values()) == 101
    for status in FindingStatus:
        expected = sum(1 for row in findings.values() if row.status is status)
        assert counts[status.value] == expected

    assert summary["progress"] == {"done": 101, "total": 101}

    by_chapter = summary["by_chapter"]
    assert sorted(by_chapter) == ["1", "2", "3"]
    assert by_chapter["1"]["total"] == 16
    assert by_chapter["2"]["total"] == 64
    assert by_chapter["3"]["total"] == 21

    for bucket in by_chapter.values():
        assert bucket["readiness"] == readiness_of(
            met=bucket["met"],
            partial=bucket["partial"],
            unknown=bucket["unknown"],
            total=bucket["total"],
        )
        assert 0.0 <= bucket["readiness"] <= 1.0


def test_audit_log_records_completion(db, assessed):
    """완료 감사 로그가 남는다."""
    actions = list(
        db.execute(
            select(AuditLog.action).where(AuditLog.target == str(assessed["assessment_id"]))
        ).scalars()
    )
    assert AUDIT_DONE_ACTION in actions


def test_rerun_is_deterministic(client, db, tenants, storage):
    """같은 입력으로 다시 돌리면 판정이 100% 일치한다(FakeProvider 결정성)."""
    project_id = seed_project(client, db, tenants)

    first_id = create_assessment(db, project_id)
    run_assessment(first_id, provider=FakeProvider())
    second_id = create_assessment(db, project_id)
    run_assessment(second_id, provider=FakeProvider())

    def snapshot(assessment_id: uuid.UUID) -> dict[str, tuple]:
        return {
            code: (
                row.status,
                round(row.confidence, 4),
                row.rationale,
                tuple(row.evidence_chunk_ids),
                tuple(row.evidence_ids),
                row.decided_by,
            )
            for code, row in findings_of(db, assessment_id).items()
        }

    first = snapshot(first_id)
    second = snapshot(second_id)
    assert first.keys() == second.keys()
    assert first == second


def test_hallucinated_reference_is_retried_and_discarded(client, db, tenants, storage, caplog):
    """없는 청크를 인용하면 재시도하고, 끝내 실패하면 판단불가로 남긴다."""
    project_id = seed_project(client, db, tenants, with_evidence=False)
    assessment_id = create_assessment(db, project_id)

    provider = HallucinatingProvider()
    with caplog.at_level("WARNING"):
        result = run_assessment(assessment_id, provider=provider)

    assert result.status is AssessmentStatus.DONE
    # 항목마다 최초 1회 + 재시도 2회.
    assert provider.calls == 101 * MAX_ATTEMPTS

    findings = findings_of(db, assessment_id)
    assert len(findings) == 101
    assert all(row.status is FindingStatus.UNKNOWN for row in findings.values())
    assert all(row.evidence_chunk_ids == [] for row in findings.values())
    assert all("검증을 통과하지 못했다" in row.rationale for row in findings.values())

    assert any("판정 폐기 후 재시도" in message for message in caplog.messages)


def test_empty_references_are_forced_to_unknown(client, db, tenants, storage):
    """근거 참조가 비면 LLM 이 met 이라 해도 서버가 unknown 으로 강제한다."""
    project_id = seed_project(client, db, tenants, with_evidence=False)
    assessment_id = create_assessment(db, project_id)

    run_assessment(assessment_id, provider=EmptyReferenceProvider())

    findings = findings_of(db, assessment_id)
    assert len(findings) == 101
    assert all(row.status is FindingStatus.UNKNOWN for row in findings.values())
    assert all(row.predicted_defect is None for row in findings.values())


def test_cost_limit_aborts_run(client, db, tenants, storage):
    """비용 상한을 넘으면 중단하고 failed + 사유를 남긴다."""
    project_id = seed_project(client, db, tenants, with_evidence=False)
    assessment_id = create_assessment(db, project_id)

    result = run_assessment(assessment_id, provider=ExpensiveProvider())

    assert result.status is AssessmentStatus.FAILED
    assert result.cost_usd > Decimal("5.00")
    assert 0 < result.finding_count < 101

    db.expire_all()
    assessment = db.execute(
        select(Assessment).where(Assessment.id == assessment_id)
    ).scalar_one()
    assert assessment.status is AssessmentStatus.FAILED
    assert "비용 상한" in (assessment.summary_json or {})["reason"]

    actions = list(
        db.execute(
            select(AuditLog.action).where(AuditLog.target == str(assessment_id))
        ).scalars()
    )
    assert AUDIT_FAILED_ACTION in actions


def test_evaluate_rules_reads_mapped_evidence(client, db, tenants, storage):
    """`evaluate_rules` 는 항목에 매핑된 증적만 모은다."""
    project_id = seed_project(client, db, tenants)

    failed = evaluate_rules(db, project_id, RULE_FAIL_CODE)
    assert failed.has_fail is True
    assert failed.verdict == "fail"
    assert "종합: fail" in failed.summary_text()

    passed = evaluate_rules(db, project_id, RULE_PASS_CODE)
    assert passed.has_fail is False
    assert passed.verdict == "pass"

    empty = evaluate_rules(db, project_id, "1.1.1")
    assert empty.has_evidence is False
    assert empty.summary_text() == "없음"

    bulk = load_rule_results(db, project_id)
    assert set(bulk) == {RULE_FAIL_CODE, RULE_PASS_CODE}


def test_prompt_includes_criterion_and_evidence_blocks():
    """프롬프트에 항목 정의와 근거 블록이 PRD §8 형식으로 들어간다."""
    from app.llm.assess_prompt import ChunkRef

    criterion = CriterionPrompt(
        code="2.5.3",
        chapter=2,
        section="2.5 인증 및 권한관리",
        title="사용자 인증",
        requirement=(
            "정보시스템과 개인정보 및 중요정보에 대한 접근은 안전한 인증절차를 거쳐야 한다."
        ),
        checkpoints=["사용자 인증 절차가 수립돼 있는가"],
        defect_examples=["관리자 계정에 다중인증이 적용돼 있지 않음"],
    )
    chunk_id = uuid.uuid4()
    prompt = build_assess_prompt(
        criterion,
        rule_text="없음",
        chunks=[ChunkRef(id=chunk_id, filename="정책.pdf", page=7, text="관리자는 MFA 를 쓴다.")],
        evidences=[],
    )

    assert "ISMS-P 인증심사원" in prompt.system
    assert "unknown" in prompt.system
    assert "## 항목 2.5.3 사용자 인증" in prompt.user
    assert "안전한 인증절차" in prompt.user
    assert f"[chunk:c_{chunk_id} | 정책.pdf p.7]" in prompt.user
    assert "## 규칙 판정 결과" in prompt.user


def test_prompt_without_evidence_says_so():
    """근거가 없으면 `근거 없음` 이라고 명시한다."""
    criterion = CriterionPrompt(
        code="1.1.1",
        chapter=1,
        section="1.1 관리체계 기반 마련",
        title="경영진의 참여",
        requirement="최고경영자는 정보보호 및 개인정보보호 관리체계 수립에 참여해야 한다.",
    )
    prompt = build_assess_prompt(criterion, rule_text="없음", chunks=[], evidences=[])
    assert "근거 없음" in prompt.user


def test_fake_provider_is_deterministic():
    """같은 프롬프트에는 같은 응답이 나온다."""
    provider = FakeProvider()
    system = "시스템"
    user = "## 항목 2.5.3 사용자 인증\n\n## 규칙 판정 결과\n\n없음\n\n## 근거\n\n근거 없음"
    first = provider.complete(system, user)
    second = provider.complete(system, user)
    assert first.text == second.text
    assert json.loads(first.text)["status"] == "unknown"
    assert first.cost_usd == Decimal("0")


def test_cost_estimation_uses_price_table():
    """비용은 모델별 단가표로 계산한다."""
    # claude-sonnet-5: 입력 $2 / 출력 $10 per MTok (2026-08 기준).
    assert estimate_cost_usd("claude-sonnet-5", 1_000_000, 0) == Decimal("2.000000")
    assert estimate_cost_usd("claude-sonnet-5", 0, 100_000) == Decimal("1.000000")
    # 표에 없는 모델은 보수적으로 비싼 쪽으로 잡는다.
    assert estimate_cost_usd("unknown-model", 1_000_000, 0) == Decimal("5.000000")
