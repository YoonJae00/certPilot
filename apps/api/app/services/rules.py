"""규칙 판정.

PRD §7 F3 파이프라인 1단계다. 항목에 매핑된 커넥터 증적(`evidence` 행)을 모아
pass/fail/warn 을 요약한다. **규칙이 LLM 을 이긴다**(PRD §8 원칙 2) — fail 이 하나라도
있으면 LLM 이 뭐라 하든 판정은 `unmet` 이다. 그 덮어쓰기는 모의심사 후처리에서 한다.

점검 정의 파일(`data/rules/aws_rules.yaml`)로 pass 조건을 계산하는 일은 AWS 커넥터
(Task 7) 몫이다. 여기서는 이미 수집돼 `evidence.status` 가 채워진 행만 읽는다.
증적이 없으면 "없음" 이고, 그건 실패가 아니라 판정 재료가 없다는 뜻이다.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Evidence, EvidenceStatus

# 규칙 판정 결과가 없을 때 프롬프트에 넣는 문구(PRD §8 프롬프트 구조).
NO_RULE_RESULT_TEXT = "없음"

# 프롬프트에 넣는 payload 요약 최대 길이.
PAYLOAD_SUMMARY_CHARS = 300


@dataclass(frozen=True)
class EvidenceRef:
    """규칙 판정에 쓰인 증적 1건의 스냅샷(세션에서 분리된 값 객체)."""

    id: uuid.UUID
    source: str
    check_id: str
    status: EvidenceStatus
    collected_at: datetime
    summary: str

    @property
    def reference(self) -> str:
        """프롬프트에서 쓰는 참조 토큰(`e_<uuid>`)."""
        return f"e_{self.id}"


@dataclass(frozen=True)
class RuleResult:
    """항목 1개에 대한 규칙 판정 요약."""

    criterion_code: str
    evidences: list[EvidenceRef] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        """규칙 판정에 쓸 증적이 있는가."""
        return bool(self.evidences)

    @property
    def failed(self) -> list[EvidenceRef]:
        """fail 로 판정된 증적."""
        return [item for item in self.evidences if item.status is EvidenceStatus.FAIL]

    @property
    def has_fail(self) -> bool:
        """fail 증적이 하나라도 있는가. True 면 LLM 판정을 덮어쓴다."""
        return bool(self.failed)

    @property
    def verdict(self) -> str:
        """종합 판정. fail > warn > pass 순으로 나쁜 쪽을 택한다."""
        if not self.evidences:
            return "none"
        statuses = {item.status for item in self.evidences}
        if EvidenceStatus.FAIL in statuses:
            return "fail"
        if EvidenceStatus.WARN in statuses:
            return "warn"
        if EvidenceStatus.PASS in statuses:
            return "pass"
        return "unknown"

    def evidence_ids(self) -> list[str]:
        """증적 id 목록(문자열)."""
        return [str(item.id) for item in self.evidences]

    def failed_evidence_ids(self) -> list[str]:
        """fail 증적 id 목록(문자열)."""
        return [str(item.id) for item in self.failed]

    def summary_text(self) -> str:
        """프롬프트 `## 규칙 판정 결과` 블록 본문."""
        if not self.evidences:
            return NO_RULE_RESULT_TEXT

        counts: dict[str, int] = defaultdict(int)
        for item in self.evidences:
            counts[item.status.value] += 1
        header = (
            f"종합: {self.verdict} "
            f"(pass {counts['pass']}, fail {counts['fail']}, "
            f"warn {counts['warn']}, unknown {counts['unknown']})"
        )
        lines = [header]
        for item in self.evidences:
            lines.append(
                f"- [{item.reference}] {item.source}.{item.check_id}: "
                f"{item.status.value} — {item.summary}"
            )
        return "\n".join(lines)


def _summarize_payload(payload: dict[str, object]) -> str:
    """증적 payload 를 한 줄 요약으로 줄인다. 자격증명·개인정보는 커넥터가 이미 제거한다."""
    if not payload:
        return "(상세 없음)"
    parts = [f"{key}={value}" for key, value in sorted(payload.items())]
    text = ", ".join(parts)
    if len(text) > PAYLOAD_SUMMARY_CHARS:
        return text[:PAYLOAD_SUMMARY_CHARS] + "…"
    return text


def _to_ref(row: Evidence) -> EvidenceRef:
    """ORM 증적 행을 값 객체로 옮긴다."""
    return EvidenceRef(
        id=row.id,
        source=row.source,
        check_id=row.check_id,
        status=row.status,
        collected_at=row.collected_at,
        summary=_summarize_payload(dict(row.payload_json or {})),
    )


def load_rule_results(db: Session, project_id: uuid.UUID) -> dict[str, RuleResult]:
    """프로젝트 증적을 한 번에 읽어 항목 코드별 규칙 판정으로 묶는다.

    101개 항목마다 쿼리를 날리지 않도록 모의심사 파이프라인은 이걸 쓴다.
    """
    rows = list(
        db.execute(
            select(Evidence)
            .where(Evidence.project_id == project_id)
            .order_by(Evidence.collected_at, Evidence.id)
        ).scalars()
    )

    grouped: dict[str, list[EvidenceRef]] = defaultdict(list)
    for row in rows:
        ref = _to_ref(row)
        for code in row.criterion_codes or []:
            grouped[str(code)].append(ref)

    return {
        code: RuleResult(criterion_code=code, evidences=items)
        for code, items in grouped.items()
    }


def evaluate_rules(db: Session, project_id: uuid.UUID, criterion_code: str) -> RuleResult:
    """항목 1개의 규칙 판정. 매핑된 증적이 없으면 빈 결과(=`없음`)다."""
    rows = list(
        db.execute(
            select(Evidence)
            .where(
                Evidence.project_id == project_id,
                # JSONB 배열 포함 검사(`@>`). 항목 코드가 매핑 목록에 있는 증적만 본다.
                Evidence.criterion_codes.contains([criterion_code]),
            )
            .order_by(Evidence.collected_at, Evidence.id)
        ).scalars()
    )
    return RuleResult(criterion_code=criterion_code, evidences=[_to_ref(row) for row in rows])
