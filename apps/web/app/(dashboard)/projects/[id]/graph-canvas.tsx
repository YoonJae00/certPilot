"use client";

/**
 * 지식 그래프 캔버스 — Cytoscape 인스턴스의 수명과 포커스 상호작용을 책임진다.
 *
 * 좌표 계산은 `graph-elements.ts`, 구조 장식은 `graph-underlay.tsx`,
 * 화면 구성·패널은 `graph-tab.tsx` 가 맡는다. 여기서 지키는 규칙.
 *  1. 그래프 데이터가 바뀔 때만 인스턴스를 다시 만든다. 콜백이 바뀌었다고 다시 만들면
 *     화면이 깜빡이고 뷰포트가 초기화되므로 콜백은 ref 로 최신값만 갈아 끼운다.
 *  2. 필터는 요소를 다시 만들지 않고 `.hidden`(display:none) 클래스만 토글한다.
 *  3. 포커스(탭)와 호버는 같은 하이라이트 로직을 쓴다. 포커스가 잡혀 있으면 호버는 무시한다.
 *
 * cytoscape 는 모듈 상단에서 정적으로 불러온다(임포트 시점에 window 를 건드리지 않아
 * SSR 에서 안전하고, 실제 초기화는 useEffect 안에서만 한다).
 */

import cytoscape from "cytoscape";
import type { Core, EventObjectNode, NodeSingular, StylesheetJson } from "cytoscape";
import * as React from "react";

import {
  computeRadialLayout,
  type GraphStats,
  hiddenNodeIds,
  toElements,
} from "@/app/(dashboard)/projects/[id]/graph-elements";
import {
  GraphUnderlay,
  type GraphUnderlayHandle,
} from "@/app/(dashboard)/projects/[id]/graph-underlay";
import type { GraphFilters, ProjectGraph, ProjectGraphNode } from "@/lib/types-graph";
import { cn } from "@/lib/utils";

/** `cy.fit()` 여백(px). */
const FIT_PADDING = 24;
/** 이보다 좁으면 문서·증적 라벨을 접는다(언더레이의 절 번호 임계값과 같은 값). */
const COMPACT_WIDTH = 640;

/** 부모가 호출할 수 있는 캔버스 조작. */
export interface GraphCanvasHandle {
  /** 보이는 노드가 모두 들어오도록 뷰포트를 맞춘다. */
  fit: () => void;
  /** 포커스(선택·하이라이트)를 모두 푼다. 정보 카드를 닫을 때 함께 부른다. */
  clearSelection: () => void;
}

/**
 * shadcn 테마 토큰에서 뽑아 온 그래프 색.
 *
 * Cytoscape 는 CSS 변수를 해석하지 못하므로 초기화 시점에 실제 색 문자열로 바꿔 둔다.
 * (다크 모드가 없으므로 1회만 읽는다. 도입하면 테마 변경 시 다시 읽어야 한다.)
 */
interface GraphColors {
  success: string;
  warning: string;
  destructive: string;
  secondary: string;
  muted: string;
  mutedForeground: string;
  border: string;
  primary: string;
  foreground: string;
  background: string;
}

/** `app/globals.css` 의 기본값. 변수를 못 읽는 환경(테스트 등)에서 쓴다.
 *
 * Cytoscape 의 색 파서는 공백 구분 HSL(CSS Color 4 문법)을 모른다. 쉼표 구문이 아니면
 * "invalid" 경고와 함께 기본 회색으로 떨어지므로 반드시 쉼표로 잇는다.
 */
const FALLBACK_COLORS: GraphColors = {
  success: "hsl(142, 71%, 29%)",
  warning: "hsl(38, 92%, 40%)",
  destructive: "hsl(0, 72.2%, 50.6%)",
  secondary: "hsl(240, 4.8%, 95.9%)",
  muted: "hsl(240, 4.8%, 95.9%)",
  mutedForeground: "hsl(240, 3.8%, 46.1%)",
  border: "hsl(240, 5.9%, 90%)",
  primary: "hsl(240, 5.9%, 10%)",
  foreground: "hsl(240, 10%, 3.9%)",
  background: "hsl(0, 0%, 100%)",
};

/** 테마 토큰(`--success` 등, "142 71% 29%" 형식)을 읽어 `hsl(...)` 문자열로 만든다. */
export function readGraphColors(): GraphColors {
  if (typeof document === "undefined") return FALLBACK_COLORS;
  const root = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string): string => {
    const raw = root.getPropertyValue(name).trim();
    // 토큰은 "142 71% 29%" 처럼 공백 구분이다. Cytoscape 가 읽도록 쉼표로 바꾼다.
    return raw ? `hsl(${raw.split(/\s+/).join(", ")})` : fallback;
  };
  return {
    success: read("--success", FALLBACK_COLORS.success),
    warning: read("--warning", FALLBACK_COLORS.warning),
    destructive: read("--destructive", FALLBACK_COLORS.destructive),
    secondary: read("--secondary", FALLBACK_COLORS.secondary),
    muted: read("--muted", FALLBACK_COLORS.muted),
    mutedForeground: read("--muted-foreground", FALLBACK_COLORS.mutedForeground),
    border: read("--border", FALLBACK_COLORS.border),
    primary: read("--primary", FALLBACK_COLORS.primary),
    foreground: read("--foreground", FALLBACK_COLORS.foreground),
    background: read("--background", FALLBACK_COLORS.background),
  };
}

/**
 * 스타일시트를 만든다.
 *
 * ## 색각이상 대응
 * 팔레트 검증에서 미충족(빨강)과 부분충족(주황)의 deutan ΔE 가 7.1 에 그쳤다. 그래서
 * 판정은 색만으로 구분하지 않고 **색 + 모양 + 크기**로 이중 인코딩한다.
 *   충족 = 작은 채운 원 / 부분충족 = 도넛 / 미충족 = 큰 원 + 진한 외곽 링 / 판단불가 = 속빈 원.
 *
 * ## 셀렉터 규칙
 * 상태 셀렉터는 항상 `type` 과 함께 건다. `unknown` 처럼 항목 판정과 증적 점검 결과가
 * 같은 문자열을 쓰는 경우가 있어, `node[status="unknown"]` 만으로는 증적까지 물든다.
 *
 * ## 치수
 * 값은 전부 모델 단위다. 링 위 항목 간격이 약 16.6 모델 단위라 그 안에 들어가도록 잡았고,
 * 기본 배치의 zoom(약 0.55)에서 화면 크기가 되도록 글자 크기는 넉넉히 키워 두었다.
 */
export function buildStylesheet(colors: GraphColors): StylesheetJson {
  return [
    /* 공통 ---------------------------------------------------------- */
    {
      selector: "node",
      style: {
        // 항목 코드는 포커스·호버 때만 띄운다(101개를 늘 붙이면 링이 글자로 덮인다).
        label: "",
        color: colors.mutedForeground,
        "font-size": 15,
        "min-zoomed-font-size": 7,
        "text-wrap": "ellipsis",
        "text-max-width": "150px",
        // 라벨을 노드 바깥 어느 쪽에 붙일지는 배치가 노드마다 계산해 실어 보낸다.
        "text-valign": (node: NodeSingular) => node.data("valign") ?? "center",
        "text-halign": (node: NodeSingular) => node.data("halign") ?? "center",
        "text-margin-x": (node: NodeSingular) => Number(node.data("tmx") ?? 0),
        "text-margin-y": (node: NodeSingular) => Number(node.data("tmy") ?? 0),
        "text-background-color": colors.background,
        "text-background-opacity": 0.78,
        "text-background-padding": "2px",
        "border-opacity": 1,
      },
    },

    /* 인증기준 항목 --------------------------------------------------
     * 크기 규칙: 링 위 항목 간격이 약 16.6 모델 단위이고 border 는 바깥으로 그려지므로,
     * 인접한 같은 상태끼리도 겹치지 않게 "width + 2×border ≤ 16.6" 을 지킨다. */
    {
      selector: 'node[type="criterion"]',
      style: {
        shape: "ellipse",
        width: 9,
        height: 9,
        "background-color": colors.muted,
        "background-opacity": 1,
        "border-width": 1,
        "border-color": colors.border,
        "font-size": 18,
      },
    },
    {
      // 충족: 작고 조용한 채운 원.
      selector: 'node[type="criterion"][status="met"]',
      style: {
        width: 12,
        height: 12,
        "background-color": colors.success,
        "border-width": 0,
      },
    },
    {
      // 부분충족: 두꺼운 테두리 + 흰 속 = 도넛.
      selector: 'node[type="criterion"][status="partial"]',
      style: {
        width: 9,
        height: 9,
        "background-color": colors.background,
        "border-width": 3,
        "border-color": colors.warning,
      },
    },
    {
      // 미충족: 경보 상태라 가장 크고 무겁게(외곽 링까지). 합계 16 — 간격 안에서 최대.
      selector: 'node[type="criterion"][status="unmet"]',
      style: {
        width: 12,
        height: 12,
        "background-color": colors.destructive,
        "border-width": 2,
        "border-color": colors.foreground,
      },
    },
    {
      // 판단불가: 속빈 원.
      selector: 'node[type="criterion"][status="unknown"]',
      style: {
        width: 9,
        height: 9,
        "background-color": colors.background,
        "border-width": 1.5,
        "border-color": colors.mutedForeground,
      },
    },

    /* 문서 · 증적 · 알림 --------------------------------------------- */
    {
      selector: 'node[type="document"]',
      style: {
        shape: "round-rectangle",
        width: 36,
        height: 26,
        "background-color": colors.primary,
        "background-opacity": 1,
        "border-width": 0,
        label: "data(short)",
        color: colors.foreground,
      },
    },
    {
      selector: 'node[type="evidence"]',
      style: {
        shape: "diamond",
        width: 21,
        height: 21,
        "background-color": colors.mutedForeground,
        "background-opacity": 1,
        "border-width": 0,
        label: "data(short)",
        color: colors.foreground,
      },
    },
    {
      selector: 'node[type="alert"]',
      style: {
        shape: "ellipse",
        width: 11,
        height: 11,
        "background-color": colors.mutedForeground,
        "background-opacity": 1,
        "border-width": 0,
      },
    },
    {
      // `[!read]` = read 가 거짓인 노드. 아직 읽지 않은 알림을 붉게 세운다.
      selector: 'node[type="alert"][!read]',
      style: { "background-color": colors.destructive },
    },

    /* 엣지 ----------------------------------------------------------- */
    {
      selector: "edge",
      style: {
        width: 1,
        "curve-style": "straight",
        "line-color": colors.mutedForeground,
        opacity: 0.3,
        "target-arrow-shape": "none",
        // 엣지는 히트테스트에서 뺀다. 안 그러면 링 안을 채운 (거의 안 보이는) 곡선이
        // 배경 탭을 흡수해 "빈 곳을 눌러 포커스 해제"가 캔버스 대부분에서 죽는다.
        events: "no",
      },
    },
    {
      // 근거 인용은 중심 쪽으로 휘어 링 안을 안개처럼 채운다(곡률은 배치가 계산).
      selector: 'edge[type="cites_document"]',
      style: {
        "curve-style": "unbundled-bezier",
        "control-point-distances": "data(bow)",
        "control-point-weights": 0.5,
        opacity: 0.12,
      },
    },
    {
      // 증적 인용은 링에서 바깥으로 뻗는 짧은 스포크라 직선이 읽기 좋다.
      selector: 'edge[type="cites_evidence"]',
      style: { "curve-style": "straight", opacity: 0.3 },
    },
    {
      // 점검 매핑·알림 발생은 기본으로 감춘다. 포커스한 노드의 것만 드러낸다.
      selector: 'edge[type="maps_to"]',
      style: {
        display: "none",
        "line-style": "dashed",
        "line-color": colors.primary,
        opacity: 0.55,
      },
    },
    {
      selector: 'edge[type="triggered"]',
      style: {
        display: "none",
        "line-style": "dashed",
        "line-color": colors.destructive,
        opacity: 0.7,
      },
    },

    /* 상호작용 상태 --------------------------------------------------- */
    { selector: ".dim", style: { opacity: 0.06 } },
    { selector: "node.dim", style: { "text-opacity": 0 } },
    { selector: ".hl", style: { opacity: 1, "z-index": 10 } },
    {
      selector: "edge.hl",
      style: { width: 2, opacity: 0.9, "z-index": 12 },
    },
    {
      selector: 'edge[type="cites_document"].hl',
      style: { "line-color": colors.primary },
    },
    {
      selector: 'edge[type="cites_evidence"].hl',
      style: { "line-color": colors.mutedForeground, opacity: 0.95 },
    },
    {
      selector: 'node[type="criterion"].hl',
      style: { label: "data(code)", color: colors.foreground, "z-index": 20 },
    },
    // 포커스한 노드 하나만 외곽 링으로 못 박는다.
    {
      selector: "node:selected",
      style: {
        "border-width": 3,
        "border-color": colors.foreground,
        "z-index": 30,
      },
    },
    // 숨겨 둔 엣지 중 포커스 이웃만 되살린다.
    { selector: ".show-edge", style: { display: "element" } },
    // 좁은 캔버스에서는 문서·증적 라벨을 접는다. 축소율이 커서 어차피 읽히지 않는다.
    { selector: 'node[type="document"].compact', style: { label: "" } },
    { selector: 'node[type="evidence"].compact', style: { label: "" } },
    // 필터로 감춘 요소. 붙은 엣지도 Cytoscape 가 함께 감춘다. 항상 마지막이어야 한다.
    { selector: ".hidden", style: { display: "none" } },
  ];
}

export const GraphCanvas = React.forwardRef<
  GraphCanvasHandle,
  {
    graph: ProjectGraph;
    filters: GraphFilters;
    /** 현재 필터를 반영한 통계. 중앙 도넛이 쓴다. */
    stats: GraphStats;
    /** 노드를 누르면 원본 노드를, 배경을 누르면 null 을 준다. */
    onTapNode: (node: ProjectGraphNode | null) => void;
    className?: string;
  }
>(function GraphCanvas({ graph, filters, stats, onTapNode, className }, ref) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const wrapperRef = React.useRef<HTMLDivElement | null>(null);
  const cyRef = React.useRef<Core | null>(null);
  const underlayRef = React.useRef<GraphUnderlayHandle | null>(null);
  // 포커스 중인 노드 id. 값이 있으면 호버 하이라이트를 무시한다.
  const focusedIdRef = React.useRef<string | null>(null);
  // 콜백이 바뀔 때마다 인스턴스를 다시 만들지 않으려고 ref 에 최신값만 담아 둔다.
  const onTapRef = React.useRef(onTapNode);

  React.useEffect(() => {
    onTapRef.current = onTapNode;
  }, [onTapNode]);

  const layout = React.useMemo(() => computeRadialLayout(graph), [graph]);

  /** 캔버스 폭에 따라 항상 켜 두는 라벨을 접거나 편다. */
  const applyCompact = React.useCallback((cy: Core) => {
    const compact = (containerRef.current?.clientWidth ?? 0) < COMPACT_WIDTH;
    const targets = cy.nodes('[type="document"], [type="evidence"]');
    if (compact) targets.addClass("compact");
    else targets.removeClass("compact");
  }, []);

  /** 언더레이를 캔버스 뷰포트에 맞춘다. */
  const syncUnderlay = React.useCallback((cy: Core) => {
    const pan = cy.pan();
    underlayRef.current?.sync(
      cy.zoom(),
      pan.x,
      pan.y,
      containerRef.current?.clientWidth ?? 0,
    );
  }, []);

  /** 보이는 노드에 맞춰 뷰포트를 정리한다. */
  const fitVisible = React.useCallback(
    (cy: Core) => {
      const visible = cy.nodes(":visible");
      cy.fit(visible.length > 0 ? visible : undefined, FIT_PADDING);
      syncUnderlay(cy);
    },
    [syncUnderlay],
  );

  /** 포커스·호버 하이라이트를 한곳에서 처리한다. `id` 가 null 이면 전부 되돌린다. */
  const applyHighlight = React.useCallback((cy: Core, id: string | null) => {
    cy.batch(() => {
      cy.elements().removeClass("hl dim show-edge");
      if (!id) return;
      const node = cy.getElementById(id);
      if (node.empty()) return;
      const near = node.closedNeighborhood();
      cy.elements().difference(near).addClass("dim");
      near.addClass("hl");
      // maps_to·triggered 는 기본이 display:none 이라 이웃 것만 되살린다.
      near.edges().addClass("show-edge");
    });
  }, []);

  React.useImperativeHandle(
    ref,
    () => ({
      fit: () => {
        const cy = cyRef.current;
        if (cy) fitVisible(cy);
      },
      clearSelection: () => {
        const cy = cyRef.current;
        if (!cy) return;
        cy.elements(":selected").unselect();
        focusedIdRef.current = null;
        applyHighlight(cy, null);
        underlayRef.current?.setFocused(false);
      },
    }),
    [applyHighlight, fitVisible],
  );

  // 그래프 데이터가 바뀔 때만 인스턴스를 새로 만든다.
  // StrictMode 의 이중 mount 는 cleanup 의 destroy() 로 정리된다.
  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
    const cy = cytoscape({
      container,
      elements: toElements(graph, layout),
      style: buildStylesheet(readGraphColors()),
      layout: { name: "preset", fit: true, padding: FIT_PADDING },
      minZoom: 0.3,
      maxZoom: 4,
      // 기본값 1 은 트랙패드에서 튀고, 0.2 는 휠 마우스에서 답답하다. 중간값으로 둔다.
      wheelSensitivity: 0.7,
      boxSelectionEnabled: false,
      // 노드를 끌어 옮기면 결정적 배치가 깨진다.
      autoungrabify: true,
    });
    cyRef.current = cy;
    focusedIdRef.current = null;
    // 재생성 전에 포커스로 흐려졌던 언더레이를 되살린다(그래프 갱신 시 잔존 방지).
    underlayRef.current?.setFocused(false);

    cy.on("viewport", () => syncUnderlay(cy));

    cy.on("tap", "node", (event: EventObjectNode) => {
      const id = event.target.id();
      focusedIdRef.current = id;
      applyHighlight(cy, id);
      underlayRef.current?.setFocused(true);
      onTapRef.current(nodeById.get(id) ?? null);
    });
    cy.on("tap", (event) => {
      // 배경(코어)을 눌렀을 때만 포커스를 푼다.
      if (event.target !== cy) return;
      focusedIdRef.current = null;
      applyHighlight(cy, null);
      underlayRef.current?.setFocused(false);
      onTapRef.current(null);
    });
    cy.on("dbltap", (event) => {
      if (event.target === cy) fitVisible(cy);
    });

    // 포인터가 있는 기기에서만 호버 미리보기를 붙인다(터치에서는 탭과 충돌한다).
    const hoverCapable =
      typeof window !== "undefined" && window.matchMedia("(hover: hover)").matches;
    if (hoverCapable) {
      cy.on("mouseover", "node", (event: EventObjectNode) => {
        if (focusedIdRef.current) return;
        applyHighlight(cy, event.target.id());
      });
      cy.on("mouseout", "node", () => {
        if (focusedIdRef.current) return;
        applyHighlight(cy, null);
      });
    }

    // preset 레이아웃의 fit 은 컨테이너 크기가 잡히기 전에 끝날 수 있어 한 번 더 맞춘다.
    applyCompact(cy);
    fitVisible(cy);

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [applyCompact, applyHighlight, fitVisible, graph, layout, syncUnderlay]);

  // 필터는 요소를 다시 만들지 않고 클래스만 토글한다.
  React.useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const hidden = hiddenNodeIds(graph, filters);
    cy.batch(() => {
      cy.nodes().forEach((node) => {
        if (hidden.has(node.id())) node.addClass("hidden");
        else node.removeClass("hidden");
      });
    });
    fitVisible(cy);
  }, [filters, fitVisible, graph]);

  // 탭 전환·창 회전으로 컨테이너가 바뀌면 캔버스를 다시 재고 맞춘다.
  React.useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      const cy = cyRef.current;
      if (!cy) return;
      cy.resize();
      applyCompact(cy);
      fitVisible(cy);
    });
    observer.observe(wrapper);
    return () => observer.disconnect();
  }, [applyCompact, fitVisible]);

  return (
    <div ref={wrapperRef} className={cn("relative size-full overflow-hidden", className)}>
      <GraphUnderlay
        ref={underlayRef}
        layout={layout}
        stats={stats}
        hasAssessment={graph.assessment_id !== null}
        chapter={filters.chapter}
        className="pointer-events-none absolute inset-0"
      />
      <div ref={containerRef} className="absolute inset-0" />
    </div>
  );
});
