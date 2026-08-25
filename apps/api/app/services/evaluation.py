"""골든셋 평가 (PRD §8 "평가", 부록 B Task 12).

`data/eval/golden.yaml` 의 기대 판정과 실제 모의심사 판정을 대조해 PRD §8 이 요구하는
지표를 계산하고, `docs/eval/YYYY-MM-DD.md` 리포트를 만든다.

계산하는 지표
    - unmet 판정의 정밀도·재현율·F1 (골든셋 안에서만)
    - 전체 일치율 (골든셋 케이스 중 기대와 실제가 같은 비율)
    - unknown 비율 (전체 판정 기준 / 골든셋 안 기준)
    - 근거 참조 유효율 (판정이 인용한 chunk/evidence id 가 실제 행으로 존재하는 비율,
      **목표 100%** — 환각 방지 장치가 실제로 작동하는지 보는 숫자다)
    - 항목당 평균 비용 (실행 비용 ÷ 판정 수)

여기서는 판정을 새로 만들지 않는다. 이미 저장된 `findings` 를 읽기만 한다.
평가가 판정 로직을 흉내 내기 시작하면 그 순간 지표가 자기 자신을 채점하게 된다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assessment,
    AssessmentStatus,
    Chunk,
    DecidedBy,
    Document,
    Evidence,
    Finding,
    FindingStatus,
    Project,
)
from app.services.criteria_loader import load_criteria_file
from app.services.demo_seed import DEMO_PROJECT_NAME
from app.services.scoring import code_sort_key

# apps/api/app/services/evaluation.py -> 리포 루트
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_GOLDEN_PATH = REPO_ROOT / "data" / "eval" / "golden.yaml"
DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "eval"

# 현재 지원하는 프로젝트 픽스처. `make demo` 가 만드는 데모핀테크 프로젝트를 가리킨다.
DEMO_FIXTURE = "demo"

# 골든셋 케이스 1건에 필요한 키.
_CASE_KEYS = ("criterion_code", "project_fixture", "expected_status", "expert_note")

# 판정 상태 한국어 라벨. 리포트 표에 그대로 쓴다.
STATUS_LABELS: dict[FindingStatus, str] = {
    FindingStatus.MET: "충족",
    FindingStatus.PARTIAL: "부분충족",
    FindingStatus.UNMET: "미충족",
    FindingStatus.UNKNOWN: "판단불가",
}

DECIDED_BY_LABELS: dict[DecidedBy, str] = {
    DecidedBy.RULE: "규칙",
    DecidedBy.LLM: "LLM",
    DecidedBy.REVIEWER: "심사원",
}

# 비용 표시 자릿수.
_COST_QUANTUM = Decimal("0.000001")


class EvaluationError(RuntimeError):
    """골든셋이 없거나 형식이 어긋나거나, 대조할 모의심사가 없을 때."""


# --------------------------------------------------------------------------
# 골든셋 로드
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenCase:
    """골든셋 케이스 1건."""

    criterion_code: str
    project_fixture: str
    expected_status: FindingStatus
    expert_note: str


@dataclass(frozen=True)
class GoldenSet:
    """골든셋 파일 1개."""

    version: str
    note: str
    cases: list[GoldenCase] = field(default_factory=list)
    path: Path | None = None

    def for_fixture(self, fixture: str) -> list[GoldenCase]:
        """픽스처가 같은 케이스만 항목 코드 순으로 돌려준다."""
        selected = [case for case in self.cases if case.project_fixture == fixture]
        return sorted(selected, key=lambda case: code_sort_key(case.criterion_code))


def known_criterion_codes(path: Path | None = None) -> set[str]:
    """`criteria.json` 에 실제로 있는 항목 코드 집합(CLAUDE.md 절대 규칙 1)."""
    _, items = load_criteria_file(path)
    return {str(item["code"]) for item in items}


def load_golden_set(
    path: Path | None = None, *, known_codes: set[str] | None = None
) -> GoldenSet:
    """골든셋 YAML 을 읽고 검증한다.

    항목 코드는 `criteria.json` 에 실제로 있는 것만 허용한다. 없는 코드를 쓰면
    골든셋이 인증기준을 지어내는 통로가 된다.
    """
    source = path or DEFAULT_GOLDEN_PATH
    if not source.exists():
        raise EvaluationError(f"골든셋 파일이 없다: {source}")

    with source.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)

    if not isinstance(payload, dict):
        raise EvaluationError(f"골든셋 최상위가 매핑이 아니다: {source}")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvaluationError(f"골든셋에 cases 목록이 없다: {source}")

    codes = known_codes if known_codes is not None else known_criterion_codes()
    valid_statuses = {status.value for status in FindingStatus}

    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, dict):
            raise EvaluationError(f"{index}번째 케이스가 매핑이 아니다")
        missing = [key for key in _CASE_KEYS if not str(raw.get(key) or "").strip()]
        if missing:
            raise EvaluationError(
                f"{index}번째 케이스에 필수 키가 비어 있다: {', '.join(missing)}"
            )

        code = str(raw["criterion_code"]).strip()
        if code not in codes:
            raise EvaluationError(
                f"{index}번째 케이스의 항목 코드가 criteria.json 에 없다: {code}"
            )
        if code in seen:
            raise EvaluationError(f"골든셋에 항목 코드가 중복됐다: {code}")
        seen.add(code)

        status = str(raw["expected_status"]).strip()
        if status not in valid_statuses:
            raise EvaluationError(
                f"{code} 의 expected_status 가 유효하지 않다: {status} "
                f"(가능한 값: {', '.join(sorted(valid_statuses))})"
            )

        cases.append(
            GoldenCase(
                criterion_code=code,
                project_fixture=str(raw["project_fixture"]).strip(),
                expected_status=FindingStatus(status),
                expert_note=" ".join(str(raw["expert_note"]).split()),
            )
        )

    return GoldenSet(
        version=str(payload.get("version") or "0"),
        note=" ".join(str(payload.get("note") or "").split()),
        cases=cases,
        path=source,
    )


# --------------------------------------------------------------------------
# 평가 결과 값 객체
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseOutcome:
    """골든셋 케이스 1건의 대조 결과."""

    criterion_code: str
    expected: FindingStatus
    actual: FindingStatus | None
    decided_by: DecidedBy | None
    expert_note: str

    @property
    def matched(self) -> bool:
        """기대 판정과 실제 판정이 같은가. 판정이 없으면 불일치로 본다."""
        return self.actual is not None and self.actual is self.expected


@dataclass(frozen=True)
class UnmetScores:
    """unmet 판정의 정밀도·재현율(골든셋 안에서만 계산한다)."""

    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        """미충족이라고 한 것 중 실제로 미충족이어야 하는 비율."""
        predicted = self.true_positive + self.false_positive
        return round(self.true_positive / predicted, 4) if predicted else 0.0

    @property
    def recall(self) -> float:
        """미충족이어야 하는 것 중 실제로 잡아낸 비율."""
        actual = self.true_positive + self.false_negative
        return round(self.true_positive / actual, 4) if actual else 0.0

    @property
    def f1(self) -> float:
        """정밀도·재현율의 조화평균."""
        precision, recall = self.precision, self.recall
        if precision + recall == 0:
            return 0.0
        return round(2 * precision * recall / (precision + recall), 4)


@dataclass(frozen=True)
class ReferenceAudit:
    """근거 참조 유효성 점검 결과(전체 판정 대상).

    판정이 인용한 chunk/evidence id 가 실제 `chunks`·`evidence` 행으로 존재하는지
    본다. PRD §8 목표는 100% 다 — 하나라도 깨지면 환각 방지 장치가 새고 있다는 뜻이다.
    """

    total_references: int
    valid_references: int
    findings_with_references: int
    findings_with_invalid_references: int
    invalid_examples: list[str] = field(default_factory=list)

    @property
    def validity(self) -> float:
        """유효 참조 비율. 인용이 하나도 없으면 1.0 으로 본다(깨진 참조가 없다)."""
        if self.total_references == 0:
            return 1.0
        return round(self.valid_references / self.total_references, 4)


@dataclass(frozen=True)
class EvaluationResult:
    """평가 1회 결과. 리포트와 콘솔 출력이 같은 값을 쓴다."""

    golden_version: str
    golden_note: str
    golden_path: Path | None
    project_name: str
    project_id: uuid.UUID
    assessment_id: uuid.UUID
    assessment_model: str | None
    assessment_finished_at: datetime | None
    finding_count: int
    status_counts: dict[FindingStatus, int]
    outcomes: list[CaseOutcome]
    unmet: UnmetScores
    references: ReferenceAudit
    cost_usd: Decimal

    @property
    def case_count(self) -> int:
        """골든셋 케이스 수."""
        return len(self.outcomes)

    @property
    def matched_count(self) -> int:
        """일치한 케이스 수."""
        return sum(1 for outcome in self.outcomes if outcome.matched)

    @property
    def missing_codes(self) -> list[str]:
        """모의심사에 판정이 아예 없던 항목 코드."""
        return [outcome.criterion_code for outcome in self.outcomes if outcome.actual is None]

    @property
    def agreement(self) -> float:
        """전체 일치율."""
        if not self.outcomes:
            return 0.0
        return round(self.matched_count / len(self.outcomes), 4)

    @property
    def unknown_ratio(self) -> float:
        """전체 판정 중 판단불가 비율."""
        if not self.finding_count:
            return 0.0
        return round(self.status_counts.get(FindingStatus.UNKNOWN, 0) / self.finding_count, 4)

    @property
    def unknown_ratio_golden(self) -> float:
        """골든셋 케이스 중 판단불가로 나온 비율."""
        if not self.outcomes:
            return 0.0
        unknown = sum(
            1 for outcome in self.outcomes if outcome.actual is FindingStatus.UNKNOWN
        )
        return round(unknown / len(self.outcomes), 4)

    @property
    def cost_per_criterion_usd(self) -> Decimal:
        """항목당 평균 비용(실행 비용 ÷ 판정 수)."""
        if not self.finding_count:
            return Decimal("0")
        return (self.cost_usd / Decimal(self.finding_count)).quantize(
            _COST_QUANTUM, rounding=ROUND_HALF_UP
        )

    def metrics(self) -> dict[str, Any]:
        """PRD §8 지표 묶음. 콘솔·리포트·테스트가 같은 dict 를 본다."""
        return {
            "golden_version": self.golden_version,
            "golden_case_count": self.case_count,
            "matched_case_count": self.matched_count,
            "missing_case_count": len(self.missing_codes),
            "agreement": self.agreement,
            "unmet_precision": self.unmet.precision,
            "unmet_recall": self.unmet.recall,
            "unmet_f1": self.unmet.f1,
            "unknown_ratio": self.unknown_ratio,
            "unknown_ratio_golden": self.unknown_ratio_golden,
            "evidence_reference_validity": self.references.validity,
            "evidence_reference_total": self.references.total_references,
            "finding_count": self.finding_count,
            "cost_usd": str(self.cost_usd),
            "cost_per_criterion_usd": str(self.cost_per_criterion_usd),
        }


# --------------------------------------------------------------------------
# 대조 대상 찾기
# --------------------------------------------------------------------------


def find_fixture_project(db: Session, fixture: str = DEMO_FIXTURE) -> Project | None:
    """픽스처 이름에 해당하는 프로젝트를 찾는다. 지금은 데모 프로젝트 하나뿐이다."""
    if fixture != DEMO_FIXTURE:
        raise EvaluationError(
            f"지원하지 않는 project_fixture 다: {fixture} (현재는 '{DEMO_FIXTURE}' 만 있다)"
        )
    return db.execute(
        select(Project).where(Project.name == DEMO_PROJECT_NAME)
    ).scalar_one_or_none()


def latest_done_assessment(db: Session, project_id: uuid.UUID) -> Assessment | None:
    """프로젝트의 최신 완료(done) 모의심사."""
    return db.execute(
        select(Assessment)
        .where(
            Assessment.project_id == project_id,
            Assessment.status == AssessmentStatus.DONE,
        )
        .order_by(Assessment.finished_at.desc().nullslast(), Assessment.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


# --------------------------------------------------------------------------
# 지표 계산
# --------------------------------------------------------------------------


def _as_uuid(value: object) -> uuid.UUID | None:
    """저장된 참조 문자열을 UUID 로 바꾼다. 형식이 아니면 None(=무효 참조)."""
    token = str(value).strip()
    for prefix in ("c_", "e_"):
        if token.startswith(prefix):
            token = token[len(prefix) :]
            break
    try:
        return uuid.UUID(token)
    except ValueError:
        return None


def audit_references(
    db: Session, project_id: uuid.UUID, findings: list[Finding]
) -> ReferenceAudit:
    """판정이 인용한 근거 id 가 실제 행으로 존재하는지 확인한다.

    청크는 같은 프로젝트 문서의 것만, 증적은 같은 프로젝트의 것만 유효로 본다.
    다른 프로젝트의 id 를 인용하면 테넌트 경계를 넘은 근거이므로 무효다.
    """
    chunk_ids = set(
        db.execute(
            select(Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.project_id == project_id)
        ).scalars()
    )
    evidence_ids = set(
        db.execute(select(Evidence.id).where(Evidence.project_id == project_id)).scalars()
    )

    total = 0
    valid = 0
    with_references = 0
    with_invalid = 0
    examples: list[str] = []

    for finding in findings:
        references: list[tuple[str, object, set[uuid.UUID]]] = [
            *(("chunk", raw, chunk_ids) for raw in finding.evidence_chunk_ids or []),
            *(("evidence", raw, evidence_ids) for raw in finding.evidence_ids or []),
        ]
        if not references:
            continue
        with_references += 1
        broken = False
        for kind, raw, allowed in references:
            total += 1
            reference = _as_uuid(raw)
            if reference is not None and reference in allowed:
                valid += 1
                continue
            broken = True
            if len(examples) < 5:
                examples.append(f"{finding.criterion_code} → {kind}:{raw}")
        if broken:
            with_invalid += 1

    return ReferenceAudit(
        total_references=total,
        valid_references=valid,
        findings_with_references=with_references,
        findings_with_invalid_references=with_invalid,
        invalid_examples=examples,
    )


def evaluate_assessment(
    db: Session,
    *,
    assessment: Assessment,
    golden: GoldenSet,
    fixture: str = DEMO_FIXTURE,
) -> EvaluationResult:
    """모의심사 1회를 골든셋과 대조한다."""
    project = db.execute(
        select(Project).where(Project.id == assessment.project_id)
    ).scalar_one_or_none()
    if project is None:
        raise EvaluationError(f"모의심사의 프로젝트를 찾을 수 없다: {assessment.project_id}")

    findings = list(
        db.execute(
            select(Finding).where(Finding.assessment_id == assessment.id)
        ).scalars()
    )
    by_code = {finding.criterion_code: finding for finding in findings}

    status_counts = {status: 0 for status in FindingStatus}
    for finding in findings:
        status_counts[finding.status] += 1

    outcomes: list[CaseOutcome] = []
    true_positive = false_positive = false_negative = 0
    for case in golden.for_fixture(fixture):
        matched: Finding | None = by_code.get(case.criterion_code)
        actual = matched.status if matched is not None else None
        outcomes.append(
            CaseOutcome(
                criterion_code=case.criterion_code,
                expected=case.expected_status,
                actual=actual,
                decided_by=matched.decided_by if matched is not None else None,
                expert_note=case.expert_note,
            )
        )
        expected_unmet = case.expected_status is FindingStatus.UNMET
        actual_unmet = actual is FindingStatus.UNMET
        if expected_unmet and actual_unmet:
            true_positive += 1
        elif actual_unmet:
            false_positive += 1
        elif expected_unmet:
            false_negative += 1

    return EvaluationResult(
        golden_version=golden.version,
        golden_note=golden.note,
        golden_path=golden.path,
        project_name=project.name,
        project_id=project.id,
        assessment_id=assessment.id,
        assessment_model=assessment.model,
        assessment_finished_at=assessment.finished_at,
        finding_count=len(findings),
        status_counts=status_counts,
        outcomes=outcomes,
        unmet=UnmetScores(
            true_positive=true_positive,
            false_positive=false_positive,
            false_negative=false_negative,
        ),
        references=audit_references(db, project.id, findings),
        cost_usd=Decimal(assessment.cost_usd or 0),
    )


def evaluate_fixture(
    db: Session, *, golden: GoldenSet, fixture: str = DEMO_FIXTURE
) -> EvaluationResult:
    """픽스처 프로젝트의 최신 완료 모의심사를 골든셋과 대조한다."""
    project = find_fixture_project(db, fixture)
    if project is None:
        raise EvaluationError(
            f"'{DEMO_PROJECT_NAME}' 프로젝트가 없다. 먼저 `make demo` 로 데모 시드를 적재한다."
        )
    assessment = latest_done_assessment(db, project.id)
    if assessment is None:
        raise EvaluationError(
            f"'{project.name}' 에 완료(done)된 모의심사가 없다. "
            "`make demo` 로 시드를 다시 적재하거나 모의심사를 실행한다."
        )
    return evaluate_assessment(db, assessment=assessment, golden=golden, fixture=fixture)


# --------------------------------------------------------------------------
# 리포트
# --------------------------------------------------------------------------


def _percent(value: float) -> str:
    """0~1 비율을 백분율 문구로."""
    return f"{round(value * 100, 1)}%"


def _status_label(status: FindingStatus | None) -> str:
    """판정 라벨. 판정이 없으면 '(없음)'."""
    if status is None:
        return "(없음)"
    return f"{STATUS_LABELS[status]}({status.value})"


def report_path(directory: Path | None = None, *, today: date | None = None) -> Path:
    """리포트 저장 경로 `docs/eval/YYYY-MM-DD.md`."""
    target = directory or DEFAULT_REPORT_DIR
    stamp = (today or date.today()).isoformat()
    return target / f"{stamp}.md"


def render_report(result: EvaluationResult, *, today: date | None = None) -> str:
    """평가 리포트(한국어 마크다운)를 만든다."""
    stamp = (today or date.today()).isoformat()
    finished = (
        result.assessment_finished_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if result.assessment_finished_at
        else "-"
    )
    golden_file = (
        result.golden_path.relative_to(REPO_ROOT).as_posix()
        if result.golden_path is not None and result.golden_path.is_relative_to(REPO_ROOT)
        else str(result.golden_path)
    )

    lines: list[str] = [
        f"# 골든셋 평가 리포트 — {stamp}",
        "",
        "`make eval` 이 만든 자동 리포트다. PRD §8 "
        "\"평가(골든셋)\" 의 지표를 그대로 계산한다.",
        "",
        "## 1. 실행 정보",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 프로젝트 | {result.project_name} |",
        f"| 모의심사 id | `{result.assessment_id}` |",
        f"| 판정 모델 | {result.assessment_model or '-'} |",
        f"| 모의심사 완료 시각 | {finished} |",
        f"| 판정 수 | {result.finding_count}개 |",
        f"| 골든셋 | {golden_file} (버전 {result.golden_version}, "
        f"{result.case_count}개 케이스) |",
        "",
        "## 2. 지표 요약",
        "",
        "| 지표 | 값 | 비고 |",
        "| --- | --- | --- |",
        f"| 전체 일치율 | {_percent(result.agreement)} | "
        f"골든셋 {result.case_count}개 중 {result.matched_count}개 일치 |",
        f"| 미충족 정밀도 | {result.unmet.precision:.3f} | "
        f"미충족이라 한 {result.unmet.true_positive + result.unmet.false_positive}개 중 "
        f"{result.unmet.true_positive}개 적중 |",
        f"| 미충족 재현율 | {result.unmet.recall:.3f} | "
        f"미충족이어야 할 {result.unmet.true_positive + result.unmet.false_negative}개 중 "
        f"{result.unmet.true_positive}개 검출 |",
        f"| 미충족 F1 | {result.unmet.f1:.3f} | 정밀도·재현율 조화평균 |",
        f"| 판단불가 비율(전체) | {_percent(result.unknown_ratio)} | "
        f"{result.status_counts.get(FindingStatus.UNKNOWN, 0)}/{result.finding_count} |",
        f"| 판단불가 비율(골든셋) | {_percent(result.unknown_ratio_golden)} | "
        "골든셋 케이스 안에서만 |",
        f"| 근거 참조 유효율 | {_percent(result.references.validity)} | "
        f"목표 100% · 인용 {result.references.total_references}건 중 "
        f"{result.references.valid_references}건 실존 |",
        f"| 항목당 평균 비용 | ${result.cost_per_criterion_usd} | "
        f"실행 비용 ${result.cost_usd} ÷ {result.finding_count}개 |",
        "",
        "## 3. 판정 분포",
        "",
        "| 판정 | 건수 | 비율 |",
        "| --- | --- | --- |",
    ]
    for status in FindingStatus:
        count = result.status_counts.get(status, 0)
        ratio = count / result.finding_count if result.finding_count else 0.0
        lines.append(f"| {STATUS_LABELS[status]}({status.value}) | {count} | {_percent(ratio)} |")

    lines += [
        "",
        "## 4. 골든셋 대조",
        "",
        "| 항목 | 기대 | 실제 | 판정 주체 | 일치 | 기대값 근거(요약) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for outcome in result.outcomes:
        decided = (
            DECIDED_BY_LABELS[outcome.decided_by] if outcome.decided_by is not None else "-"
        )
        note = outcome.expert_note
        if len(note) > 80:
            note = note[:80] + "…"
        lines.append(
            f"| {outcome.criterion_code} | {_status_label(outcome.expected)} | "
            f"{_status_label(outcome.actual)} | {decided} | "
            f"{'O' if outcome.matched else 'X'} | {note} |"
        )

    lines += [
        "",
        "## 5. 근거 참조 유효성",
        "",
        f"- 근거를 인용한 판정: {result.references.findings_with_references}개 "
        f"/ 전체 {result.finding_count}개",
        f"- 인용된 참조: {result.references.total_references}건 "
        f"(유효 {result.references.valid_references}건, "
        f"무효 {result.references.total_references - result.references.valid_references}건)",
        f"- 무효 참조를 포함한 판정: {result.references.findings_with_invalid_references}개",
    ]
    if result.references.invalid_examples:
        lines.append("- 무효 참조 예시:")
        lines.extend(f"  - `{example}`" for example in result.references.invalid_examples)
    else:
        lines.append(
            "- 무효 참조 없음. 존재하지 않는 근거를 인용한 판정은 파이프라인에서 "
            "폐기되므로(PRD §6 환각 방지) 이 값이 100% 가 아니면 회귀다."
        )

    if result.missing_codes:
        lines += [
            "",
            "## 6. 판정이 없는 골든셋 항목",
            "",
            "모의심사에 판정이 아예 없던 항목이다. 실행이 중간에 끊겼는지 확인한다.",
            "",
        ]
        lines.extend(f"- {code}" for code in result.missing_codes)

    lines += [
        "",
        "## 한계 (읽는 사람이 반드시 알아야 하는 것)",
        "",
        "- 골든셋 기대값은 데모 시드의 사실관계에서 유도한 값이며 **심사원·보안 전문가 "
        "검증 전**이다(PRD §14 오픈 이슈 4). 일치율은 '전문가 정답과의 일치'가 아니라 "
        "'우리가 데모 데이터에서 읽어낸 기대값과의 일치'다.",
        "- 평가 대상은 가상 회사 문서 12개와 AWS 점검 10개로 만든 단일 프로젝트다. "
        "실제 고객 데이터의 난이도를 대표하지 않는다.",
    ]
    if (result.assessment_model or "").startswith("fake"):
        lines.append(
            "- 이 실행은 `ANTHROPIC_API_KEY` 없이 결정적 Fake 프로바이더로 돌았다. "
            "일치율·정밀도·재현율은 LLM 판정 품질이 아니라 파이프라인 배선의 재현성만 "
            "보여 준다. 근거 참조 유효율과 비용 지표는 그대로 유효하다."
        )
    lines.append("")
    return "\n".join(lines)


def write_report(
    result: EvaluationResult,
    directory: Path | None = None,
    *,
    today: date | None = None,
) -> Path:
    """리포트를 `docs/eval/YYYY-MM-DD.md` 로 저장한다. 같은 날 재실행은 덮어쓴다."""
    path = report_path(directory, today=today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(result, today=today), encoding="utf-8")
    return path
