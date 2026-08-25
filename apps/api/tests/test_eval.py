"""골든셋 평가 테스트 (PRD §8, 부록 B Task 12).

두 가지를 지킨다.

1. 골든셋 파일이 형식을 지키고, 거기 적힌 항목 코드가 `criteria.json` 에 실제로
   있는지(CLAUDE.md 절대 규칙 1 — 인증기준을 지어내지 않는다).
2. 시드된 DB 에서 평가 러너가 실제로 돌고, **근거 참조 유효율이 1.0** 인지
   (환각 방지 장치의 회귀 감지선).
"""

import uuid
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Assessment, AssessmentStatus, Finding, FindingStatus
from app.services.demo_seed import DemoSeedResult, seed_demo
from app.services.evaluation import (
    DEFAULT_GOLDEN_PATH,
    DEMO_FIXTURE,
    EvaluationError,
    audit_references,
    evaluate_fixture,
    known_criterion_codes,
    latest_done_assessment,
    load_golden_set,
    render_report,
    report_path,
    write_report,
)

# PRD §8: 초기 골든셋 20개.
EXPECTED_CASES = 20

# `metrics()` 가 반드시 담아야 하는 키. 리포트·콘솔·발표 슬라이드가 이걸 읽는다.
REQUIRED_METRIC_KEYS = {
    "golden_version",
    "golden_case_count",
    "matched_case_count",
    "missing_case_count",
    "agreement",
    "unmet_precision",
    "unmet_recall",
    "unmet_f1",
    "unknown_ratio",
    "unknown_ratio_golden",
    "evidence_reference_validity",
    "evidence_reference_total",
    "finding_count",
    "cost_usd",
    "cost_per_criterion_usd",
}


@pytest.fixture(scope="module")
def golden():
    """리포에 커밋된 골든셋. 파일 파싱은 여러 테스트가 함께 쓴다."""
    return load_golden_set()


@pytest.fixture
def seeded(db: Session, storage) -> DemoSeedResult:
    """데모 시드를 한 번 적재한다(`storage` 는 moto 가짜 S3)."""
    return seed_demo(db)


def _write_golden(path: Path, cases: list[dict[str, str]]) -> Path:
    """임시 골든셋 파일을 만든다."""
    payload = {"version": "test", "note": "테스트용", "cases": cases}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def _valid_case(code: str = "2.5.3") -> dict[str, str]:
    """검증을 통과하는 최소 케이스."""
    return {
        "criterion_code": code,
        "project_fixture": DEMO_FIXTURE,
        "expected_status": "unmet",
        "expert_note": "테스트용 근거 메모",
    }


# --------------------------------------------------------------------------
# 골든셋 파일
# --------------------------------------------------------------------------


def test_golden_file_is_committed() -> None:
    """`data/eval/golden.yaml` 이 리포에 있다."""
    assert DEFAULT_GOLDEN_PATH.exists(), f"골든셋이 없다: {DEFAULT_GOLDEN_PATH}"


def test_golden_set_has_twenty_valid_cases(golden) -> None:
    """20개 케이스 · 코드는 criteria.json 에 실존 · 상태값 유효 · 중복 없음."""
    assert len(golden.cases) == EXPECTED_CASES

    codes = known_criterion_codes()
    valid_statuses = set(FindingStatus)
    seen: set[str] = set()
    for case in golden.cases:
        assert case.criterion_code in codes, f"criteria.json 에 없는 코드: {case.criterion_code}"
        assert case.expected_status in valid_statuses
        assert case.project_fixture == DEMO_FIXTURE
        assert case.expert_note.strip(), f"{case.criterion_code} 에 근거 메모가 없다"
        assert case.criterion_code not in seen, f"코드 중복: {case.criterion_code}"
        seen.add(case.criterion_code)

    assert len(golden.for_fixture(DEMO_FIXTURE)) == EXPECTED_CASES


def test_golden_set_covers_every_status(golden) -> None:
    """네 가지 판정이 모두 들어 있어야 정밀도·재현율·판단불가 지표가 의미를 가진다."""
    present = {case.expected_status for case in golden.cases}
    assert present == set(FindingStatus)


def test_golden_note_marks_expert_review_pending(golden) -> None:
    """`note` 에 심사원 검증 전이라는 사실이 남아 있어야 한다(PRD 오픈 이슈 4)."""
    assert "검증 전" in golden.note
    assert "데모 시드" in golden.note


def test_golden_set_rejects_unknown_criterion_code(tmp_path: Path) -> None:
    """criteria.json 에 없는 코드는 로드 단계에서 막는다."""
    path = _write_golden(tmp_path / "golden.yaml", [_valid_case("9.9.9")])
    with pytest.raises(EvaluationError, match="criteria.json"):
        load_golden_set(path)


def test_golden_set_rejects_invalid_status(tmp_path: Path) -> None:
    """expected_status 는 FindingStatus 값만 허용한다."""
    case = _valid_case()
    case["expected_status"] = "충족"
    path = _write_golden(tmp_path / "golden.yaml", [case])
    with pytest.raises(EvaluationError, match="expected_status"):
        load_golden_set(path)


def test_golden_set_rejects_duplicate_code(tmp_path: Path) -> None:
    """같은 항목을 두 번 채점하지 않는다."""
    path = _write_golden(tmp_path / "golden.yaml", [_valid_case(), _valid_case()])
    with pytest.raises(EvaluationError, match="중복"):
        load_golden_set(path)


def test_golden_set_rejects_missing_field(tmp_path: Path) -> None:
    """근거 메모가 빠지면 골든셋으로 인정하지 않는다."""
    case = _valid_case()
    case["expert_note"] = ""
    path = _write_golden(tmp_path / "golden.yaml", [case])
    with pytest.raises(EvaluationError, match="expert_note"):
        load_golden_set(path)


def test_load_golden_set_missing_file(tmp_path: Path) -> None:
    """파일이 없으면 친절한 오류를 낸다."""
    with pytest.raises(EvaluationError, match="골든셋 파일이 없다"):
        load_golden_set(tmp_path / "없는파일.yaml")


# --------------------------------------------------------------------------
# 평가 실행
# --------------------------------------------------------------------------


def test_evaluate_fixture_returns_metrics(db: Session, seeded: DemoSeedResult, golden) -> None:
    """시드된 DB 에서 평가가 돌고 지표 dict 구조가 맞는다. 근거 참조 유효율은 1.0."""
    result = evaluate_fixture(db, golden=golden)

    assert result.assessment_id == seeded.assessment_id
    assert result.project_id == seeded.project_id
    assert result.finding_count == seeded.finding_count == 101
    assert result.case_count == EXPECTED_CASES
    assert result.missing_codes == []

    metrics = result.metrics()
    assert set(metrics) == REQUIRED_METRIC_KEYS
    assert metrics["golden_case_count"] == EXPECTED_CASES
    assert metrics["finding_count"] == 101
    assert 0.0 <= metrics["agreement"] <= 1.0
    assert 0.0 <= metrics["unmet_precision"] <= 1.0
    assert 0.0 <= metrics["unmet_recall"] <= 1.0
    assert 0.0 <= metrics["unknown_ratio"] <= 1.0

    # PRD §8 목표 100%. 근거 검증이 뚫리면 여기서 먼저 깨진다.
    assert metrics["evidence_reference_validity"] == 1.0
    assert metrics["evidence_reference_total"] > 0
    assert result.references.findings_with_invalid_references == 0
    assert result.references.invalid_examples == []


def test_rule_decided_unmet_matches_golden(db: Session, seeded: DemoSeedResult, golden) -> None:
    """규칙이 fail 로 덮은 항목은 골든셋과 반드시 일치한다(결정적 경로)."""
    result = evaluate_fixture(db, golden=golden)
    by_code = {outcome.criterion_code: outcome for outcome in result.outcomes}

    # data/rules/aws_rules.yaml 에서 fail 증적이 매핑된 항목들.
    for code in ("2.5.1", "2.5.3", "2.5.6", "2.6.2", "2.10.2"):
        outcome = by_code[code]
        assert outcome.actual is FindingStatus.UNMET, f"{code} 가 미충족이 아니다"
        assert outcome.matched, f"{code} 가 골든셋과 어긋난다"

    assert result.unmet.true_positive >= 5


def test_unmet_scores_are_consistent(db: Session, seeded: DemoSeedResult, golden) -> None:
    """정밀도·재현율의 분모가 실제 대조 결과와 맞는다."""
    result = evaluate_fixture(db, golden=golden)
    expected_unmet = sum(
        1 for outcome in result.outcomes if outcome.expected is FindingStatus.UNMET
    )
    actual_unmet = sum(
        1 for outcome in result.outcomes if outcome.actual is FindingStatus.UNMET
    )
    assert result.unmet.true_positive + result.unmet.false_negative == expected_unmet
    assert result.unmet.true_positive + result.unmet.false_positive == actual_unmet
    assert result.matched_count == sum(1 for outcome in result.outcomes if outcome.matched)
    assert result.agreement == round(result.matched_count / result.case_count, 4)


def test_reference_audit_flags_broken_reference(db: Session, seeded: DemoSeedResult) -> None:
    """존재하지 않는 근거를 인용한 판정이 있으면 유효율이 1.0 아래로 떨어진다."""
    assessment = latest_done_assessment(db, seeded.project_id)
    assert assessment is not None

    target = db.execute(
        select(Finding).where(
            Finding.assessment_id == assessment.id,
            Finding.criterion_code == "2.5.3",
        )
    ).scalar_one()
    db.execute(
        update(Finding)
        .where(Finding.id == target.id)
        .values(evidence_chunk_ids=[str(uuid.uuid4())])
    )
    db.commit()
    db.expire_all()

    findings = list(
        db.execute(select(Finding).where(Finding.assessment_id == assessment.id)).scalars()
    )
    audit = audit_references(db, seeded.project_id, findings)
    assert audit.validity < 1.0
    assert audit.findings_with_invalid_references == 1
    assert any("2.5.3" in example for example in audit.invalid_examples)


def test_evaluate_requires_done_assessment(db: Session, seeded: DemoSeedResult, golden) -> None:
    """완료된 모의심사가 없으면 안내와 함께 멈춘다."""
    db.execute(update(Assessment).values(status=AssessmentStatus.FAILED))
    db.commit()
    with pytest.raises(EvaluationError, match="완료"):
        evaluate_fixture(db, golden=golden)


def test_evaluate_requires_demo_project(db: Session, golden) -> None:
    """데모 시드가 없으면 `make demo` 를 안내한다."""
    with pytest.raises(EvaluationError, match="make demo"):
        evaluate_fixture(db, golden=golden)


def test_unsupported_fixture_rejected(db: Session, golden) -> None:
    """모르는 픽스처 이름은 조용히 빈 결과를 내지 않고 오류를 낸다."""
    with pytest.raises(EvaluationError, match="project_fixture"):
        evaluate_fixture(db, golden=golden, fixture="staging")


# --------------------------------------------------------------------------
# 리포트
# --------------------------------------------------------------------------


def test_report_path_uses_date(tmp_path: Path) -> None:
    """리포트 파일명은 `YYYY-MM-DD.md` 다."""
    assert report_path(tmp_path, today=date(2026, 8, 26)).name == "2026-08-26.md"


def test_write_report_renders_korean_tables(
    db: Session, seeded: DemoSeedResult, golden, tmp_path: Path
) -> None:
    """리포트가 저장되고 PRD §8 지표가 한국어 표로 들어 있다."""
    result = evaluate_fixture(db, golden=golden)
    path = write_report(result, tmp_path, today=date(2026, 8, 26))

    assert path == tmp_path / "2026-08-26.md"
    text = path.read_text(encoding="utf-8")
    for heading in ("지표 요약", "판정 분포", "골든셋 대조", "근거 참조 유효성", "한계"):
        assert heading in text
    for metric in (
        "전체 일치율",
        "미충족 정밀도",
        "미충족 재현율",
        "근거 참조 유효율",
        "항목당 평균 비용",
    ):
        assert metric in text
    # 골든셋 20개 항목이 모두 표에 나온다.
    for case in golden.cases:
        assert f"| {case.criterion_code} |" in text
    # 미검증 골든셋이라는 사실을 리포트가 스스로 밝힌다.
    assert "검증 전" in text


def test_report_notes_fake_provider(db: Session, seeded: DemoSeedResult, golden) -> None:
    """Fake 프로바이더로 돈 실행은 리포트에 그 사실이 적힌다."""
    result = evaluate_fixture(db, golden=golden)
    text = render_report(result, today=date(2026, 8, 26))
    if (result.assessment_model or "").startswith("fake"):
        assert "Fake 프로바이더" in text
