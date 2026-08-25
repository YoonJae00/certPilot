"""모의심사 파이프라인 (PRD §7 F3).

항목 1개마다:

1. **규칙 판정** — 매핑된 커넥터 증적의 pass/fail/warn 요약.
2. **검색** — 인증기준 + 주요 확인사항을 쿼리로 청크 top-8.
3. **LLM 판정** — 항목 정의·결함사례·규칙 결과·근거 블록을 넣고 JSON 을 받는다.
4. **후처리** — 스키마 검증 → 근거 참조 실존 검증 → 근거 없으면 unknown 강제 →
   규칙 fail 이면 unmet 덮어쓰기.

4번의 세 장치는 환각 방지의 핵심이다(CLAUDE.md 절대 규칙 2). 우회로를 만들지 않는다.
항목 101개는 동시 5개로 처리하고, 워커 스레드는 각자 자기 DB 세션을 연다
(SQLAlchemy 세션은 스레드 사이에서 공유할 수 없다).
"""

import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_session_factory
from app.llm.assess_prompt import (
    ChunkRef,
    CriterionPrompt,
    EvidenceBlock,
    LLMFinding,
    build_assess_prompt,
)
from app.llm.provider import LLMProvider, get_llm_provider
from app.models import (
    Assessment,
    AssessmentStatus,
    Criterion,
    DecidedBy,
    Finding,
    FindingStatus,
    Project,
)
from app.services.audit import record_audit
from app.services.rules import NO_RULE_RESULT_TEXT, RuleResult, load_rule_results
from app.services.scoring import code_sort_key, readiness_of
from app.services.search import search_project_chunks
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# 동시 처리 수(PRD §7 F3 "동시 5").
MAX_WORKERS = 5
# 항목당 검색할 청크 수(PRD §7 F3 "top-8").
TOP_K_CHUNKS = 8
# 근거로 인정할 최소 유사도. 벡터 검색은 관련이 없어도 k 건을 채워 주므로 하한선이
# 없으면 모든 항목에 근거가 생겨 판단불가가 사라진다. 해싱 임베딩은 유사도 스케일이
# 낮아 값이 작다 — 실제 임베딩 API 로 교체하면 이 상수를 다시 잡아야 한다.
# 0.05/0.10/…/0.30 스윕(데모 시드 + 골든셋 20케이스) 결과 0.10 이 최적이었다:
# 판단불가 1→34개로 D2 복원, 골든셋 일치율 0.55→0.70(최고), unmet 정밀도·재현율 불변.
MIN_CHUNK_SIMILARITY = 0.10
# 최초 1회 + 재시도 2회(PRD §8 원칙 5).
MAX_ATTEMPTS = 3
# 1회 실행 비용 상한(PRD §7 F3 "초기 상한 5달러").
COST_LIMIT_USD = Decimal("5.00")
# 진행률을 DB 에 반영하는 주기(항목 수).
PROGRESS_COMMIT_EVERY = 10
# Celery 워커 생존 확인 타임아웃(초). 요청 경로에서 도는 값이라 짧게 잡는다.
WORKER_PING_TIMEOUT_SECONDS = 0.5

# 다른 모듈이 이 이름들을 여기서 임포트할 수 있게 재수출한다(산식 정의는 services.scoring).
__all__ = [
    "AssessmentResult",
    "assess_project",
    "code_sort_key",
    "enqueue_assessment",
    "readiness_of",
    "run_assessment",
    "start_assessment_thread",
]

AUDIT_DONE_ACTION = "assessment.done"
AUDIT_FAILED_ACTION = "assessment.failed"

# rationale 본문 안의 근거 인용(`(chunk:c_…)`, `evidence:e_…`).
_INLINE_REFERENCE_RE = re.compile(r"\b(chunk|evidence):([ce]_[0-9A-Za-z-]+)")
# 모델이 코드 펜스로 감싸는 경우를 대비한 JSON 추출.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class FindingRejectedError(Exception):
    """LLM 응답이 스키마·근거 검증을 통과하지 못했다. 재시도 사유가 된다."""


@dataclass
class ItemOutcome:
    """항목 1개의 처리 결과."""

    code: str
    status: FindingStatus
    confidence: float
    rationale: str
    chunk_ids: list[str]
    evidence_ids: list[str]
    predicted_defect: str | None
    recommendation: str | None
    decided_by: DecidedBy
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    attempts: int = 0


@dataclass
class AssessmentResult:
    """모의심사 1회 실행 요약. 테스트와 로그에서 쓴다."""

    assessment_id: uuid.UUID
    status: AssessmentStatus
    finding_count: int
    summary: dict[str, Any]
    cost_usd: Decimal


@dataclass
class _Budget:
    """비용 상한 추적. 갱신은 조율 스레드 한 곳에서만 한다."""

    limit: Decimal
    spent: Decimal = Decimal("0")
    stop: threading.Event = field(default_factory=threading.Event)


def _normalize_reference(value: str, prefix: str) -> uuid.UUID | None:
    """`c_<uuid>` / `<uuid>` 를 UUID 로 바꾼다. 형식이 아니면 None."""
    token = str(value).strip()
    if token.startswith(prefix):
        token = token[len(prefix) :]
    try:
        return uuid.UUID(token)
    except ValueError:
        return None


def _validate_references(
    parsed: LLMFinding,
    *,
    allowed_chunks: set[uuid.UUID],
    allowed_evidence: set[uuid.UUID],
) -> tuple[list[str], list[str]]:
    """인용된 근거가 실제로 프롬프트에 제시된 것인지 확인한다.

    하나라도 없는 id 를 인용하면 판정 전체를 폐기한다(PRD §6 환각 방지 2차 장치).
    """
    chunk_ids: list[str] = []
    for raw in parsed.evidence_chunk_ids:
        reference = _normalize_reference(raw, "c_")
        if reference is None or reference not in allowed_chunks:
            raise FindingRejectedError(f"존재하지 않는 청크 참조를 인용했다: {raw}")
        if str(reference) not in chunk_ids:
            chunk_ids.append(str(reference))

    evidence_ids: list[str] = []
    for raw in parsed.evidence_ids:
        reference = _normalize_reference(raw, "e_")
        if reference is None or reference not in allowed_evidence:
            raise FindingRejectedError(f"존재하지 않는 증적 참조를 인용했다: {raw}")
        if str(reference) not in evidence_ids:
            evidence_ids.append(str(reference))

    # rationale 본문 안의 인용도 같은 기준으로 확인한다.
    for kind, token in _INLINE_REFERENCE_RE.findall(parsed.rationale or ""):
        prefix = "c_" if kind == "chunk" else "e_"
        allowed = allowed_chunks if kind == "chunk" else allowed_evidence
        reference = _normalize_reference(token, prefix)
        if reference is None or reference not in allowed:
            raise FindingRejectedError(
                f"rationale 이 존재하지 않는 근거를 인용했다: {kind}:{token}"
            )

    return chunk_ids, evidence_ids


def _parse_finding(text: str) -> LLMFinding:
    """LLM 응답 텍스트에서 판정 JSON 을 뽑아 스키마로 검증한다."""
    match = _JSON_OBJECT_RE.search(text or "")
    if match is None:
        raise FindingRejectedError("응답에서 JSON 객체를 찾지 못했다")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise FindingRejectedError(f"JSON 파싱 실패: {error}") from error
    if not isinstance(payload, dict):
        raise FindingRejectedError("JSON 최상위가 객체가 아니다")
    try:
        return LLMFinding.model_validate(payload)
    except ValidationError as error:
        raise FindingRejectedError(f"판정 스키마 위반: {error.error_count()}건") from error


def _unknown_outcome(
    code: str, reason: str, *, decided_by: DecidedBy = DecidedBy.LLM
) -> ItemOutcome:
    """근거·응답이 없을 때 남기는 판단불가 결과."""
    return ItemOutcome(
        code=code,
        status=FindingStatus.UNKNOWN,
        confidence=0.0,
        rationale=reason,
        chunk_ids=[],
        evidence_ids=[],
        predicted_defect=None,
        recommendation=None,
        decided_by=decided_by,
    )


def _apply_safeguards(
    outcome: ItemOutcome, rule: RuleResult, *, missing_info: list[str]
) -> ItemOutcome:
    """환각 방지 후처리. 순서를 바꾸지 않는다.

    1. 근거 참조가 모두 비면 `unknown` 으로 강제한다(절대 규칙 2).
    2. 규칙 판정에 fail 이 있으면 LLM 이 뭐라 했든 `unmet`(`decided_by=rule`).
    """
    if not outcome.chunk_ids and not outcome.evidence_ids:
        outcome.status = FindingStatus.UNKNOWN
        outcome.predicted_defect = None
        if missing_info:
            outcome.rationale = (
                f"{outcome.rationale} (판정에 필요한 자료: {', '.join(missing_info)})"
            ).strip()

    if rule.has_fail:
        failed_ids = rule.failed_evidence_ids()
        merged = list(outcome.evidence_ids)
        for evidence_id in failed_ids:
            if evidence_id not in merged:
                merged.append(evidence_id)
        checks = ", ".join(f"{item.source}.{item.check_id}" for item in rule.failed)
        outcome.status = FindingStatus.UNMET
        outcome.decided_by = DecidedBy.RULE
        outcome.evidence_ids = merged
        # 규칙 판정은 수집된 설정값에서 나온 결정적 결과다. LLM 의 확신도로 덮지 않는다.
        outcome.confidence = 0.95
        outcome.rationale = (
            f"클라우드 증적 점검 {checks} 이(가) fail 로 확인돼 미충족으로 판정한다. "
            f"(LLM 의견: {outcome.rationale})"
        )
        if not outcome.predicted_defect:
            outcome.predicted_defect = f"{checks} 점검이 인증기준을 만족하지 못함"
    return outcome


def _assess_criterion(
    *,
    project_id: uuid.UUID,
    criterion: CriterionPrompt,
    rule: RuleResult,
    provider: LLMProvider,
    session_factory: sessionmaker[Session],
    budget: _Budget,
) -> ItemOutcome | None:
    """항목 1개를 판정한다. 비용 상한으로 중단됐으면 None."""
    if budget.stop.is_set():
        return None

    session = session_factory()
    try:
        hits = search_project_chunks(
            session,
            project_id,
            build_criterion_query_text(criterion),
            k=TOP_K_CHUNKS,
            min_score=MIN_CHUNK_SIMILARITY,
        )
    except Exception:
        # 항목 하나의 실패가 전체 실행을 죽이지 않게 잡되, 로그는 반드시 남긴다.
        logger.exception("청크 검색 실패: 항목=%s", criterion.code)
        return _unknown_outcome(criterion.code, "근거 검색 중 오류가 발생해 판정하지 못했다")
    finally:
        session.close()

    chunks = [
        ChunkRef(id=hit.chunk_id, filename=hit.filename, page=hit.page, text=hit.text)
        for hit in hits
    ]
    evidences = [
        EvidenceBlock(
            id=item.id,
            source=item.source,
            check_id=item.check_id,
            status=item.status.value,
            collected_at=item.collected_at,
            summary=item.summary,
        )
        for item in rule.evidences
    ]
    allowed_chunks = {chunk.id for chunk in chunks}
    allowed_evidence = {evidence.id for evidence in evidences}

    prompt = build_assess_prompt(
        criterion,
        rule_text=rule.summary_text() if rule.has_evidence else NO_RULE_RESULT_TEXT,
        chunks=chunks,
        evidences=evidences,
    )

    input_tokens = 0
    output_tokens = 0
    cost = Decimal("0")
    last_reason = "알 수 없는 사유"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if budget.stop.is_set():
            return None
        try:
            result = provider.complete(prompt.system, prompt.user)
        except Exception:
            logger.exception("LLM 호출 실패: 항목=%s 시도=%d", criterion.code, attempt)
            last_reason = "LLM 호출이 실패했다"
            continue

        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        cost += result.cost_usd

        try:
            parsed = _parse_finding(result.text)
            chunk_ids, evidence_ids = _validate_references(
                parsed, allowed_chunks=allowed_chunks, allowed_evidence=allowed_evidence
            )
        except FindingRejectedError as error:
            last_reason = str(error)
            logger.warning(
                "판정 폐기 후 재시도: 항목=%s 시도=%d/%d 사유=%s",
                criterion.code,
                attempt,
                MAX_ATTEMPTS,
                last_reason,
            )
            continue

        outcome = ItemOutcome(
            code=criterion.code,
            status=parsed.status,
            confidence=parsed.confidence,
            rationale=parsed.rationale.strip(),
            chunk_ids=chunk_ids,
            evidence_ids=evidence_ids,
            predicted_defect=parsed.predicted_defect,
            recommendation=parsed.recommendation,
            decided_by=DecidedBy.LLM,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            attempts=attempt,
        )
        return _apply_safeguards(outcome, rule, missing_info=parsed.missing_info)

    logger.error(
        "항목 판정 실패(재시도 소진): 항목=%s 마지막 사유=%s", criterion.code, last_reason
    )
    outcome = _unknown_outcome(
        criterion.code,
        f"LLM 판정을 {MAX_ATTEMPTS}회 시도했으나 검증을 통과하지 못했다. "
        f"마지막 사유: {last_reason}",
    )
    outcome.input_tokens = input_tokens
    outcome.output_tokens = output_tokens
    outcome.cost_usd = cost
    outcome.attempts = MAX_ATTEMPTS
    # 근거 없는 판정이므로 규칙 fail 은 여전히 우선한다.
    return _apply_safeguards(outcome, rule, missing_info=[])


def build_criterion_query_text(criterion: CriterionPrompt) -> str:
    """항목 스냅샷에서 검색 쿼리를 만든다(청크 검색 API 와 같은 규칙)."""
    parts = [criterion.title, criterion.requirement]
    parts.extend(str(checkpoint) for checkpoint in criterion.checkpoints)
    return "\n".join(part for part in parts if part)


def _load_criteria(db: Session) -> list[CriterionPrompt]:
    """인증기준 101개를 값 객체로 읽는다(본문은 지어내지 않고 DB 에서만 온다)."""
    rows = list(db.execute(select(Criterion).order_by(Criterion.code)).scalars())
    snapshots = [
        CriterionPrompt(
            code=row.code,
            chapter=row.chapter,
            section=row.section,
            title=row.title,
            requirement=row.requirement,
            checkpoints=[str(item) for item in (row.checkpoints or [])],
            defect_examples=[str(item) for item in (row.defect_examples or [])],
        )
        for row in rows
    ]
    snapshots.sort(key=lambda item: code_sort_key(item.code))
    return snapshots


def build_summary(
    outcomes: list[ItemOutcome],
    chapters: dict[str, int],
    *,
    total: int,
    done: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`assessments.summary_json` 을 만든다.

    준비도 = (met + 0.5·partial) / (장 전체 − unknown). unknown 은 분모에서 뺀다
    (모르는 걸 점수에 넣지 않는다, PRD §7 F8).
    """
    counts = {status.value: 0 for status in FindingStatus}
    by_chapter: dict[str, dict[str, Any]] = {}

    for outcome in outcomes:
        counts[outcome.status.value] += 1
        chapter = str(chapters.get(outcome.code, 0))
        bucket = by_chapter.setdefault(
            chapter,
            {"total": 0, "met": 0, "partial": 0, "unmet": 0, "unknown": 0, "readiness": 0.0},
        )
        bucket["total"] += 1
        bucket[outcome.status.value] += 1

    for bucket in by_chapter.values():
        bucket["readiness"] = readiness_of(
            met=bucket["met"],
            partial=bucket["partial"],
            unknown=bucket["unknown"],
            total=bucket["total"],
        )

    summary: dict[str, Any] = {
        "counts": counts,
        "by_chapter": dict(sorted(by_chapter.items())),
        "progress": {"done": done, "total": total},
        "readiness": readiness_of(
            met=counts["met"],
            partial=counts["partial"],
            unknown=counts["unknown"],
            total=sum(counts.values()),
        ),
    }
    if extra:
        summary.update(extra)
    return summary


def _finish(
    db: Session,
    assessment: Assessment,
    *,
    status: AssessmentStatus,
    summary: dict[str, Any],
    cost: Decimal,
    org_id: uuid.UUID,
) -> None:
    """실행을 마감하고 감사 로그를 남긴다."""
    assessment.status = status
    assessment.finished_at = datetime.now(UTC)
    assessment.summary_json = summary
    assessment.cost_usd = cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    record_audit(
        db,
        action=AUDIT_DONE_ACTION if status is AssessmentStatus.DONE else AUDIT_FAILED_ACTION,
        org_id=org_id,
        target=str(assessment.id),
        meta={
            "counts": summary.get("counts", {}),
            "cost_usd": str(assessment.cost_usd),
            "model": assessment.model,
            **({"reason": summary["reason"]} if "reason" in summary else {}),
        },
    )
    db.commit()


def run_assessment(
    assessment_id: uuid.UUID, *, provider: LLMProvider | None = None
) -> AssessmentResult:
    """모의심사 1회를 끝까지 실행한다(동기).

    Celery 없이도 그대로 호출할 수 있다. 진행률은 실행 중에도 주기적으로 커밋한다.
    """
    session_factory = get_session_factory()
    llm = provider or get_llm_provider()
    db = session_factory()

    try:
        assessment = db.execute(
            select(Assessment).where(Assessment.id == assessment_id)
        ).scalar_one_or_none()
        if assessment is None:
            raise ValueError(f"모의심사를 찾을 수 없다: {assessment_id}")

        project = db.execute(
            select(Project).where(Project.id == assessment.project_id)
        ).scalar_one_or_none()
        if project is None:
            raise ValueError(f"프로젝트를 찾을 수 없다: {assessment.project_id}")
        org_id = project.org_id

        criteria = _load_criteria(db)
        if not criteria:
            summary = build_summary(
                [], {}, total=0, done=0, extra={"reason": "인증기준 지식베이스가 비어 있다"}
            )
            assessment.model = llm.model
            _finish(
                db,
                assessment,
                status=AssessmentStatus.FAILED,
                summary=summary,
                cost=Decimal("0"),
                org_id=org_id,
            )
            return AssessmentResult(
                assessment_id=assessment_id,
                status=AssessmentStatus.FAILED,
                finding_count=0,
                summary=summary,
                cost_usd=Decimal("0"),
            )

        chapters = {item.code: item.chapter for item in criteria}
        rules = load_rule_results(db, project.id)
        total = len(criteria)

        # 재실행이면 이전 판정을 먼저 비운다.
        db.execute(delete(Finding).where(Finding.assessment_id == assessment_id))
        assessment.status = AssessmentStatus.RUNNING
        assessment.started_at = datetime.now(UTC)
        assessment.finished_at = None
        assessment.model = llm.model
        assessment.cost_usd = Decimal("0")
        assessment.summary_json = {"progress": {"done": 0, "total": total}}
        db.commit()

        budget = _Budget(limit=COST_LIMIT_USD)
        outcomes: list[ItemOutcome] = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="assess") as pool:
            futures = {
                pool.submit(
                    _assess_criterion,
                    project_id=project.id,
                    criterion=criterion,
                    rule=rules.get(criterion.code) or RuleResult(criterion_code=criterion.code),
                    provider=llm,
                    session_factory=session_factory,
                    budget=budget,
                ): criterion.code
                for criterion in criteria
            }

            for future in as_completed(futures):
                code = futures[future]
                try:
                    outcome = future.result()
                except Exception:
                    logger.exception("항목 처리 중 예외: 항목=%s", code)
                    outcome = _unknown_outcome(code, "처리 중 오류가 발생해 판정하지 못했다")

                if outcome is None:
                    # 비용 상한으로 건너뛴 항목.
                    continue

                outcomes.append(outcome)
                budget.spent += outcome.cost_usd
                if budget.spent > budget.limit and not budget.stop.is_set():
                    logger.error(
                        "비용 상한 초과로 모의심사를 중단한다: 사용=%s 상한=%s",
                        budget.spent,
                        budget.limit,
                    )
                    budget.stop.set()

                if len(outcomes) % PROGRESS_COMMIT_EVERY == 0:
                    assessment.summary_json = {
                        "progress": {"done": len(outcomes), "total": total}
                    }
                    db.commit()

        outcomes.sort(key=lambda item: code_sort_key(item.code))
        db.add_all(
            [
                Finding(
                    assessment_id=assessment_id,
                    criterion_code=outcome.code,
                    status=outcome.status,
                    confidence=outcome.confidence,
                    rationale=outcome.rationale,
                    evidence_chunk_ids=outcome.chunk_ids,
                    evidence_ids=outcome.evidence_ids,
                    predicted_defect=outcome.predicted_defect,
                    recommendation=outcome.recommendation,
                    decided_by=outcome.decided_by,
                )
                for outcome in outcomes
            ]
        )

        aborted = budget.stop.is_set()
        extra = (
            {
                "reason": (
                    f"비용 상한 {budget.limit} USD 를 넘어 중단했다"
                    f"(사용 {budget.spent.quantize(Decimal('0.0001'))} USD)."
                )
            }
            if aborted
            else None
        )
        summary = build_summary(
            outcomes, chapters, total=total, done=len(outcomes), extra=extra
        )
        status = AssessmentStatus.FAILED if aborted else AssessmentStatus.DONE
        _finish(db, assessment, status=status, summary=summary, cost=budget.spent, org_id=org_id)

        logger.info(
            "모의심사 완료: id=%s 상태=%s 판정=%d 비용=%s USD",
            assessment_id,
            status.value,
            len(outcomes),
            assessment.cost_usd,
        )
        return AssessmentResult(
            assessment_id=assessment_id,
            status=status,
            finding_count=len(outcomes),
            summary=summary,
            cost_usd=budget.spent,
        )
    except Exception:
        # 예외를 삼키지 않는다. 상태만 기록하고 그대로 올린다.
        db.rollback()
        logger.exception("모의심사 실행 중 처리하지 못한 예외: id=%s", assessment_id)
        _mark_failed(db, assessment_id, "실행 중 오류가 발생했다")
        raise
    finally:
        db.close()


def _mark_failed(db: Session, assessment_id: uuid.UUID, reason: str) -> None:
    """예외로 죽은 실행을 failed 로 남긴다. 여기서 다시 실패하면 로그만 남긴다."""
    try:
        assessment = db.execute(
            select(Assessment).where(Assessment.id == assessment_id)
        ).scalar_one_or_none()
        if assessment is None:
            return
        assessment.status = AssessmentStatus.FAILED
        assessment.finished_at = datetime.now(UTC)
        summary = dict(assessment.summary_json or {})
        summary["reason"] = reason
        assessment.summary_json = summary
        org_id = db.execute(
            select(Project.org_id).where(Project.id == assessment.project_id)
        ).scalar_one_or_none()
        record_audit(
            db,
            action=AUDIT_FAILED_ACTION,
            org_id=org_id,
            target=str(assessment_id),
            meta={"reason": reason},
        )
        db.commit()
    except Exception:  # noqa: BLE001 - 마감 처리 실패가 원래 예외를 가리면 안 된다
        logger.exception("모의심사 실패 기록에 실패했다: id=%s", assessment_id)
        db.rollback()


@celery_app.task(name="certpilot.run_assessment")
def assess_project(assessment_id: str) -> dict[str, str | int]:
    """Celery 태스크. 동기 함수 `run_assessment` 를 감싸기만 한다."""
    result = run_assessment(uuid.UUID(assessment_id))
    return {
        "assessment_id": str(result.assessment_id),
        "status": result.status.value,
        "finding_count": result.finding_count,
    }


def has_celery_worker() -> bool:
    """살아 있는 Celery 워커가 있는지 확인한다.

    브로커만 떠 있고 워커가 없으면 큐에 넣어 봐야 잡이 영원히 대기한다. 요청 경로에서
    도는 확인이라 타임아웃을 짧게 잡는다.
    """
    try:
        replies = celery_app.control.ping(timeout=WORKER_PING_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - 브로커가 죽어 있어도 API 는 계속 동작해야 한다
        logger.info("Celery 브로커에 접속할 수 없다. 스레드 폴백으로 실행한다", exc_info=True)
        return False
    return bool(replies)


def enqueue_assessment(assessment_id: uuid.UUID) -> bool:
    """모의심사 잡을 큐에 넣는다. 워커·브로커가 없으면 False 를 돌려준다.

    `retry=False` 라서 브로커가 죽어 있으면 즉시 실패한다(요청이 매달리지 않는다).
    호출 측은 False 를 받으면 백그라운드 스레드 폴백을 쓴다.
    """
    if not has_celery_worker():
        logger.info("Celery 워커가 없어 스레드 폴백으로 실행한다: id=%s", assessment_id)
        return False
    try:
        assess_project.apply_async(args=[str(assessment_id)], retry=False)
        return True
    except Exception:  # noqa: BLE001 - 큐잉 실패가 API 를 500 으로 만들면 안 된다
        logger.warning(
            "모의심사 큐잉 실패, 스레드 폴백으로 실행한다: id=%s", assessment_id, exc_info=True
        )
        return False


def start_assessment_thread(assessment_id: uuid.UUID) -> threading.Thread:
    """Celery 워커가 없을 때 쓰는 백그라운드 스레드 폴백(데모용).

    운영에서는 Celery 워커가 처리한다. 이 경로는 브로커·워커 없이 데모를 돌리기
    위한 것이며, 프로세스가 죽으면 실행도 사라진다.
    """

    def _run() -> None:
        try:
            run_assessment(assessment_id)
        except Exception:  # noqa: BLE001 - 스레드에서 죽어도 로그는 남긴다
            logger.exception("백그라운드 모의심사 실행 실패: id=%s", assessment_id)

    thread = threading.Thread(target=_run, name=f"assessment-{assessment_id}", daemon=True)
    thread.start()
    return thread
