"""지식 그래프 조립 (PRD §7 F3·F5·F8).

한 프로젝트를 "인증기준 계층 + 판정 + 근거"로 묶어 한 번에 그릴 수 있게 만든다.

설계 원칙 세 가지가 이 모듈의 모양을 결정한다.

1. 계층(장 → 절 → 항목)은 `parent_id` 로만 표현한다. `contains` 류 엣지를 만들지
   않는다 — 화면(Cytoscape compound)이 부모 관계를 직접 그린다.
2. 청크는 노드로 내리지 않는다. 문서 단위로 접어 `cites_document` 엣지의
   `chunk_count`/`chunk_ids` 에 싣는다(수백 개 노드로 화면이 덮이는 것을 막는다).
3. 증적은 점검(check_id) 단위 노드다. 스냅샷이 여러 벌 쌓여도 최신 1행만 노드가
   되고, 구 스냅샷 행을 인용한 판정도 같은 노드로 귀결된다.

쿼리는 고정 횟수(최대 7회)다. 항목 101개를 도는 루프 안에서는 DB 를 부르지 않는다.

테넌트 격리: 판정의 `evidence_chunk_ids`/`evidence_ids` 는 JSONB 문자열 배열이라
DB 제약이 없다. 그래서 여기서 **다시** 프로젝트 스코프로 조회해 걸러 낸다. 해석되지
않는 참조는 조용히 탈락시킨다(CLAUDE.md 절대 규칙 5).
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.mapping import MappingError, get_mapping
from app.models import (
    Alert,
    Assessment,
    Chunk,
    Criterion,
    Document,
    Evidence,
    Finding,
    Project,
)
from app.schemas.graph import (
    AlertNodeOut,
    ChapterNodeOut,
    CriterionNodeOut,
    DocumentNodeOut,
    EvidenceNodeOut,
    GraphEdgeOut,
    GraphEdgeType,
    GraphFindingOut,
    GraphNodeOut,
    GraphOut,
    SectionNodeOut,
)

# 최신 완료 모의심사를 읽는 함수는 운영명세서 초안과 같은 것을 쓴다.
# "최신 done 심사"의 정의가 두 곳으로 갈라지지 않게 한다.
from app.services.draft_sow import latest_done_assessment
from app.services.scoring import code_sort_key

# 노드 id 접두어. 종류가 달라도 id 가 겹치지 않게 한다.
CHAPTER_PREFIX = "ch:"
SECTION_PREFIX = "sec:"
CRITERION_PREFIX = "cri:"
DOCUMENT_PREFIX = "doc:"
EVIDENCE_PREFIX = "ev:"
ALERT_PREFIX = "al:"

# 엣지 id 접두어.
CITES_DOCUMENT_PREFIX = "cd:"
CITES_EVIDENCE_PREFIX = "ce:"
MAPS_TO_PREFIX = "mt:"
TRIGGERED_PREFIX = "tr:"


@dataclass
class _SectionAccumulator:
    """절 노드를 만들며 모으는 값."""

    chapter: int
    # criteria 테이블 `section` 문자열 원문. 절 이름을 지어내지 않는다.
    label: str
    criteria_count: int


def _to_uuids(values: list[Any]) -> list[uuid.UUID]:
    """저장된 참조 문자열을 UUID 로 바꾼다. 형식이 아니면 버린다.

    `app/api/assessments.py` 의 `_to_uuids` 와 같은 사양이다. JSONB 에 담긴 값은
    형식을 신뢰하지 않는다.
    """
    parsed: list[uuid.UUID] = []
    for value in values or []:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return parsed


def _section_number(code: str) -> str:
    """항목 코드에서 절 번호를 얻는다(`2.5.3` → `2.5`).

    `section` 문자열을 파싱하지 않는다. 코드가 유일한 근거다.
    """
    return ".".join(code.split(".")[:2])


def _evidence_label(check_id: str) -> str:
    """점검 표시명. 매핑에 없으면 `check_id` 를 그대로 쓴다(라벨을 지어내지 않는다)."""
    try:
        mapping = get_mapping(check_id)
    except MappingError:
        return check_id
    return mapping.title if mapping is not None else check_id


def _load_findings(
    db: Session, assessment: Assessment | None, known_codes: set[str]
) -> dict[str, Finding]:
    """최신 완료 모의심사의 판정을 항목 코드로 인덱싱한다. 심사가 없으면 빈 딕셔너리."""
    if assessment is None:
        return {}

    rows = db.execute(
        select(Finding)
        .where(Finding.assessment_id == assessment.id)
        .order_by(Finding.criterion_code, Finding.created_at, Finding.id)
    ).scalars()

    findings: dict[str, Finding] = {}
    for finding in rows:
        # 한 항목에 판정이 둘 이상 있으면 정렬 순서상 처음 것만 쓴다(결정적).
        if finding.criterion_code in known_codes and finding.criterion_code not in findings:
            findings[finding.criterion_code] = finding
    return findings


def _load_chunk_documents(
    db: Session, project_id: uuid.UUID, findings: dict[str, Finding]
) -> dict[uuid.UUID, uuid.UUID]:
    """인용된 청크 id → 문서 id. 이 프로젝트 문서의 청크만 담긴다.

    `Document.project_id` 로 다시 스코프하는 것이 핵심이다 — 판정 JSONB 에 다른
    조직의 청크 id 가 들어 있어도 여기서 걸러진다.
    """
    chunk_ids: set[uuid.UUID] = set()
    for finding in findings.values():
        chunk_ids.update(_to_uuids(list(finding.evidence_chunk_ids or [])))
    if not chunk_ids:
        return {}

    rows = db.execute(
        select(Chunk.id, Chunk.document_id)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(chunk_ids), Document.project_id == project_id)
    ).all()
    return {row.id: row.document_id for row in rows}


def _latest_evidence_by_check(rows: list[Evidence]) -> dict[str, Evidence]:
    """점검별 최신 스냅샷 행. 수집 시각이 같으면 id 가 큰 쪽을 쓴다(결정적 타이브레이크)."""
    latest: dict[str, Evidence] = {}
    for row in rows:
        current = latest.get(row.check_id)
        if current is None or (row.collected_at, row.id) > (current.collected_at, current.id):
            latest[row.check_id] = row
    return latest


def _hierarchy_nodes(
    criteria: list[Criterion], findings: dict[str, Finding]
) -> list[GraphNodeOut]:
    """장 → 절 → 항목 노드를 만든다. `criteria` 는 코드 순으로 정렬돼 있어야 한다."""
    chapter_counts: dict[int, int] = {}
    sections: dict[str, _SectionAccumulator] = {}

    for criterion in criteria:
        chapter_counts[criterion.chapter] = chapter_counts.get(criterion.chapter, 0) + 1
        number = _section_number(criterion.code)
        entry = sections.get(number)
        if entry is None:
            # 정렬돼 있으므로 절의 첫 항목이 곧 가장 앞선 코드다. 그 행의 문자열을 쓴다.
            sections[number] = _SectionAccumulator(
                chapter=criterion.chapter, label=criterion.section, criteria_count=1
            )
        else:
            entry.criteria_count += 1

    nodes: list[GraphNodeOut] = []
    # 부모가 자식보다 먼저 나오도록 장 → 절 → 항목 순으로 붙인다.
    for chapter in sorted(chapter_counts):
        nodes.append(
            ChapterNodeOut(
                id=f"{CHAPTER_PREFIX}{chapter}",
                label=f"{chapter}장",
                parent_id=None,
                chapter=chapter,
                criteria_count=chapter_counts[chapter],
            )
        )
    for number in sorted(sections, key=code_sort_key):
        entry = sections[number]
        nodes.append(
            SectionNodeOut(
                id=f"{SECTION_PREFIX}{number}",
                label=entry.label,
                parent_id=f"{CHAPTER_PREFIX}{entry.chapter}",
                criteria_count=entry.criteria_count,
            )
        )
    for criterion in criteria:
        finding = findings.get(criterion.code)
        nodes.append(
            CriterionNodeOut(
                id=f"{CRITERION_PREFIX}{criterion.code}",
                label=criterion.title,
                parent_id=f"{SECTION_PREFIX}{_section_number(criterion.code)}",
                code=criterion.code,
                finding=(
                    GraphFindingOut(
                        finding_id=finding.id,
                        status=finding.status,
                        confidence=round(float(finding.confidence), 4),
                        decided_by=finding.decided_by,
                    )
                    if finding is not None
                    else None
                ),
            )
        )
    return nodes


def _cites_document_edges(
    findings: dict[str, Finding], chunk_to_document: dict[uuid.UUID, uuid.UUID]
) -> list[GraphEdgeOut]:
    """판정이 인용한 청크를 문서 단위로 접는다(항목 → 문서)."""
    edges: list[GraphEdgeOut] = []
    for code, finding in findings.items():
        grouped: dict[uuid.UUID, set[str]] = {}
        for chunk_id in _to_uuids(list(finding.evidence_chunk_ids or [])):
            document_id = chunk_to_document.get(chunk_id)
            if document_id is None:
                # 삭제됐거나 다른 조직 문서의 청크. 조용히 버린다.
                continue
            grouped.setdefault(document_id, set()).add(str(chunk_id))

        for document_id, chunk_ids in grouped.items():
            ordered = sorted(chunk_ids)
            edges.append(
                GraphEdgeOut(
                    id=f"{CITES_DOCUMENT_PREFIX}{code}:{document_id}",
                    type=GraphEdgeType.CITES_DOCUMENT,
                    source=f"{CRITERION_PREFIX}{code}",
                    target=f"{DOCUMENT_PREFIX}{document_id}",
                    chunk_count=len(ordered),
                    chunk_ids=ordered,
                )
            )
    return edges


def _cites_evidence_edges(
    findings: dict[str, Finding], row_to_check: dict[uuid.UUID, str]
) -> list[GraphEdgeOut]:
    """판정이 인용한 증적을 점검 단위로 접는다(항목 → 증적).

    구 스냅샷 행을 인용해도 같은 `ev:<check_id>` 노드로 귀결된다. 인용된 원본 행 id 는
    `evidence_ids` 에 그대로 남겨 어느 스냅샷을 봤는지 추적할 수 있게 한다.
    """
    edges: list[GraphEdgeOut] = []
    for code, finding in findings.items():
        grouped: dict[str, set[str]] = {}
        for evidence_id in _to_uuids(list(finding.evidence_ids or [])):
            check_id = row_to_check.get(evidence_id)
            if check_id is None:
                # 삭제됐거나 다른 조직의 증적. 조용히 버린다.
                continue
            grouped.setdefault(check_id, set()).add(str(evidence_id))

        for check_id, evidence_ids in grouped.items():
            edges.append(
                GraphEdgeOut(
                    id=f"{CITES_EVIDENCE_PREFIX}{code}:{check_id}",
                    type=GraphEdgeType.CITES_EVIDENCE,
                    source=f"{CRITERION_PREFIX}{code}",
                    target=f"{EVIDENCE_PREFIX}{check_id}",
                    evidence_ids=sorted(evidence_ids),
                )
            )
    return edges


def _maps_to_edges(
    latest_evidence: dict[str, Evidence], known_codes: set[str]
) -> list[GraphEdgeOut]:
    """점검 → 인증기준 항목 정적 매핑(증적 → 항목).

    criteria 테이블에 실제로 있는 코드만 엣지가 된다. 안내서 개정으로 사라진 코드가
    증적에 남아 있어도 없는 노드를 가리키지 않는다.
    """
    edges: list[GraphEdgeOut] = []
    for check_id, row in latest_evidence.items():
        seen: set[str] = set()
        for raw_code in row.criterion_codes or []:
            code = str(raw_code)
            if code not in known_codes or code in seen:
                continue
            seen.add(code)
            edges.append(
                GraphEdgeOut(
                    id=f"{MAPS_TO_PREFIX}{check_id}:{code}",
                    type=GraphEdgeType.MAPS_TO,
                    source=f"{EVIDENCE_PREFIX}{check_id}",
                    target=f"{CRITERION_PREFIX}{code}",
                )
            )
    return edges


def _triggered_edges(
    alerts: list[Alert], row_to_check: dict[uuid.UUID, str]
) -> list[GraphEdgeOut]:
    """알림 → 그 알림을 유발한 증적.

    `evidence_id` 가 없거나 해석되지 않으면 엣지를 만들지 않는다. 알림 노드는 남는다.
    """
    edges: list[GraphEdgeOut] = []
    for alert in alerts:
        if alert.evidence_id is None:
            continue
        check_id = row_to_check.get(alert.evidence_id)
        if check_id is None:
            continue
        edges.append(
            GraphEdgeOut(
                id=f"{TRIGGERED_PREFIX}{alert.id}",
                type=GraphEdgeType.TRIGGERED,
                source=f"{ALERT_PREFIX}{alert.id}",
                target=f"{EVIDENCE_PREFIX}{check_id}",
            )
        )
    return edges


def build_graph(db: Session, project: Project) -> GraphOut:
    """프로젝트 하나의 지식 그래프를 만든다(읽기 전용).

    호출자는 먼저 `load_scoped_project` 로 org 스코프를 확정해야 한다. 이 함수는
    받은 프로젝트 id 로만 조회하며, 그 밖의 데이터에는 손대지 않는다.
    """
    criteria = list(db.execute(select(Criterion)).scalars())
    criteria.sort(key=lambda item: code_sort_key(item.code))
    known_codes = {item.code for item in criteria}

    assessment = latest_done_assessment(db, project.id)
    findings = _load_findings(db, assessment, known_codes)
    chunk_to_document = _load_chunk_documents(db, project.id, findings)

    documents = list(
        db.execute(select(Document).where(Document.project_id == project.id)).scalars()
    )
    documents.sort(key=lambda item: (item.filename, str(item.id)))

    evidence_rows = list(
        db.execute(select(Evidence).where(Evidence.project_id == project.id)).scalars()
    )
    # 구 스냅샷 행을 인용한 판정·알림을 해석하려면 전 행의 check_id 가 필요하다.
    row_to_check = {row.id: row.check_id for row in evidence_rows}
    latest_evidence = _latest_evidence_by_check(evidence_rows)

    alerts = list(db.execute(select(Alert).where(Alert.project_id == project.id)).scalars())
    # 최신 알림이 앞. 같은 수집 잡에서 생성돼 시각이 같으면 id 오름차순이다
    # (파이썬 정렬은 안정적이라 두 번 정렬하면 된다).
    alerts.sort(key=lambda item: item.id)
    alerts.sort(key=lambda item: item.created_at, reverse=True)

    nodes: list[GraphNodeOut] = _hierarchy_nodes(criteria, findings)
    nodes.extend(
        DocumentNodeOut(
            id=f"{DOCUMENT_PREFIX}{document.id}",
            label=document.filename,
            parent_id=None,
            document_id=document.id,
            status=document.status,
        )
        for document in documents
    )
    nodes.extend(
        EvidenceNodeOut(
            id=f"{EVIDENCE_PREFIX}{check_id}",
            label=_evidence_label(check_id),
            parent_id=None,
            evidence_id=latest_evidence[check_id].id,
            source=latest_evidence[check_id].source,
            check_id=check_id,
            status=latest_evidence[check_id].status,
            collected_at=latest_evidence[check_id].collected_at,
        )
        for check_id in sorted(latest_evidence)
    )
    nodes.extend(
        AlertNodeOut(
            id=f"{ALERT_PREFIX}{alert.id}",
            label=alert.message,
            parent_id=None,
            alert_id=alert.id,
            alert_type=alert.type,
            read=alert.read_at is not None,
        )
        for alert in alerts
    )

    edges = [
        *_cites_document_edges(findings, chunk_to_document),
        *_cites_evidence_edges(findings, row_to_check),
        *_maps_to_edges(latest_evidence, known_codes),
        *_triggered_edges(alerts, row_to_check),
    ]
    edges.sort(key=lambda edge: edge.id)

    return GraphOut(
        assessment_id=assessment.id if assessment is not None else None,
        nodes=nodes,
        edges=edges,
    )
