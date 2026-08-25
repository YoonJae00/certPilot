"""골든셋 평가 CLI — `make eval` 이 부르는 스크립트 (PRD §8, 부록 B Task 12).

실행:
    cd apps/api && uv run python ../../scripts/eval_run.py

`data/eval/golden.yaml` 의 기대 판정과 데모 프로젝트의 **최신 완료 모의심사** 판정을
대조해 지표를 계산하고, `docs/eval/YYYY-MM-DD.md` 리포트를 만든다.

옵션
    --seed            데모 시드가 없으면(또는 --force-seed 면) `seed_demo()` 를 먼저 돌린다
    --golden PATH     골든셋 파일 경로
    --output-dir PATH 리포트 디렉터리 (기본 docs/eval)
    --no-report       리포트를 쓰지 않고 콘솔 출력만 한다

`.env` 의 `DATABASE_URL`·`S3_*` 를 쓴다. 스키마는 미리 `alembic upgrade head` 로 맞춘다
(`make eval` 이 대신 해 준다).
"""

from __future__ import annotations

import argparse
import logging
import sys
import unicodedata
from pathlib import Path

# apps/api 를 임포트 경로에 넣는다. 리포 어디서 실행해도 동작하게 하기 위함이다.
REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import get_session_factory  # noqa: E402
from app.models import FindingStatus  # noqa: E402
from app.services.demo_seed import seed_demo  # noqa: E402
from app.services.evaluation import (  # noqa: E402
    DEMO_FIXTURE,
    STATUS_LABELS,
    EvaluationError,
    EvaluationResult,
    evaluate_fixture,
    find_fixture_project,
    latest_done_assessment,
    load_golden_set,
    write_report,
)


def _percent(value: float) -> str:
    """0~1 비율을 백분율 문구로."""
    return f"{round(value * 100, 1)}%"


def _pad(text: str, width: int) -> str:
    """터미널 표 정렬용 패딩. 한글은 두 칸을 차지하므로 직접 센다."""
    used = sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)
    return text + " " * max(0, width - used)


def _print_result(result: EvaluationResult, report: Path | None) -> None:
    """지표와 대조 결과를 콘솔에 출력한다."""
    line = "─" * 78
    print()
    print(line)
    print("  골든셋 평가 결과 — " + result.project_name)
    print(line)
    print()
    print("  [실행 정보]")
    print(f"    모의심사      : {result.assessment_id}")
    print(f"    판정 모델     : {result.assessment_model or '-'}")
    print(f"    판정 수       : {result.finding_count}개")
    print(f"    골든셋        : 버전 {result.golden_version}, {result.case_count}개 케이스")
    print()
    print("  [지표] PRD §8")
    print(
        f"    전체 일치율        : {_percent(result.agreement)} "
        f"({result.matched_count}/{result.case_count})"
    )
    print(
        f"    미충족 정밀도      : {result.unmet.precision:.3f} "
        f"(TP {result.unmet.true_positive} / FP {result.unmet.false_positive})"
    )
    print(
        f"    미충족 재현율      : {result.unmet.recall:.3f} "
        f"(TP {result.unmet.true_positive} / FN {result.unmet.false_negative})"
    )
    print(f"    미충족 F1          : {result.unmet.f1:.3f}")
    print(
        f"    판단불가 비율      : {_percent(result.unknown_ratio)} "
        f"({result.status_counts.get(FindingStatus.UNKNOWN, 0)}/{result.finding_count}), "
        f"골든셋 안 {_percent(result.unknown_ratio_golden)}"
    )
    print(
        f"    근거 참조 유효율   : {_percent(result.references.validity)} "
        f"(목표 100%, 인용 {result.references.total_references}건 중 "
        f"{result.references.valid_references}건 실존)"
    )
    print(
        f"    항목당 평균 비용   : ${result.cost_per_criterion_usd} "
        f"(실행 ${result.cost_usd} ÷ {result.finding_count})"
    )
    print()
    print("  [골든셋 대조]")
    print(f"    {_pad('항목', 8)} {_pad('기대', 12)} {_pad('실제', 12)} 일치")
    for outcome in result.outcomes:
        expected = STATUS_LABELS[outcome.expected]
        actual = STATUS_LABELS[outcome.actual] if outcome.actual is not None else "(없음)"
        mark = "O" if outcome.matched else "X"
        print(
            f"    {_pad(outcome.criterion_code, 8)} {_pad(expected, 12)} "
            f"{_pad(actual, 12)} {mark}"
        )
    print()
    if result.references.validity < 1.0:
        print("  [경고] 근거 참조 유효율이 100% 가 아니다. 환각 방지 장치의 회귀를 의심한다.")
        for example in result.references.invalid_examples:
            print(f"          무효 참조: {example}")
        print()
    if report is not None:
        print(f"  리포트: {report}")
        print()
    print(line)
    print()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 인자를 읽는다."""
    parser = argparse.ArgumentParser(description="골든셋 평가 실행 (PRD §8)")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="데모 시드가 없으면 먼저 적재한다(시간이 걸린다)",
    )
    parser.add_argument(
        "--force-seed",
        action="store_true",
        help="데모 시드를 무조건 다시 적재한 뒤 평가한다",
    )
    parser.add_argument("--golden", type=Path, default=None, help="골든셋 파일 경로")
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="리포트 디렉터리 (기본 docs/eval)"
    )
    parser.add_argument(
        "--no-report", action="store_true", help="리포트 파일을 쓰지 않는다"
    )
    return parser.parse_args(argv)


def _has_fixture_assessment(session: Session) -> bool:
    """데모 프로젝트에 완료된 모의심사가 이미 있는가."""
    project = find_fixture_project(session)
    if project is None:
        return False
    return latest_done_assessment(session, project.id) is not None


def main(argv: list[str] | None = None) -> int:
    """골든셋을 읽고 평가한 뒤 리포트를 남긴다."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        golden = load_golden_set(args.golden)
    except EvaluationError as error:
        print(f"골든셋을 읽지 못했다: {error}", file=sys.stderr)
        return 1

    session = get_session_factory()()
    try:
        if args.force_seed or (args.seed and not _has_fixture_assessment(session)):
            print("데모 시드를 적재한다(모의심사 101개 항목을 포함한다)…")
            seed_demo(session)

        result = evaluate_fixture(session, golden=golden, fixture=DEMO_FIXTURE)
    except EvaluationError as error:
        print(f"평가를 실행하지 못했다: {error}", file=sys.stderr)
        print(
            "  힌트: `make demo` 로 데모 시드를 먼저 적재하거나, "
            "`make eval SEED=1` 로 시드까지 한 번에 돌린다.",
            file=sys.stderr,
        )
        return 1
    finally:
        session.close()

    report = None if args.no_report else write_report(result, args.output_dir)
    _print_result(result, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
