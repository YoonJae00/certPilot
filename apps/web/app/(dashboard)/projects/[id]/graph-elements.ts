/**
 * 지식 그래프의 순수 계산부 — 서버 응답을 방사형(radial) 좌표와 Cytoscape 입력으로 옮긴다.
 *
 * 렌더링·DOM 은 `graph-canvas.tsx`, 장식 언더레이는 `graph-underlay.tsx` 가 맡는다.
 * 이 파일은 cytoscape 를 타입으로만 참조하는 순수 모듈이라 `"use client"` 가 필요 없다.
 *
 * ## 배치 모델
 * 데이터의 본질은 "계층(장>절>항목) × 근거(문서·증적) 이분 연결"이다. 그래서
 * 격자 대신 하나의 큰 링을 쓴다. 모든 값은 결정적으로 계산한다(Math.random·Date 금지).
 *
 *   · 항목 101개 : 반지름 R 링 위. 12시(-90°)에서 시작해 시계방향, 코드순.
 *                  항목마다 `itemStep`, 절이 바뀌면 +4°, 장이 바뀌면 +16° 를 더 벌린다.
 *                  `itemStep` 은 남은 각도를 항목 수로 나눈 값이라 링이 정확히 닫힌다.
 *   · 문서 12개  : 링 안쪽 0.42R. 각도는 그 문서를 인용한 항목들 각도의 원형 평균.
 *   · 증적 10개  : 링 바깥 1.24R. 각도는 매핑된 항목들 각도의 원형 평균.
 *   · 알림       : 자기 증적과 같은 각도, 0.08R 만큼 더 바깥.
 *
 * 각도 규약: 도(deg) 단위. -90 이 12시, 값이 커지면 화면상 시계방향
 * (SVG·Cytoscape 는 y 가 아래로 자라므로 `x = r·cos`, `y = r·sin` 이면 그렇게 된다).
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
/* 기하 상수 (모델 좌표. 중심은 항상 0,0)                                */
/* ------------------------------------------------------------------ */

/** 항목 링의 반지름. 다른 반지름은 전부 이 값의 배수로 정한다. */
export const RING_RADIUS = 400;
/** 문서 링(안쪽). */
export const DOCUMENT_RADIUS = RING_RADIUS * 0.42;
/** 증적 링(바깥). */
export const EVIDENCE_RADIUS = RING_RADIUS * 1.24;
/** 알림 링. 자기 증적보다 0.08R 만큼 더 바깥. */
export const ALERT_RADIUS = EVIDENCE_RADIUS + RING_RADIUS * 0.08;

/** 장 밴드(고리 조각)의 안·바깥 반지름. */
export const CHAPTER_BAND_INNER = RING_RADIUS + 18;
export const CHAPTER_BAND_OUTER = RING_RADIUS + 34;
/** 절 경계 틱 선의 안·바깥 반지름. */
export const SECTION_TICK_INNER = RING_RADIUS - 8;
export const SECTION_TICK_OUTER = RING_RADIUS + 10;
/** 절 번호 텍스트 반지름(장 밴드 위에 얹힌다). */
export const SECTION_LABEL_RADIUS = RING_RADIUS * 1.06;
/** 장 라벨 텍스트 반지름(밴드 바로 바깥). */
export const CHAPTER_LABEL_RADIUS = RING_RADIUS * 1.17;
/** 중앙 도넛의 중심선 반지름과 두께. */
export const DONUT_RADIUS = RING_RADIUS * 0.22;
export const DONUT_THICKNESS = 14;

/** 시작 각(12시). */
const START_ANGLE_DEG = -90;
/** 장이 바뀔 때 벌리는 각. */
const CHAPTER_GAP_DEG = 16;
/** 절이 바뀔 때 벌리는 각. */
const SECTION_GAP_DEG = 4;
/**
 * 문서·증적이 서로 겹치지 않게 보장하는 최소 간격.
 *
 * 노드끼리는 훨씬 좁은 각도로도 안 겹치지만, 항상 켜 두는 라벨이 문제다. 문서 링은
 * 반지름이 작아(0.42R) 같은 각도라도 호 길이가 짧으므로 증적보다 더 크게 벌린다.
 */
const DOCUMENT_MIN_GAP_DEG = 24;
const EVIDENCE_MIN_GAP_DEG = 15;
/** 연결이 없는 노드를 모아 두는 방향(6시). */
const ORPHAN_ANGLE_DEG = 90;

/**
 * 라벨을 노드 바깥(중심 반대편)으로 밀어내는 거리.
 *
 * 링 위에서는 라벨이 안쪽으로 들어가면 곧바로 엣지·이웃 노드와 겹친다.
 * Cytoscape 의 `text-margin-x/y` 에 노드별로 실어 보낸다.
 */
const CRITERION_LABEL_OFFSET = 15;
const DOCUMENT_LABEL_OFFSET = 27;
const EVIDENCE_LABEL_OFFSET = 21;
/**
 * 라벨 층 간격.
 *
 * 이웃한 문서·증적의 라벨을 한 칸씩 번갈아 더 밀어내 두 겹으로 만든다. 각도만 벌려서는
 * 원 아래쪽처럼 라벨이 가로로 나란히 서는 구간을 풀 수 없다.
 */
const LABEL_TIER_STEP = 38;

const DEG = Math.PI / 180;

/** 화면 좌표 1개. */
export interface GraphPosition {
  x: number;
  y: number;
}

/* ------------------------------------------------------------------ */
/* 각도 도우미                                                          */
/* ------------------------------------------------------------------ */

/** 각도(도)와 반지름으로 모델 좌표를 만든다. */
export function pointOnCircle(angleDeg: number, radius: number): GraphPosition {
  const rad = angleDeg * DEG;
  return { x: radius * Math.cos(rad), y: radius * Math.sin(rad) };
}

/** 각도를 [0, 360) 으로 접는다. */
function normalizeDeg(value: number): number {
  const wrapped = value % 360;
  return wrapped < 0 ? wrapped + 360 : wrapped;
}

/**
 * 각도들의 원형 평균(단위벡터 합의 atan2).
 *
 * 산술 평균을 쓰면 350° 와 10° 의 평균이 180° 가 되어 정반대에 놓인다.
 * 합 벡터가 0 에 가까우면(정확히 마주 보는 두 각 등) 평균이 의미 없으므로 null.
 */
function circularMeanDeg(angles: number[]): number | null {
  if (angles.length === 0) return null;
  let sumX = 0;
  let sumY = 0;
  for (const angle of angles) {
    sumX += Math.cos(angle * DEG);
    sumY += Math.sin(angle * DEG);
  }
  if (Math.hypot(sumX, sumY) < 1e-9) return null;
  return Math.atan2(sumY, sumX) / DEG;
}

/**
 * 각도들이 최소 간격을 지키도록 앞(시계방향)으로 밀어낸다.
 *
 * 원 위라 단순 정렬 후 밀기만 하면 마지막 항목이 첫 항목을 덮칠 수 있다. 그래서
 * "가장 넓게 비어 있는 구간" 다음부터 밀기 시작해 여유를 최대로 쓴다. 입력 순서에
 * 의존하지 않도록 같은 각이면 id 로 갈라 정렬한다(결정적).
 */
function spreadAngles(
  entries: { id: string; angle: number }[],
  minGapDeg: number,
): Map<string, number> {
  const result = new Map<string, number>();
  const count = entries.length;
  if (count === 0) return result;

  const sorted = entries
    .map((entry) => ({ id: entry.id, angle: normalizeDeg(entry.angle) }))
    .sort((a, b) => a.angle - b.angle || a.id.localeCompare(b.id));

  if (count === 1) {
    result.set(sorted[0].id, sorted[0].angle);
    return result;
  }

  // 가장 넓은 빈 구간 바로 다음 항목을 기준점으로 삼는다.
  let startIndex = 0;
  let widestGap = -1;
  for (let index = 0; index < count; index += 1) {
    const next = sorted[(index + 1) % count];
    const gap = normalizeDeg(next.angle - sorted[index].angle);
    if (gap > widestGap) {
      widestGap = gap;
      startIndex = (index + 1) % count;
    }
  }

  // 기준점부터 한 바퀴 도는 동안 각도를 단조 증가로 펴 둔다.
  // (밀린 이전 값과 원래 값을 그때그때 [0,360) 으로 비교하면, 이미 지나친 항목을
  //  "거의 한 바퀴 뒤"로 읽어 버려 링을 몇 바퀴씩 감는다.)
  let base = 0;
  let previous = Number.NEGATIVE_INFINITY;
  for (let step = 0; step < count; step += 1) {
    const index = (startIndex + step) % count;
    // 배열 끝에서 처음으로 되돌아오는 순간(정확히 한 번) 한 바퀴를 더한다.
    if (step > 0 && index === 0) base += 360;
    const item = sorted[index];
    // 이전 항목 기준으로 앞으로만 민다(뒤로 당기면 이미 배치한 것과 부딪힌다).
    const angle = Math.max(item.angle + base, previous + minGapDeg);
    result.set(item.id, angle);
    previous = angle;
  }

  return result;
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
/* 방사형 배치                                                          */
/* ------------------------------------------------------------------ */

/** 링 위의 한 구간(장 또는 절). 언더레이가 밴드·라벨·틱을 그릴 때 쓴다. */
export interface RingArc {
  /** 노드 id(`ch:2` / `sec:2.5`). */
  id: string;
  /** 표시 문자열. 장은 번호, 절은 절 번호(`"2.5"`). */
  key: string;
  /** 이 구간이 속한 장 번호. */
  chapter: number;
  startDeg: number;
  endDeg: number;
  midDeg: number;
  /** 이 구간이 품은 항목 수. */
  count: number;
}

/** 방사형 배치 결과. 캔버스와 언더레이가 같은 값을 나눠 쓴다. */
export interface RadialLayout {
  /** 리프 노드(항목·문서·증적·알림)의 모델 좌표. */
  positions: Map<string, GraphPosition>;
  /** 항목 id → 링 위 각도(도). */
  criterionAngles: Map<string, number>;
  chapters: RingArc[];
  sections: RingArc[];
  /** 문서·증적 id → 라벨 층(0 또는 1). 이웃끼리 번갈아 두 겹으로 세운다. */
  labelTiers: Map<string, number>;
  /** `cites_document` 엣지 id → 곡률(부호 있는 control-point-distance). */
  bows: Map<string, number>;
}

/** 각 구간의 각도 범위를 누적할 때 쓰는 임시 값. */
interface ArcAccumulator {
  chapter: number;
  start: number;
  end: number;
  count: number;
}

/**
 * 그래프 전체의 방사형 좌표를 계산한다.
 *
 * 장·절은 좌표를 갖지 않는다(Cytoscape 노드로 내리지 않고 언더레이가 그린다).
 */
export function computeRadialLayout(graph: ProjectGraph): RadialLayout {
  const positions = new Map<string, GraphPosition>();
  const criterionAngles = new Map<string, number>();
  const labelTiers = new Map<string, number>();
  const bows = new Map<string, number>();

  const criteria = graph.nodes
    .filter(isCriterion)
    .sort((a, b) => compareCodes(a.code, b.code));

  // ① 링을 몇 조각으로 나눌지 먼저 센다. 장 갭·절 갭을 빼고 남은 각을 항목이 나눠 갖는다.
  const chapterKeys: number[] = [];
  const sectionKeys: string[] = [];
  for (const item of criteria) {
    const chapter = chapterOfCode(item.code) ?? 0;
    if (!chapterKeys.includes(chapter)) chapterKeys.push(chapter);
    if (!sectionKeys.includes(item.parent_id)) sectionKeys.push(item.parent_id);
  }
  const innerSectionBoundaries = Math.max(0, sectionKeys.length - chapterKeys.length);
  const totalGapDeg =
    chapterKeys.length * CHAPTER_GAP_DEG + innerSectionBoundaries * SECTION_GAP_DEG;
  const itemStepDeg =
    criteria.length > 0 ? (360 - totalGapDeg) / criteria.length : 0;

  // ② 커서를 돌리며 항목을 놓고, 장·절 구간의 각도 범위를 함께 적는다.
  const chapterArcs = new Map<string, ArcAccumulator>();
  const sectionArcs = new Map<string, ArcAccumulator>();
  let cursor = START_ANGLE_DEG;
  let previousChapter: number | null = null;
  let previousSection: string | null = null;

  for (const item of criteria) {
    const chapter = chapterOfCode(item.code) ?? 0;
    const sectionId = item.parent_id;

    if (previousChapter === null || chapter !== previousChapter) {
      cursor += CHAPTER_GAP_DEG;
    } else if (sectionId !== previousSection) {
      cursor += SECTION_GAP_DEG;
    }

    criterionAngles.set(item.id, cursor);
    positions.set(item.id, pointOnCircle(cursor, RING_RADIUS));

    const slotStart = cursor - itemStepDeg / 2;
    const slotEnd = cursor + itemStepDeg / 2;
    extendArc(sectionArcs, sectionId, chapter, slotStart, slotEnd);
    extendArc(chapterArcs, `ch:${chapter}`, chapter, slotStart, slotEnd);

    cursor += itemStepDeg;
    previousChapter = chapter;
    previousSection = sectionId;
  }

  // ③ 문서: 인용한 항목들의 원형 평균 각도. 인용이 없으면 아래쪽에 모은다.
  const documentAngleSources = new Map<string, number[]>();
  const evidenceMappedAngles = new Map<string, number[]>();
  const evidenceCitedAngles = new Map<string, number[]>();
  const alertTargets = new Map<string, string>();

  for (const edge of graph.edges) {
    switch (edge.type) {
      case "cites_document":
        pushAngle(documentAngleSources, edge.target, criterionAngles.get(edge.source));
        break;
      case "cites_evidence":
        pushAngle(evidenceCitedAngles, edge.target, criterionAngles.get(edge.source));
        break;
      case "maps_to":
        pushAngle(evidenceMappedAngles, edge.source, criterionAngles.get(edge.target));
        break;
      case "triggered":
        alertTargets.set(edge.source, edge.target);
        break;
      default:
        break;
    }
  }

  const documents = graph.nodes.filter((node) => node.type === "document");
  const documentAngles = spreadAngles(
    documents.map((node) => ({
      id: node.id,
      angle: circularMeanDeg(documentAngleSources.get(node.id) ?? []) ?? ORPHAN_ANGLE_DEG,
    })),
    DOCUMENT_MIN_GAP_DEG,
  );
  for (const node of documents) {
    const angle = documentAngles.get(node.id) ?? ORPHAN_ANGLE_DEG;
    positions.set(node.id, pointOnCircle(angle, DOCUMENT_RADIUS));
  }
  assignLabelTiers(documentAngles, labelTiers);

  // ④ 증적: 점검 매핑(maps_to) 우선, 없으면 근거 인용, 그것도 없으면 아래쪽.
  const evidence = graph.nodes.filter((node) => node.type === "evidence");
  const evidenceAngles = spreadAngles(
    evidence.map((node) => ({
      id: node.id,
      angle:
        circularMeanDeg(evidenceMappedAngles.get(node.id) ?? []) ??
        circularMeanDeg(evidenceCitedAngles.get(node.id) ?? []) ??
        ORPHAN_ANGLE_DEG,
    })),
    EVIDENCE_MIN_GAP_DEG,
  );
  for (const node of evidence) {
    const angle = evidenceAngles.get(node.id) ?? ORPHAN_ANGLE_DEG;
    positions.set(node.id, pointOnCircle(angle, EVIDENCE_RADIUS));
  }
  assignLabelTiers(evidenceAngles, labelTiers);

  // ⑤ 알림: 자기 증적과 같은 각도에서 한 겹 더 바깥. 대상이 없으면 증적 배치의 꼬리에 붙인다.
  const alerts = graph.nodes.filter((node) => node.type === "alert");
  let orphanAlertAngle = maxAngle(evidenceAngles) ?? ORPHAN_ANGLE_DEG;
  for (const alert of alerts) {
    const targetId = alertTargets.get(alert.id);
    const angle = targetId ? evidenceAngles.get(targetId) : undefined;
    if (angle !== undefined) {
      positions.set(alert.id, pointOnCircle(angle, ALERT_RADIUS));
      continue;
    }
    orphanAlertAngle += EVIDENCE_MIN_GAP_DEG;
    positions.set(alert.id, pointOnCircle(orphanAlertAngle, ALERT_RADIUS));
  }

  // ⑥ 근거 인용 곡선의 곡률. 항상 중심 쪽으로 휘게 부호를 고른다.
  for (const edge of graph.edges) {
    if (edge.type !== "cites_document") continue;
    const bow = bowForChord(positions.get(edge.source), positions.get(edge.target));
    if (bow !== null) bows.set(edge.id, bow);
  }

  return {
    positions,
    criterionAngles,
    chapters: toRingArcs(chapterArcs, (id) => id.replace(/^ch:/, "")),
    sections: toRingArcs(sectionArcs, sectionNumberOf),
    labelTiers,
    bows,
  };
}

/** 각도 순서대로 라벨 층을 0·1 로 번갈아 매긴다. */
function assignLabelTiers(
  angles: Map<string, number>,
  target: Map<string, number>,
): void {
  Array.from(angles.entries())
    .sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]))
    .forEach(([id], index) => target.set(id, index % 2));
}

/** 구간 누적기에 항목 슬롯 하나를 반영한다. */
function extendArc(
  target: Map<string, ArcAccumulator>,
  id: string,
  chapter: number,
  start: number,
  end: number,
): void {
  const current = target.get(id);
  if (current) {
    current.start = Math.min(current.start, start);
    current.end = Math.max(current.end, end);
    current.count += 1;
    return;
  }
  target.set(id, { chapter, start, end, count: 1 });
}

/** 누적기를 표시용 구간 배열로 바꾼다(시작 각도순). */
function toRingArcs(
  source: Map<string, ArcAccumulator>,
  keyOf: (id: string) => string,
): RingArc[] {
  return Array.from(source.entries())
    .map(([id, arc]) => ({
      id,
      key: keyOf(id),
      chapter: arc.chapter,
      startDeg: arc.start,
      endDeg: arc.end,
      midDeg: (arc.start + arc.end) / 2,
      count: arc.count,
    }))
    .sort((a, b) => a.startDeg - b.startDeg);
}

/** 값이 있을 때만 버킷에 각도를 담는다. */
function pushAngle(
  target: Map<string, number[]>,
  key: string,
  angle: number | undefined,
): void {
  if (angle === undefined) return;
  const bucket = target.get(key);
  if (bucket) bucket.push(angle);
  else target.set(key, [angle]);
}

/** 배치된 각도 중 가장 큰 값. 비어 있으면 null. */
function maxAngle(angles: Map<string, number>): number | null {
  const values = Array.from(angles.values());
  return values.length === 0 ? null : Math.max(...values);
}

/**
 * 현(source→target)이 중심 쪽으로 휘도록 control-point-distance 를 계산한다.
 *
 * Cytoscape 는 제어점을 `중점 + n · d` 로 잡고, `n = (-dy, dx) / |v|` 다
 * (`edge-control-points.mjs`). 그래서 `n` 이 중심을 향하는지 보고 부호만 고르면 된다.
 * 크기는 현 길이의 25%, 상한 0.35R — 짧은 현은 살짝, 긴 현은 넉넉히 휜다.
 */
function bowForChord(
  source: GraphPosition | undefined,
  target: GraphPosition | undefined,
): number | null {
  if (!source || !target) return null;
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const length = Math.hypot(dx, dy);
  if (length < 1e-6) return null;

  const midX = (source.x + target.x) / 2;
  const midY = (source.y + target.y) / 2;
  const midLength = Math.hypot(midX, midY);
  if (midLength < 1e-6) return null;

  const normalX = -dy / length;
  const normalY = dx / length;
  // 중점에서 중심(0,0)으로 향하는 단위벡터와의 내적 부호.
  const towardCenter = (normalX * -midX + normalY * -midY) / midLength;
  const magnitude = Math.min(length * 0.25, RING_RADIUS * 0.35);
  return towardCenter >= 0 ? magnitude : -magnitude;
}

/* ------------------------------------------------------------------ */
/* 라벨 다듬기                                                          */
/* ------------------------------------------------------------------ */

/** 라벨을 지정 글자 수로 자르고 말줄임표를 붙인다. */
export function shortenLabel(value: string, max = 12): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

/** 파일명에서 확장자를 떼고 줄인다(`01_정보보호정책_v2.1.pdf` → `01_정보보호정책…`). */
export function documentShortLabel(value: string, max = 12): string {
  return shortenLabel(value.replace(/\.[A-Za-z0-9]{1,5}$/, ""), max);
}

/* ------------------------------------------------------------------ */
/* Cytoscape 입력                                                       */
/* ------------------------------------------------------------------ */

/**
 * 서버 응답을 Cytoscape `elements` 배열로 옮긴다.
 *
 * 장·절 노드는 넣지 않는다. 계층은 링의 갭·밴드(언더레이 SVG)로 보여 주는 편이
 * compound 상자보다 훨씬 조용하다.
 *
 * 스타일 셀렉터가 쓰는 값(`type`·`status`·`read`)과 배치가 계산한 값(`tmx`·`tmy`·`bow`)은
 * 모두 `data` 로 평탄화한다.
 */
export function toElements(
  graph: ProjectGraph,
  layout: RadialLayout,
): ElementDefinition[] {
  const nodes: ElementDefinition[] = [];

  for (const node of graph.nodes) {
    if (isChapter(node) || isSection(node)) continue;
    const position = layout.positions.get(node.id);
    if (!position) continue;

    const offset =
      labelOffsetOf(node.type) + (layout.labelTiers.get(node.id) ?? 0) * LABEL_TIER_STEP;
    const anchor = labelAnchor(position, offset);

    nodes.push({
      group: "nodes",
      data: {
        id: node.id,
        type: node.type,
        label: node.label,
        short:
          node.type === "document"
            ? documentShortLabel(node.label, 11)
            : shortenLabel(node.label, 12),
        code: node.type === "criterion" ? node.code : undefined,
        status: nodeStatus(node),
        read: node.type === "alert" ? node.read : undefined,
        halign: anchor.halign,
        valign: anchor.valign,
        tmx: anchor.marginX,
        tmy: anchor.marginY,
      },
      position,
    });
  }

  const edges: ElementDefinition[] = graph.edges.map((edge) => ({
    group: "edges",
    data: {
      id: edge.id,
      type: edge.type,
      source: edge.source,
      target: edge.target,
      bow: layout.bows.get(edge.id) ?? 0,
    },
  }));

  return [...nodes, ...edges];
}

/** 라벨을 노드의 어느 쪽에 어떻게 붙일지. Cytoscape 스타일이 그대로 읽어 간다. */
export interface LabelAnchor {
  halign: "left" | "center" | "right";
  valign: "top" | "center" | "bottom";
  marginX: number;
  marginY: number;
}

/**
 * 라벨을 중심 반대편(바깥)으로 붙인다.
 *
 * 좌우로 나갈 때는 정렬까지 바깥으로 돌리는 게 핵심이다. 가운데 정렬로 두면 글자의
 * 절반이 노드 안쪽으로 되돌아와 이웃 노드를 덮는다(문서 링처럼 촘촘한 곳에서 치명적).
 */
function labelAnchor(position: GraphPosition, offset: number): LabelAnchor {
  const radius = Math.hypot(position.x, position.y);
  // 원점에 놓인 노드는 방향이 없으므로 아래로 내린다.
  const unitX = radius > 1e-6 ? position.x / radius : 0;
  const unitY = radius > 1e-6 ? position.y / radius : 1;

  // 가로로 충분히 기울었으면 좌우로, 아니면 위아래로 뺀다.
  if (Math.abs(unitX) > 0.38) {
    return unitX > 0
      ? { halign: "right", valign: "center", marginX: offset, marginY: 0 }
      : { halign: "left", valign: "center", marginX: -offset, marginY: 0 };
  }
  return unitY > 0
    ? { halign: "center", valign: "bottom", marginX: 0, marginY: offset }
    : { halign: "center", valign: "top", marginX: 0, marginY: -offset };
}

/** 노드 종류별 라벨 바깥 밀기 거리. */
function labelOffsetOf(type: ProjectGraphNode["type"]): number {
  switch (type) {
    case "document":
      return DOCUMENT_LABEL_OFFSET;
    case "evidence":
      return EVIDENCE_LABEL_OFFSET;
    case "criterion":
      return CRITERION_LABEL_OFFSET;
    default:
      return 0;
  }
}

/* ------------------------------------------------------------------ */
/* 이웃 색인 (정보 카드)                                                */
/* ------------------------------------------------------------------ */

/** 정보 카드가 "무엇과 이어져 있는지" 보여 줄 때 쓰는 색인. */
export interface GraphNeighborIndex {
  /** 항목 id → 인용한 문서 이름. */
  criterionDocuments: Map<string, string[]>;
  /** 항목 id → 인용한 증적 이름. */
  criterionEvidence: Map<string, string[]>;
  /** 문서 id → 이 문서를 인용한 항목 코드. */
  documentCriteria: Map<string, string[]>;
  /** 증적 id → 매핑되거나 이 증적을 인용한 항목 코드. */
  evidenceCriteria: Map<string, string[]>;
  /** 알림 id → 알림을 발생시킨 증적 이름. */
  alertEvidence: Map<string, string>;
}

/** 엣지를 훑어 정보 카드용 이웃 목록을 만든다. 목록은 항상 정렬해 둔다. */
export function buildNeighborIndex(graph: ProjectGraph): GraphNeighborIndex {
  const index: GraphNeighborIndex = {
    criterionDocuments: new Map(),
    criterionEvidence: new Map(),
    documentCriteria: new Map(),
    evidenceCriteria: new Map(),
    alertEvidence: new Map(),
  };

  const labelById = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const codeById = new Map(
    graph.nodes.filter(isCriterion).map((node) => [node.id, node.code]),
  );

  const push = (target: Map<string, string[]>, key: string, value?: string) => {
    if (!value) return;
    const bucket = target.get(key);
    if (bucket) {
      if (!bucket.includes(value)) bucket.push(value);
      return;
    }
    target.set(key, [value]);
  };

  for (const edge of graph.edges) {
    switch (edge.type) {
      case "cites_document":
        push(index.criterionDocuments, edge.source, labelById.get(edge.target));
        push(index.documentCriteria, edge.target, codeById.get(edge.source));
        break;
      case "cites_evidence":
        push(index.criterionEvidence, edge.source, labelById.get(edge.target));
        push(index.evidenceCriteria, edge.target, codeById.get(edge.source));
        break;
      case "maps_to":
        push(index.evidenceCriteria, edge.source, codeById.get(edge.target));
        break;
      case "triggered": {
        const label = labelById.get(edge.target);
        if (label) index.alertEvidence.set(edge.source, label);
        break;
      }
      default:
        break;
    }
  }

  index.criterionDocuments.forEach((list) => list.sort((a, b) => a.localeCompare(b, "ko")));
  index.criterionEvidence.forEach((list) => list.sort((a, b) => a.localeCompare(b, "ko")));
  index.documentCriteria.forEach((list) => list.sort(compareCodes));
  index.evidenceCriteria.forEach((list) => list.sort(compareCodes));

  return index;
}

/* ------------------------------------------------------------------ */
/* 필터                                                                 */
/* ------------------------------------------------------------------ */

/**
 * 현재 필터에서 숨겨야 할 노드 id 집합.
 *
 * 장·절은 Cytoscape 노드가 아니지만, 언더레이 감쇠와 통계가 같은 기준을 쓰도록
 * 여기서 함께 계산해 둔다. Cytoscape 는 `display: none` 인 노드에 붙은 엣지도 함께
 * 감추므로 엣지는 계산하지 않는다.
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
