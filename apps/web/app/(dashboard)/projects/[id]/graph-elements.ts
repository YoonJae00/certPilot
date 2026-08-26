/**
 * 지식 그래프의 순수 계산부 — 서버 응답을 Cytoscape 입력으로 옮기고, 좌표와 필터를 만든다.
 *
 * 렌더링·DOM 은 `graph-canvas.tsx` 가 맡는다. 이 파일은 cytoscape 를 타입으로만 참조하는
 * 순수 모듈이라 `"use client"` 가 필요 없고, 그대로 단위 테스트할 수 있다.
 *
 * 배치는 결정적 `preset` 좌표다(force 레이아웃은 실행할 때마다 그림이 달라져
 * "같은 프로젝트인데 어제와 다르게 보인다"는 인상을 준다).
 *   · 항목  : 절 안에서 3열 그리드
 *   · 절    : 장 밴드 안에서 좌→우 4개씩 개행
 *   · 장    : 세로로 3개 밴드 적층 (계층은 엣지가 아니라 compound `parent` 로 그린다)
 *   · 문서  : 왼쪽 레일 / 증적: 오른쪽 레일 / 알림: 자기 증적 옆
 */

import type { ElementDefinition } from "cytoscape";

import type {
  ChapterGraphNode,
  CriterionGraphNode,
  GraphFilters,
  ProjectGraph,
  ProjectGraphEdge,
  ProjectGraphNode,
  SectionGraphNode,
} from "@/lib/types-graph";

/* ------------------------------------------------------------------ */
/* 배치 상수                                                            */
/* ------------------------------------------------------------------ */

/** 항목 1개가 차지하는 셀 크기(px). */
const CELL_W = 72;
const CELL_H = 52;
/** 절 안에서 항목을 몇 열로 깔지. */
const CRITERIA_COLS = 3;
/** 절 하나의 가로 폭. */
const SECTION_W = CELL_W * CRITERIA_COLS;
/** 절·절 사이 여백. */
const SECTION_GAP = 48;
/** 장 밴드 한 줄에 놓는 절 수. */
const SECTIONS_PER_ROW = 4;
/** 절 배치 간격(폭 + 여백). */
const SECTION_PITCH = SECTION_W + SECTION_GAP;
/** 장 밴드 사이 여백. */
const BAND_GAP = 140;
/** 본문 밴드에서 좌·우 레일까지의 거리. */
const RAIL_OFFSET = 280;
/** 레일 위 노드 간 세로 간격. */
const RAIL_STEP = 80;
/** 알림을 자기 증적에서 얼마나 오른쪽에 둘지. */
const ALERT_OFFSET_X = 90;
/** 한 증적에 알림이 여럿 달렸을 때 겹치지 않게 내리는 간격. */
const ALERT_STACK_STEP = 34;

/** 화면 좌표 1개. */
export interface GraphPosition {
  x: number;
  y: number;
}

/* ------------------------------------------------------------------ */
/* 정렬 도우미                                                          */
/* ------------------------------------------------------------------ */

/** `"2.10.1"` 같은 코드를 숫자 배열로 바꾼다(문자열 정렬이면 2.10 이 2.2 앞에 온다). */
export function codeSortKey(code: string): number[] {
  return code.split(".").map((part) => {
    const value = Number.parseInt(part, 10);
    return Number.isNaN(value) ? 0 : value;
  });
}

/** 숫자 배열 두 개를 앞자리부터 비교한다. */
function compareCodeKeys(left: number[], right: number[]): number {
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const diff = (left[index] ?? 0) - (right[index] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

/** 코드 문자열끼리 번호 순으로 비교한다. */
function compareCodes(left: string, right: string): number {
  const diff = compareCodeKeys(codeSortKey(left), codeSortKey(right));
  return diff !== 0 ? diff : left.localeCompare(right);
}

/** 항목 코드에서 장 번호를 뽑는다. 형식이 다르면 null. */
function chapterOfCode(code: string): number | null {
  const head = Number.parseInt(code.split(".")[0] ?? "", 10);
  return Number.isNaN(head) ? null : head;
}

/** `"sec:2.5"` 에서 절 번호 `"2.5"` 를 꺼낸다. */
function sectionNumberOf(sectionId: string): string {
  return sectionId.startsWith("sec:") ? sectionId.slice(4) : sectionId;
}

/* ------------------------------------------------------------------ */
/* 타입 좁히기                                                          */
/* ------------------------------------------------------------------ */

function isChapter(node: ProjectGraphNode): node is ChapterGraphNode {
  return node.type === "chapter";
}

function isSection(node: ProjectGraphNode): node is SectionGraphNode {
  return node.type === "section";
}

function isCriterion(node: ProjectGraphNode): node is CriterionGraphNode {
  return node.type === "criterion";
}

/**
 * Cytoscape 스타일 셀렉터가 쓸 `data(status)` 값.
 *
 * 항목은 판정, 문서는 파싱 상태, 증적은 점검 결과다. 세 값의 도메인이 겹치므로
 * (`unknown` 은 항목과 증적 양쪽에 있다) 스타일 셀렉터는 항상 `type` 과 함께 건다.
 */
function nodeStatus(node: ProjectGraphNode): string | undefined {
  switch (node.type) {
    case "criterion":
      return node.finding?.status;
    case "document":
      return node.status;
    case "evidence":
      return node.status;
    default:
      return undefined;
  }
}

/* ------------------------------------------------------------------ */
/* 좌표 계산                                                            */
/* ------------------------------------------------------------------ */

/**
 * 노드별 preset 좌표를 만든다.
 *
 * 리프(항목·문서·증적·알림)만 좌표를 갖는다. compound 부모인 장·절은 자식들의
 * 바운딩 박스로 Cytoscape 가 알아서 잡는다.
 */
export function computePositions(graph: ProjectGraph): Map<string, GraphPosition> {
  const positions = new Map<string, GraphPosition>();

  const chapters = graph.nodes.filter(isChapter).sort((a, b) => a.chapter - b.chapter);
  const sections = graph.nodes.filter(isSection);
  const criteria = graph.nodes.filter(isCriterion);

  // 절 id → 항목(코드순)
  const criteriaBySection = new Map<string, CriterionGraphNode[]>();
  for (const node of criteria) {
    const bucket = criteriaBySection.get(node.parent_id);
    if (bucket) bucket.push(node);
    else criteriaBySection.set(node.parent_id, [node]);
  }
  criteriaBySection.forEach((bucket) => {
    bucket.sort((a, b) => compareCodes(a.code, b.code));
  });

  // 장 id → 절(절 번호순)
  const sectionsByChapter = new Map<string, SectionGraphNode[]>();
  for (const node of sections) {
    const bucket = sectionsByChapter.get(node.parent_id);
    if (bucket) bucket.push(node);
    else sectionsByChapter.set(node.parent_id, [node]);
  }
  sectionsByChapter.forEach((bucket) => {
    bucket.sort((a, b) => compareCodes(sectionNumberOf(a.id), sectionNumberOf(b.id)));
  });

  // ① 장 밴드를 세로로 쌓으면서 절·항목 좌표를 채운다.
  let bandTop = 0;
  let bandWidth = 0;
  for (const chapter of chapters) {
    const list = sectionsByChapter.get(chapter.id) ?? [];
    let rowTop = bandTop;
    let rowHeight = 0;

    list.forEach((section, index) => {
      const col = index % SECTIONS_PER_ROW;
      if (col === 0 && index > 0) {
        // 줄이 바뀌면 방금 줄에서 가장 높았던 절만큼 내려간다.
        rowTop += rowHeight + SECTION_GAP;
        rowHeight = 0;
      }

      const items = criteriaBySection.get(section.id) ?? [];
      const rows = Math.max(1, Math.ceil(items.length / CRITERIA_COLS));
      rowHeight = Math.max(rowHeight, rows * CELL_H);

      const originX = col * SECTION_PITCH;
      items.forEach((item, order) => {
        positions.set(item.id, {
          x: originX + (order % CRITERIA_COLS) * CELL_W + CELL_W / 2,
          y: rowTop + Math.floor(order / CRITERIA_COLS) * CELL_H + CELL_H / 2,
        });
      });

      bandWidth = Math.max(bandWidth, originX + SECTION_W);
    });

    bandTop = rowTop + rowHeight + BAND_GAP;
  }

  if (bandWidth === 0) {
    // 항목이 하나도 없어도 레일 좌표는 정해져야 한다.
    bandWidth = SECTION_PITCH * SECTIONS_PER_ROW - SECTION_GAP;
  }

  // ② 레일에 놓을 노드는 "연결된 항목들의 평균 y" 순으로 세운다(엣지 교차를 줄인다).
  const anchors = new Map<string, number[]>();
  const addAnchor = (nodeId: string, criterionId: string) => {
    const point = positions.get(criterionId);
    if (!point) return;
    const bucket = anchors.get(nodeId);
    if (bucket) bucket.push(point.y);
    else anchors.set(nodeId, [point.y]);
  };
  for (const edge of graph.edges) {
    if (edge.type === "cites_document") addAnchor(edge.target, edge.source);
    else if (edge.type === "cites_evidence") addAnchor(edge.target, edge.source);
    else if (edge.type === "maps_to") addAnchor(edge.source, edge.target);
  }

  const documents = graph.nodes.filter((node) => node.type === "document");
  layoutRail(documents, -RAIL_OFFSET, anchors, positions);

  const evidence = graph.nodes.filter((node) => node.type === "evidence");
  const evidenceRailX = bandWidth + RAIL_OFFSET;
  const railBottom = layoutRail(evidence, evidenceRailX, anchors, positions);

  // ③ 알림은 자기가 가리키는 증적 옆에 붙인다. 대상이 없으면 증적 레일 아래로.
  const alerts = graph.nodes.filter((node) => node.type === "alert");
  const evidenceOfAlert = new Map<string, string>();
  for (const edge of graph.edges) {
    if (edge.type === "triggered") evidenceOfAlert.set(edge.source, edge.target);
  }
  const stacked = new Map<string, number>();
  let orphanY = railBottom + RAIL_STEP;
  for (const alert of alerts) {
    const anchorId = evidenceOfAlert.get(alert.id);
    const anchor = anchorId ? positions.get(anchorId) : undefined;
    if (anchor && anchorId) {
      const order = stacked.get(anchorId) ?? 0;
      stacked.set(anchorId, order + 1);
      positions.set(alert.id, {
        x: anchor.x + ALERT_OFFSET_X,
        y: anchor.y + order * ALERT_STACK_STEP,
      });
      continue;
    }
    positions.set(alert.id, { x: evidenceRailX + ALERT_OFFSET_X, y: orphanY });
    orphanY += RAIL_STEP;
  }

  return positions;
}

/**
 * 세로 레일 하나를 채우고 마지막 y 를 돌려준다.
 *
 * 연결된 노드(평균 y 순)를 먼저 세우고, 아무 항목과도 이어지지 않은 노드는 하단에 모은다.
 */
function layoutRail(
  nodes: ProjectGraphNode[],
  railX: number,
  anchors: Map<string, number[]>,
  positions: Map<string, GraphPosition>,
): number {
  const mean = (nodeId: string): number | null => {
    const values = anchors.get(nodeId);
    if (!values || values.length === 0) return null;
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  };

  const ordered = [...nodes].sort((a, b) => {
    const left = mean(a.id);
    const right = mean(b.id);
    // 미연결(null)은 항상 아래로 내린다.
    if (left === null && right !== null) return 1;
    if (left !== null && right === null) return -1;
    if (left !== null && right !== null && left !== right) return left - right;
    const byLabel = a.label.localeCompare(b.label, "ko");
    return byLabel !== 0 ? byLabel : a.id.localeCompare(b.id);
  });

  let lastY = 0;
  ordered.forEach((node, index) => {
    lastY = index * RAIL_STEP;
    positions.set(node.id, { x: railX, y: lastY });
  });
  return lastY;
}

/* ------------------------------------------------------------------ */
/* Cytoscape 입력                                                       */
/* ------------------------------------------------------------------ */

/**
 * 서버 응답을 Cytoscape `elements` 배열로 옮긴다.
 *
 * 스타일 셀렉터가 쓰는 값(`type`·`status`·`read`)은 노드 `data` 로 평탄화한다.
 * 계층은 `data.parent`(compound) 로만 표현하고 별도 엣지를 만들지 않는다.
 */
export function toElements(graph: ProjectGraph): ElementDefinition[] {
  const positions = computePositions(graph);

  const nodes: ElementDefinition[] = graph.nodes.map((node) => {
    const position = positions.get(node.id);
    return {
      group: "nodes",
      data: {
        id: node.id,
        type: node.type,
        label: node.label,
        parent: node.parent_id ?? undefined,
        code: node.type === "criterion" ? node.code : undefined,
        status: nodeStatus(node),
        read: node.type === "alert" ? node.read : undefined,
      },
      // 장·절은 compound 부모라 자식 바운딩 박스로 잡힌다(좌표를 주지 않는다).
      ...(position ? { position } : {}),
    };
  });

  const edges: ElementDefinition[] = graph.edges.map((edge) => ({
    group: "edges",
    data: {
      id: edge.id,
      type: edge.type,
      source: edge.source,
      target: edge.target,
    },
  }));

  return [...nodes, ...edges];
}

/* ------------------------------------------------------------------ */
/* 필터                                                                 */
/* ------------------------------------------------------------------ */

/**
 * 현재 필터에서 숨겨야 할 노드 id 집합.
 *
 * 자식이 전부 숨은 절·장 compound 도 함께 숨긴다(빈 상자만 남으면 지저분하다).
 * Cytoscape 는 `display: none` 인 노드에 붙은 엣지도 함께 감추므로 엣지는 계산하지 않는다.
 */
export function hiddenNodeIds(
  graph: ProjectGraph,
  filters: GraphFilters,
): Set<string> {
  const hidden = new Set<string>();

  // ① 항목: 장 필터 → 판정 상태 필터 순으로 거른다.
  const visibleInSection = new Map<string, number>();
  for (const node of graph.nodes) {
    if (!isCriterion(node)) continue;

    let hide = false;
    if (filters.chapter !== "all" && chapterOfCode(node.code) !== filters.chapter) {
      hide = true;
    }
    if (!hide && filters.statuses.length > 0) {
      const status = node.finding?.status;
      // 판정이 없는 항목은 어떤 상태에도 해당하지 않으므로 숨는다.
      if (!status || !filters.statuses.includes(status)) hide = true;
    }

    if (hide) {
      hidden.add(node.id);
      continue;
    }
    visibleInSection.set(
      node.parent_id,
      (visibleInSection.get(node.parent_id) ?? 0) + 1,
    );
  }

  // ② 절: 살아남은 항목이 하나도 없으면 숨긴다.
  const visibleInChapter = new Map<string, number>();
  for (const node of graph.nodes) {
    if (!isSection(node)) continue;
    if ((visibleInSection.get(node.id) ?? 0) === 0) {
      hidden.add(node.id);
      continue;
    }
    visibleInChapter.set(
      node.parent_id,
      (visibleInChapter.get(node.parent_id) ?? 0) + 1,
    );
  }

  // ③ 장: 살아남은 절이 하나도 없으면 숨긴다.
  for (const node of graph.nodes) {
    if (!isChapter(node)) continue;
    if ((visibleInChapter.get(node.id) ?? 0) === 0) hidden.add(node.id);
  }

  // ④ 노드 종류 토글. 알림은 증적에 매달린 노드라 증적을 끄면 같이 사라진다.
  for (const node of graph.nodes) {
    if (!filters.showDocuments && node.type === "document") hidden.add(node.id);
    if (!filters.showEvidence && (node.type === "evidence" || node.type === "alert")) {
      hidden.add(node.id);
    }
  }

  return hidden;
}

/** 통계 헤더에 쓰는 수치. 모두 현재 필터를 반영한 값이다. */
export interface GraphStats {
  /** 보이는 항목 수. */
  criteria: number;
  /** 전체 항목 수(분모, 보통 101). */
  criteriaTotal: number;
  documents: number;
  evidence: number;
  alerts: number;
  /** 양 끝이 모두 보이는 엣지 수. */
  edges: number;
  /** 보이는 항목의 판정 분포. 판정이 없는 항목은 어디에도 세지 않는다. */
  met: number;
  partial: number;
  unmet: number;
  unknown: number;
}

/** 필터 적용 후 화면에 실제로 보이는 것들을 센다. */
export function computeGraphStats(
  graph: ProjectGraph,
  hidden: Set<string>,
): GraphStats {
  const stats: GraphStats = {
    criteria: 0,
    criteriaTotal: 0,
    documents: 0,
    evidence: 0,
    alerts: 0,
    edges: 0,
    met: 0,
    partial: 0,
    unmet: 0,
    unknown: 0,
  };

  for (const node of graph.nodes) {
    if (node.type === "criterion") stats.criteriaTotal += 1;
    if (hidden.has(node.id)) continue;

    switch (node.type) {
      case "criterion": {
        stats.criteria += 1;
        const status = node.finding?.status;
        if (status) stats[status] += 1;
        break;
      }
      case "document":
        stats.documents += 1;
        break;
      case "evidence":
        stats.evidence += 1;
        break;
      case "alert":
        stats.alerts += 1;
        break;
      default:
        break;
    }
  }

  for (const edge of graph.edges) {
    if (!hidden.has(edge.source) && !hidden.has(edge.target)) stats.edges += 1;
  }

  return stats;
}

/** 엣지 종류별 한국어 문구. 범례와 정보 패널이 같이 쓴다. */
export const EDGE_TYPE_LABELS: Record<ProjectGraphEdge["type"], string> = {
  cites_document: "문서 근거 인용",
  cites_evidence: "증적 근거 인용",
  maps_to: "점검 항목 매핑",
  triggered: "알림 발생",
};
