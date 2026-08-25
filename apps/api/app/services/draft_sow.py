"""운영명세서(sow) 초안 생성 (PRD §7 F4).

인증기준 101개 항목마다 1행을 만든다. 행의 재료는 **최신 완료 모의심사의 판정**뿐이다.

- `met` / `partial` → 판정 근거(rationale)와 근거 청크 본문으로 서술형 초안을 만든다.
- `unmet`          → "현재 미이행" + 예상 결함 + 개선 권고.
- `unknown`        → `[확인 필요]` + 판정하지 못한 사유.

판정에 없는 사실은 절대 만들어 넣지 않는다(CLAUDE.md 절대 규칙 2의 연장). 채울 수
없는 칸은 전부 `[확인 필요]` 로 남기고, 그 개수를 `stats.needs_review` 로 보고한다.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import (
    Assessment,
    AssessmentStatus,
    Chunk,
    Criterion,
    Document,
    Evidence,
    Finding,
    FindingStatus,
    Project,
)
from app.services.draft_common import (
    NEEDS_REVIEW,
    DraftSourceError,
    count_needs_review,
    document_label,
    join_sentences,
    strip_citations,
)
from app.services.scoring import code_sort_key

# 운영 현황 초안에 인용할 근거 청크 발췌 길이.
EXCERPT_LENGTH = 160
# 한 행의 "관련 문서·증적" 칸에 넣을 최대 개수(표가 읽을 수 없이 길어지지 않게).
MAX_RELATED_REFS = 5

# 모의심사가 없을 때 API 가 그대로 내보내는 문구.
NO_ASSESSMENT_MESSAGE = "모의심사를 먼저 실행하세요"

STATUS_LABELS: dict[FindingStatus, str] = {
    FindingStatus.MET: "충족",
    FindingStatus.PARTIAL: "부분충족",
    FindingStatus.UNMET: "미충족",
    FindingStatus.UNKNOWN: "판단불가",
}

DECIDED_BY_LABELS = {"rule": "규칙", "llm": "AI 초안", "reviewer": "심사원"}


@dataclass(frozen=True)
class ChunkRef:
    """운영명세서에서 인용할 문서 청크."""

    label: str
    page: int | None
    text: str

    def display(self) -> str:
        """`정보보호정책 v2.1 p.7` 형태의 표시 문자열."""
        return f"{self.label} p.{self.page}" if self.page else self.label


def latest_done_assessment(db: Session, project_id: uuid.UUID) -> Assessment | None:
    """프로젝트의 최신 `done` 모의심사를 읽는다. 없으면 None."""
    return db.execute(
        select(Assessment)
        .where(Assessment.project_id == project_id, Assessment.status == AssessmentStatus.DONE)
        .order_by(desc(Assessment.finished_at), desc(Assessment.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _load_chunk_refs(
    db: Session, project_id: uuid.UUID, chunk_ids: set[uuid.UUID]
) -> dict[uuid.UUID, ChunkRef]:
    """근거 청크를 프로젝트 스코프 안에서만 읽는다(테넌트 격리)."""
    if not chunk_ids:
        return {}
    rows = db.execute(
        select(Chunk.id, Chunk.page, Chunk.text, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(chunk_ids), Document.project_id == project_id)
    ).all()
    return {
        row.id: ChunkRef(label=document_label(row.filename), page=row.page, text=row.text or "")
        for row in rows
    }


def _load_evidence_labels(
    db: Session, project_id: uuid.UUID, evidence_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """근거 증적을 `aws.iam.mfa_enabled` 형태의 표시 문자열로 읽는다."""
    if not evidence_ids:
        return {}
    rows = db.execute(
        select(Evidence.id, Evidence.source, Evidence.check_id).where(
            Evidence.id.in_(evidence_ids), Evidence.project_id == project_id
        )
    ).all()
    return {row.id: f"{row.source}.{row.check_id}" for row in rows}


def _to_uuids(values: list[Any]) -> list[uuid.UUID]:
    """저장된 참조 문자열을 UUID 로 바꾼다. 형식이 아니면 버린다."""
    parsed: list[uuid.UUID] = []
    for value in values or []:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return parsed


def _excerpt(text: str) -> str:
    """근거 청크 본문에서 인용할 발췌를 만든다(청크 텍스트는 이미 마스킹돼 있다)."""
    normalized = " ".join((text or "").split())
    if len(normalized) <= EXCERPT_LENGTH:
        return normalized
    return f"{normalized[:EXCERPT_LENGTH].rstrip()}…"


def _evidence_sentence(chunks: list[ChunkRef], evidence_labels: list[str]) -> str:
    """근거를 한 문장으로 요약한다. 청크 본문이 있으면 그 발췌를 그대로 인용한다."""
    for chunk in chunks:
        excerpt = _excerpt(chunk.text)
        if excerpt:
            return f"근거 문서 「{chunk.display()}」에서 다음 내용을 확인했다: {excerpt}"
    if evidence_labels:
        return f"클라우드 증적 점검 {', '.join(evidence_labels)} 결과를 근거로 확인했다"
    return ""


def _operation_status(
    finding: Finding, chunks: list[ChunkRef], evidence_labels: list[str]
) -> str:
    """운영 현황 초안 1칸. 판정 상태별로 문장 구성이 다르다."""
    rationale = strip_citations(finding.rationale)
    recommendation = strip_citations(finding.recommendation or "")

    if finding.status is FindingStatus.UNKNOWN:
        # 판정하지 못한 사유를 그대로 남긴다. 없는 현황을 지어내지 않는다.
        reason = rationale or "제출된 문서·증적에서 관련 근거를 찾지 못했다"
        return f"{NEEDS_REVIEW} {join_sentences(reason, recommendation)}".strip()

    if finding.status is FindingStatus.UNMET:
        defect = strip_citations(finding.predicted_defect or "")
        return join_sentences(
            "현재 미이행",
            defect or f"{NEEDS_REVIEW} 예상 결함 내용을 확인해야 한다",
            recommendation or f"{NEEDS_REVIEW} 개선 방안을 확인해야 한다",
        )

    # met / partial: 판정 근거 + 근거 청크 발췌(+ 부분충족이면 보완 사항).
    evidence_sentence = _evidence_sentence(chunks, evidence_labels)
    parts = [rationale or f"{NEEDS_REVIEW} 운영 현황을 확인해야 한다", evidence_sentence]
    if finding.status is FindingStatus.PARTIAL:
        parts.append(
            f"보완 필요: {recommendation}"
            if recommendation
            else f"{NEEDS_REVIEW} 보완이 필요한 사항을 확인해야 한다"
        )
    return join_sentences(*parts)


def _note(finding: Finding) -> str:
    """비고 칸. 어떤 근거로 이 초안이 나왔는지 추적할 수 있게 남긴다."""
    label = STATUS_LABELS[finding.status]
    decided_by = DECIDED_BY_LABELS.get(finding.decided_by.value, finding.decided_by.value)
    return (
        f"모의심사 판정 {label} · 확신도 {round(float(finding.confidence), 2)} "
        f"· 판정 주체 {decided_by}"
    )


def _build_row(
    finding: Finding,
    criterion: Criterion,
    chunk_refs: dict[uuid.UUID, ChunkRef],
    evidence_labels: dict[uuid.UUID, str],
) -> dict[str, Any]:
    """항목 1개의 운영명세서 행을 만든다."""
    chunks = [
        chunk_refs[chunk_id]
        for chunk_id in _to_uuids(list(finding.evidence_chunk_ids or []))
        if chunk_id in chunk_refs
    ]
    evidences = [
        evidence_labels[evidence_id]
        for evidence_id in _to_uuids(list(finding.evidence_ids or []))
        if evidence_id in evidence_labels
    ]

    related: list[str] = []
    for value in [chunk.display() for chunk in chunks] + evidences:
        if value not in related:
            related.append(value)
    related = related[:MAX_RELATED_REFS] or [NEEDS_REVIEW]

    return {
        "criterion_code": finding.criterion_code,
        "criterion_title": criterion.title,
        "section": criterion.section,
        "operation_status": _operation_status(finding, chunks, evidences),
        "related_refs": related,
        # 담당 부서는 시스템이 알 수 없다. 항상 사람이 채운다.
        "owner_dept": NEEDS_REVIEW,
        "note": _note(finding),
    }


def build_sow_content(db: Session, project: Project) -> dict[str, Any]:
    """운영명세서 `content_json` 을 만든다.

    완료된 모의심사가 없으면 `DraftSourceError` 를 던진다(API 가 400 으로 바꾼다).
    """
    assessment = latest_done_assessment(db, project.id)
    if assessment is None:
        raise DraftSourceError(NO_ASSESSMENT_MESSAGE)

    records = db.execute(
        select(Finding, Criterion)
        .join(Criterion, Criterion.code == Finding.criterion_code)
        .where(Finding.assessment_id == assessment.id)
    ).all()
    if not records:
        raise DraftSourceError(f"{NO_ASSESSMENT_MESSAGE} (완료된 모의심사에 판정 결과가 없다)")

    chunk_ids: set[uuid.UUID] = set()
    evidence_ids: set[uuid.UUID] = set()
    for finding, _criterion in records:
        chunk_ids.update(_to_uuids(list(finding.evidence_chunk_ids or [])))
        evidence_ids.update(_to_uuids(list(finding.evidence_ids or [])))

    chunk_refs = _load_chunk_refs(db, project.id, chunk_ids)
    evidence_labels = _load_evidence_labels(db, project.id, evidence_ids)

    rows = [
        _build_row(finding, criterion, chunk_refs, evidence_labels)
        for finding, criterion in records
    ]
    rows.sort(key=lambda row: code_sort_key(str(row["criterion_code"])))

    counts = {status.value: 0 for status in FindingStatus}
    for finding, _criterion in records:
        counts[finding.status.value] += 1

    return {
        "assessment_id": str(assessment.id),
        "rows": rows,
        "stats": {
            "total": len(rows),
            # 사람이 채워야 할 **칸** 수. 담당 부서는 항상 비어 있으므로 행 수 이상이다.
            "needs_review": count_needs_review(rows),
            "needs_review_rows": sum(1 for row in rows if count_needs_review(row) > 0),
            "by_status": counts,
        },
    }
