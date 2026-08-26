"""지식 그래프 API 스키마 (PRD §7 F3·F5·F8).

모의심사 판정·문서·클라우드 증적·알림을 하나의 그래프로 본다. 화면(Cytoscape)이
그대로 그릴 수 있는 모양이며, 다음 두 규칙이 스키마에 박혀 있다.

- 계층(장 → 절 → 항목)은 `parent_id` 로만 표현한다. `contains` 류 엣지를 만들지
  않는다 — compound 노드가 부모 관계를 그린다.
- 청크는 노드로 내리지 않는다. 수백 개가 화면을 덮으므로 `cites_document` 엣지의
  `chunk_count`/`chunk_ids` 로 접는다.

`datetime` 은 모듈째 임포트한다(`app/schemas/dashboard.py` 와 같은 이유 — 필드
이름과 타입 이름이 겹치는 것을 피한다).
"""

import datetime
import enum
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models import AlertType, DecidedBy, DocumentStatus, EvidenceStatus, FindingStatus


class GraphNodeType(enum.StrEnum):
    """그래프 노드 종류. 이 어휘의 단일 정의처다."""

    CHAPTER = "chapter"
    SECTION = "section"
    CRITERION = "criterion"
    DOCUMENT = "document"
    EVIDENCE = "evidence"
    ALERT = "alert"


class GraphEdgeType(enum.StrEnum):
    """그래프 엣지 종류. 이 어휘의 단일 정의처다."""

    # 판정이 인용한 문서(청크를 문서 단위로 접은 것).
    CITES_DOCUMENT = "cites_document"
    # 판정이 인용한 클라우드 증적(점검 단위).
    CITES_EVIDENCE = "cites_evidence"
    # 점검 → 인증기준 항목 정적 매핑(data/rules/aws_rules.yaml).
    MAPS_TO = "maps_to"
    # 알림을 유발한 증적.
    TRIGGERED = "triggered"


class GraphNodeBase(BaseModel):
    """모든 노드가 공유하는 필드. `parent_id` 가 계층을 만든다."""

    id: str
    label: str
    parent_id: str | None = None


class ChapterNodeOut(GraphNodeBase):
    """장 노드(`ch:2`). 라벨은 번호만 쓴다 — 장 이름을 지어내지 않는다."""

    type: Literal[GraphNodeType.CHAPTER] = GraphNodeType.CHAPTER
    chapter: int
    criteria_count: int


class SectionNodeOut(GraphNodeBase):
    """절 노드(`sec:2.5`). 라벨은 criteria 테이블 `section` 문자열 원문이다."""

    type: Literal[GraphNodeType.SECTION] = GraphNodeType.SECTION
    criteria_count: int


class GraphFindingOut(BaseModel):
    """항목 노드에 접어 넣은 판정 요약.

    상세(근거 본문·권고)는 기존 findings API 몫이다. 그래프는 색을 칠할 만큼만 싣는다.
    """

    finding_id: uuid.UUID
    status: FindingStatus
    confidence: float
    decided_by: DecidedBy


class CriterionNodeOut(GraphNodeBase):
    """인증기준 항목 노드(`cri:2.5.3`).

    `finding` 은 최신 완료 모의심사의 판정이다. 심사를 아직 돌리지 않았으면 None.
    """

    type: Literal[GraphNodeType.CRITERION] = GraphNodeType.CRITERION
    code: str
    finding: GraphFindingOut | None = None


class DocumentNodeOut(GraphNodeBase):
    """업로드 문서 노드(`doc:<uuid>`)."""

    type: Literal[GraphNodeType.DOCUMENT] = GraphNodeType.DOCUMENT
    document_id: uuid.UUID
    status: DocumentStatus


class EvidenceNodeOut(GraphNodeBase):
    """클라우드 증적 노드(`ev:<check_id>`).

    점검(check_id) 단위 노드다. 스냅샷이 여러 벌 쌓여도 최신 1행만 노드가 된다.
    `check_id` 가 이미 `aws.iam.user_mfa` 처럼 source 를 포함하므로 id 에 source 를
    다시 붙이지 않는다.
    """

    type: Literal[GraphNodeType.EVIDENCE] = GraphNodeType.EVIDENCE
    evidence_id: uuid.UUID
    source: str
    check_id: str
    status: EvidenceStatus
    collected_at: datetime.datetime


class AlertNodeOut(GraphNodeBase):
    """대시보드 알림 노드(`al:<uuid>`)."""

    type: Literal[GraphNodeType.ALERT] = GraphNodeType.ALERT
    alert_id: uuid.UUID
    alert_type: AlertType
    read: bool


# `type` 필드로 갈라지는 판별 유니온. 프런트가 좁히기(narrowing)에 쓴다.
GraphNodeOut = Annotated[
    ChapterNodeOut
    | SectionNodeOut
    | CriterionNodeOut
    | DocumentNodeOut
    | EvidenceNodeOut
    | AlertNodeOut,
    Field(discriminator="type"),
]


class GraphEdgeOut(BaseModel):
    """그래프 엣지 1개.

    `chunk_count`/`chunk_ids` 는 `cites_document` 에서만, `evidence_ids` 는
    `cites_evidence` 에서만 채워진다. 나머지는 기본값(None/빈 배열)이다.
    """

    id: str
    type: GraphEdgeType
    source: str
    target: str
    chunk_count: int | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class GraphOut(BaseModel):
    """지식 그래프 응답.

    `assessment_id` 는 판정을 가져온 최신 완료 모의심사다. 아직 없으면 None 이고,
    항목 노드의 `finding` 도 전부 None 이며 `cites_*` 엣지가 하나도 없다(골격만).
    """

    assessment_id: uuid.UUID | None = None
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)
