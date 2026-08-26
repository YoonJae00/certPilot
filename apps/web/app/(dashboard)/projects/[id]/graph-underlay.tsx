"use client";

/**
 * 지식 그래프 언더레이 — Cytoscape 가 그릴 수 없는 구조 장식을 SVG 로 깐다.
 *
 * 캔버스 컨테이너 "아래"에 절대배치되고, 모델 좌표로 한 번만 그린 뒤
 * `translate(pan) scale(zoom)` 만 갈아 끼워 캔버스와 같은 좌표계를 유지한다
 * (Cytoscape 렌더 좌표 = 모델좌표 × zoom + pan).
 *
 * 그리는 것:
 *   · 장 밴드 : 링 바깥 고리 조각 3개. 장 경계에서 끊겨 장 구분이 한눈에 보인다.
 *   · 절 틱   : 절 사이 갭 한가운데의 짧은 선 + 밴드 위의 절 번호.
 *   · 중앙    : 판정 분포 도넛 + 충족 수.
 *
 * ## 글자 크기
 * 텍스트가 `scale(zoom)` 안에 들어가면 축소 배치(그래프 지름 ≈ 1,070 모델 단위)에서
 * 읽을 수 없게 된다. 그래서 루트 `<g>` 의 font-size 를 `기준px / zoom` 모델 단위로 두고
 * 자식은 전부 `em` 배수를 쓴다. 결과적으로 화면에서는 항상 같은 px 로 보인다.
 */

import * as React from "react";

import {
  CHAPTER_BAND_INNER,
  CHAPTER_BAND_OUTER,
  CHAPTER_LABEL_RADIUS,
  DONUT_RADIUS,
  DONUT_THICKNESS,
  type GraphStats,
  type RadialLayout,
  SECTION_LABEL_RADIUS,
  SECTION_TICK_INNER,
  SECTION_TICK_OUTER,
  pointOnCircle,
} from "@/app/(dashboard)/projects/[id]/graph-elements";
import { CHAPTER_SHORT_LABELS, FINDING_STATUS_ORDER } from "@/lib/labels";
import type { CriterionChapter, FindingStatus } from "@/lib/types";
import type { GraphFilters } from "@/lib/types-graph";
import { cn } from "@/lib/utils";

/** 루트 글자 크기 기준(px). 자식은 이 값의 `em` 배수를 쓴다. */
const BASE_FONT_PX = 12;
/**
 * 글자가 차지할 수 있는 모델 단위 상한.
 *
 * 화면 px 를 유지하려고 `기준px / zoom` 을 쓰면, 크게 축소된 배치(모바일)에서 글자가
 * 도형보다 커져 링과 도넛을 뚫고 나온다. 그래서 축소 쪽으로는 클램프를 건다.
 * 바깥 라벨(장·절)보다 중앙 텍스트를 더 조인다 — 도넛 안에 갇혀야 하기 때문이다.
 */
const MAX_RING_FONT_MODEL = 30;
const MAX_CENTER_FONT_MODEL = 22;
/** 절 번호를 감추는 임계값. 이보다 좁거나 축소돼 있으면 21개가 서로 부딪힌다. */
const SECTION_LABEL_MIN_WIDTH = 640;
const SECTION_LABEL_MIN_ZOOM = 0.45;
/** 도넛 호 사이 간격(모델 단위 ≈ 2px 상당). */
const DONUT_ARC_GAP_DEG = (2 / DONUT_RADIUS) * (180 / Math.PI);

/** 판정별 도넛 호 색(테마 토큰). */
const DONUT_ARC_FILL: Record<FindingStatus, string> = {
  met: "hsl(var(--success))",
  partial: "hsl(var(--warning))",
  unmet: "hsl(var(--destructive))",
  unknown: "hsl(var(--muted-foreground))",
};

/** 캔버스가 뷰포트를 옮길 때마다 부르는 조작. */
export interface GraphUnderlayHandle {
  /** Cytoscape 뷰포트를 그대로 옮겨 붙인다. */
  sync: (
    zoom: number,
    panX: number,
    panY: number,
    containerWidth: number,
  ) => void;
  /** 포커스가 잡히면 언더레이를 뒤로 물린다. */
  setFocused: (active: boolean) => void;
}

/** 뷰포트 상태. 리렌더가 일어나도 imperative 값이 날아가지 않게 ref 에 담아 둔다. */
interface ViewState {
  zoom: number;
  panX: number;
  panY: number;
  width: number;
}

/**
 * 고리 조각(annulus sector) 경로.
 *
 * 시작 각에서 끝 각까지 바깥 호를 시계방향으로 그린 뒤, 안쪽 호로 되돌아와 닫는다.
 */
function annulusPath(
  startDeg: number,
  endDeg: number,
  inner: number,
  outer: number,
): string {
  const sweep = endDeg - startDeg;
  const largeArc = Math.abs(sweep) > 180 ? 1 : 0;
  const outerStart = pointOnCircle(startDeg, outer);
  const outerEnd = pointOnCircle(endDeg, outer);
  const innerEnd = pointOnCircle(endDeg, inner);
  const innerStart = pointOnCircle(startDeg, inner);
  return [
    `M ${outerStart.x.toFixed(2)} ${outerStart.y.toFixed(2)}`,
    `A ${outer} ${outer} 0 ${largeArc} 1 ${outerEnd.x.toFixed(2)} ${outerEnd.y.toFixed(2)}`,
    `L ${innerEnd.x.toFixed(2)} ${innerEnd.y.toFixed(2)}`,
    `A ${inner} ${inner} 0 ${largeArc} 0 ${innerStart.x.toFixed(2)} ${innerStart.y.toFixed(2)}`,
    "Z",
  ].join(" ");
}

/** 중앙 도넛의 호 하나. */
interface DonutArc {
  status: FindingStatus;
  path: string;
}

/** 판정 분포를 도넛 호 4개로 만든다. 값이 0 인 상태는 건너뛴다. */
function donutArcs(stats: GraphStats): DonutArc[] {
  const total = FINDING_STATUS_ORDER.reduce(
    (sum, status) => sum + stats[status],
    0,
  );
  if (total === 0) return [];

  const inner = DONUT_RADIUS - DONUT_THICKNESS / 2;
  const outer = DONUT_RADIUS + DONUT_THICKNESS / 2;
  const arcs: DonutArc[] = [];
  let cursor = -90;

  for (const status of FINDING_STATUS_ORDER) {
    const count = stats[status];
    if (count === 0) continue;
    const sweep = (count / total) * 360;
    // 조각이 갭보다 얇으면 갭을 빼지 않는다(음수 스윕이 되면 경로가 뒤집힌다).
    const gap = sweep > DONUT_ARC_GAP_DEG * 2 ? DONUT_ARC_GAP_DEG : 0;
    arcs.push({
      status,
      path: annulusPath(
        cursor + gap / 2,
        cursor + sweep - gap / 2,
        inner,
        outer,
      ),
    });
    cursor += sweep;
  }

  return arcs;
}

export const GraphUnderlay = React.forwardRef<
  GraphUnderlayHandle,
  {
    layout: RadialLayout;
    stats: GraphStats;
    /** 완료된 모의심사가 있는지. 없으면 도넛 대신 안내 문구를 넣는다. */
    hasAssessment: boolean;
    /** 장 필터. 선택된 장 외의 밴드·라벨을 감쇠한다. */
    chapter: GraphFilters["chapter"];
    className?: string;
  }
>(function GraphUnderlay(
  { layout, stats, hasAssessment, chapter, className },
  ref,
) {
  const rootRef = React.useRef<SVGGElement | null>(null);
  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const sectionLabelRef = React.useRef<SVGGElement | null>(null);
  const centerRef = React.useRef<SVGGElement | null>(null);
  const viewRef = React.useRef<ViewState>({
    zoom: 1,
    panX: 0,
    panY: 0,
    width: 0,
  });

  /** ref 에 담긴 뷰포트를 DOM 에 반영한다. */
  const applyView = React.useCallback(() => {
    const { zoom, panX, panY, width } = viewRef.current;
    const root = rootRef.current;
    if (root) {
      root.setAttribute(
        "transform",
        `translate(${panX}, ${panY}) scale(${zoom})`,
      );
      // 화면 기준 px 를 유지하려고 모델 단위 글자 크기를 zoom 으로 되돌린다.
      root.style.fontSize = `${Math.min(BASE_FONT_PX / zoom, MAX_RING_FONT_MODEL)}px`;
    }
    const center = centerRef.current;
    if (center) {
      center.style.fontSize = `${Math.min(BASE_FONT_PX / zoom, MAX_CENTER_FONT_MODEL)}px`;
    }
    const labels = sectionLabelRef.current;
    if (labels) {
      const compact =
        width < SECTION_LABEL_MIN_WIDTH || zoom < SECTION_LABEL_MIN_ZOOM;
      labels.style.display = compact ? "none" : "";
    }
  }, []);

  // 필터·통계가 바뀌어 다시 그려져도 뷰포트는 유지돼야 한다.
  React.useLayoutEffect(applyView);

  React.useImperativeHandle(
    ref,
    () => ({
      sync: (zoom, panX, panY, width) => {
        viewRef.current = { zoom, panX, panY, width };
        applyView();
      },
      setFocused: (active) => {
        const svg = svgRef.current;
        if (svg) svg.style.opacity = active ? "0.35" : "1";
      },
    }),
    [applyView],
  );

  const arcs = React.useMemo(() => donutArcs(stats), [stats]);
  const donutInner = DONUT_RADIUS - DONUT_THICKNESS / 2;

  /** 장 필터가 걸린 동안 다른 장은 흐리게. */
  const chapterOpacity = (value: number): number =>
    chapter === "all" || chapter === value ? 1 : 0.18;

  return (
    <svg
      ref={svgRef}
      aria-hidden
      className={cn("size-full transition-opacity duration-200", className)}
      // viewBox 를 두지 않아야 SVG 사용자 단위 = CSS px 가 되어 pan/zoom 을 그대로 쓸 수 있다.
    >
      <g ref={rootRef} fontSize={`${BASE_FONT_PX}px`}>
        {/* 장 밴드 --------------------------------------------------- */}
        {layout.chapters.map((arc) => (
          <path
            key={`band-${arc.id}`}
            d={annulusPath(
              arc.startDeg,
              arc.endDeg,
              CHAPTER_BAND_INNER,
              CHAPTER_BAND_OUTER,
            )}
            fill="hsl(var(--muted-foreground))"
            fillOpacity={0.14 * chapterOpacity(arc.chapter)}
          />
        ))}

        {/* 절 경계 틱 ------------------------------------------------- */}
        {layout.sections.map((arc, index) => {
          const previous = layout.sections[index - 1];
          // 장이 바뀌는 자리는 밴드가 이미 끊겨 있으므로 틱을 그리지 않는다.
          if (!previous || previous.chapter !== arc.chapter) return null;
          const tickDeg = (previous.endDeg + arc.startDeg) / 2;
          const start = pointOnCircle(tickDeg, SECTION_TICK_INNER);
          const end = pointOnCircle(tickDeg, SECTION_TICK_OUTER);
          return (
            <line
              key={`tick-${arc.id}`}
              x1={start.x}
              y1={start.y}
              x2={end.x}
              y2={end.y}
              stroke="hsl(var(--border))"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
              opacity={chapterOpacity(arc.chapter)}
            />
          );
        })}

        {/* 절 번호 ---------------------------------------------------- */}
        <g ref={sectionLabelRef} fill="hsl(var(--muted-foreground))">
          {layout.sections.map((arc) => {
            const point = pointOnCircle(arc.midDeg, SECTION_LABEL_RADIUS);
            return (
              <text
                key={`sec-${arc.id}`}
                x={point.x}
                y={point.y}
                fontSize="0.72em"
                textAnchor="middle"
                dominantBaseline="middle"
                opacity={chapterOpacity(arc.chapter)}
              >
                {arc.key}
              </text>
            );
          })}
        </g>

        {/* 장 라벨 ---------------------------------------------------- */}
        {layout.chapters.map((arc) => {
          const point = pointOnCircle(arc.midDeg, CHAPTER_LABEL_RADIUS);
          const key = String(arc.chapter) as CriterionChapter;
          return (
            <text
              key={`chap-${arc.id}`}
              x={point.x}
              y={point.y}
              fontSize="1.05em"
              fontWeight={600}
              textAnchor="middle"
              dominantBaseline="middle"
              fill="hsl(var(--muted-foreground))"
              // 밴드·절 번호 위를 지나가도 읽히도록 배경색 후광을 두른다.
              stroke="hsl(var(--card))"
              strokeWidth="0.35em"
              paintOrder="stroke"
              strokeLinejoin="round"
              opacity={chapterOpacity(arc.chapter)}
            >
              {CHAPTER_SHORT_LABELS[key] ?? `${arc.chapter}장`}
            </text>
          );
        })}

        {/* 중앙 도넛 -------------------------------------------------- */}
        <g ref={centerRef} fontSize={`${BASE_FONT_PX}px`}>
          <circle
            cx={0}
            cy={0}
            r={donutInner}
            fill="hsl(var(--card))"
            fillOpacity={0.75}
          />
          {hasAssessment ? (
            <>
              {arcs.map((arc) => (
                <path
                  key={`arc-${arc.status}`}
                  d={arc.path}
                  fill={DONUT_ARC_FILL[arc.status]}
                />
              ))}
              <text
                x={0}
                y={-6}
                fontSize="1.75em"
                fontWeight={700}
                textAnchor="middle"
                dominantBaseline="middle"
                fill="hsl(var(--foreground))"
              >
                {`충족 ${stats.met}`}
              </text>
              <text
                x={0}
                y={18}
                fontSize="0.85em"
                textAnchor="middle"
                dominantBaseline="middle"
                fill="hsl(var(--muted-foreground))"
              >
                {`/ 총 ${stats.criteriaTotal}`}
              </text>
            </>
          ) : (
            <>
              <circle
                cx={0}
                cy={0}
                r={DONUT_RADIUS}
                fill="none"
                stroke="hsl(var(--border))"
                strokeWidth={DONUT_THICKNESS}
              />
              <text
                x={0}
                y={-4}
                fontSize="1.1em"
                fontWeight={600}
                textAnchor="middle"
                dominantBaseline="middle"
                fill="hsl(var(--muted-foreground))"
              >
                모의심사 전
              </text>
              <text
                x={0}
                y={16}
                fontSize="0.85em"
                textAnchor="middle"
                dominantBaseline="middle"
                fill="hsl(var(--muted-foreground))"
              >
                {`인증기준 ${stats.criteriaTotal}개`}
              </text>
            </>
          )}
        </g>
      </g>
    </svg>
  );
});
