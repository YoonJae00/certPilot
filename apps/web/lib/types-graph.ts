/**
 * 지식 그래프 API 응답 계약 (PRD §7 F3·F5·F8 — 판정·증적·알림을 한 화면에 잇는다).
 *
 * `lib/types-dashboard.ts` 와 같은 이유로 `lib/types.ts` 와 분리해 둔다. 서버의
 * `GET /projects/{id}/graph` 스키마가 바뀌면 이 파일부터 맞춘다.
 * 판정 상태·증적 상태 같은 공통 유니온은 `lib/types.ts` 것을 그대로 재사용한다.
 */

import type {
  DecidedBy,
  DocumentStatus,
  EvidenceStatus,
  FindingStatus,
} from "@/lib/types";
import type { AlertType } from "@/lib/types-dashboard";

/** 노드 6종. `type` 으로 판별하는 유니온의 태그다. */
export type GraphNodeType =
  | "chapter"
  | "section"
  | "criterion"
  | "document"
  | "evidence"
  | "alert";

/** 엣지 4종. 계층(장→절→항목)은 엣지가 아니라 `parent_id` 로 표현한다. */
export type GraphEdgeType =
  | "cites_document"
  | "cites_evidence"
  | "maps_to"
  | "triggered";

/** 모든 노드가 공유하는 필드. `parent_id` 는 Cytoscape compound 의 부모 id 다. */
interface GraphNodeBase {
  id: string;
  label: string;
  /** 상위 노드 id. 루트(문서·증적·알림·장)는 null. */
  parent_id: string | null;
}

/** 장 노드(`ch:2`). 라벨은 "2장" 처럼 번호만 쓴다. */
export interface ChapterGraphNode extends GraphNodeBase {
  type: "chapter";
  /** 장 번호(1 | 2 | 3). 서버는 숫자로 준다. */
  chapter: number;
  /** 이 장에 속한 인증기준 항목 수. */
  criteria_count: number;
}

/** 절 노드(`sec:2.5`). 라벨은 criteria 테이블의 `section` 문자열 원문이다. */
export interface SectionGraphNode extends GraphNodeBase {
  type: "section";
  parent_id: string;
  criteria_count: number;
}

/** 최신 완료 심사에서 이 항목이 받은 판정을 접은 값. */
export interface CriterionFindingRef {
  finding_id: string;
  status: FindingStatus;
  /** 0~1 비율. `toPercent()` 로 백분율로 바꿔 쓴다. */
  confidence: number;
  decided_by: DecidedBy;
}

/**
 * 인증기준 항목 노드(`cri:2.5.3`).
 *
 * 원문(requirement·checkpoints)은 싣지 않는다. 상세는 판정 상세 API 몫이다.
 * 완료된 심사가 없으면 `finding` 이 null 이고 골격만 그려진다.
 */
export interface CriterionGraphNode extends GraphNodeBase {
  type: "criterion";
  parent_id: string;
  /** 인증기준 코드(예: "2.5.3"). */
  code: string;
  finding: CriterionFindingRef | null;
}

/** 업로드 문서 노드(`doc:<uuid>`). 라벨은 파일명. */
export interface DocumentGraphNode extends GraphNodeBase {
  type: "document";
  parent_id: null;
  document_id: string;
  status: DocumentStatus;
}

/**
 * 클라우드 증적 노드(`ev:<check_id>`).
 *
 * 점검(check_id) 단위다. 스냅샷이 여러 번 쌓여도 최신 1행만 노드가 된다.
 * `check_id` 자체가 이미 `aws.iam.user_mfa` 처럼 source 를 포함한다.
 */
export interface EvidenceGraphNode extends GraphNodeBase {
  type: "evidence";
  parent_id: null;
  /** 노드가 대표하는 최신 스냅샷 행의 id. */
  evidence_id: string;
  /** 수집원(예: "aws.iam"). */
  source: string;
  check_id: string;
  status: EvidenceStatus;
  collected_at: string;
}

/** 알림 노드(`al:<uuid>`). 라벨은 알림 메시지. */
export interface AlertGraphNode extends GraphNodeBase {
  type: "alert";
  parent_id: null;
  alert_id: string;
  alert_type: AlertType;
  read: boolean;
}

/** 그래프 노드 판별 유니온. */
export type ProjectGraphNode =
  | ChapterGraphNode
  | SectionGraphNode
  | CriterionGraphNode
  | DocumentGraphNode
  | EvidenceGraphNode
  | AlertGraphNode;

/**
 * 그래프 엣지 1건.
 *
 * 청크는 노드로 내리지 않는다(수백 개면 화면이 읽히지 않는다). 대신
 * `cites_document` 엣지에 `chunk_count`·`chunk_ids` 로 접어 넣는다.
 */
export interface ProjectGraphEdge {
  id: string;
  type: GraphEdgeType;
  /** 출발 노드 id. */
  source: string;
  /** 도착 노드 id. */
  target: string;
  /** cites_document 에서만 채워진다. 그 외에는 null. */
  chunk_count: number | null;
  /** 인용된 청크 id 목록. 해당 없으면 빈 배열. */
  chunk_ids: string[];
  /** 인용된 증적 행 id 목록. 해당 없으면 빈 배열. */
  evidence_ids: string[];
}

/** `GET /projects/{id}/graph` 응답. */
export interface ProjectGraph {
  /** 그래프가 반영한 최신 완료 심사 id. 완료된 심사가 없으면 null. */
  assessment_id: string | null;
  nodes: ProjectGraphNode[];
  edges: ProjectGraphEdge[];
}

/** 화면에서만 쓰는 보기 필터(서버로 보내지 않는다). */
export interface GraphFilters {
  /** 장 필터. "all" 이면 전체. */
  chapter: "all" | 1 | 2 | 3;
  /** 판정 상태 필터. 빈 배열이면 전체를 보여 준다. */
  statuses: FindingStatus[];
  showDocuments: boolean;
  /** 증적 표시 여부. 알림도 함께 따라간다(알림은 증적에 매달린 노드다). */
  showEvidence: boolean;
}

/** 필터 초기값. "보기 초기화" 도 이 값으로 되돌린다. */
export const DEFAULT_FILTERS: GraphFilters = {
  chapter: "all",
  statuses: [],
  showDocuments: true,
  showEvidence: true,
};
