"use client";

/**
 * 지식 그래프 캔버스 — Cytoscape 인스턴스의 수명만 책임진다.
 *
 * 데이터 계산은 `graph-elements.ts`, 화면 구성·패널은 `graph-tab.tsx` 가 맡는다.
 * 여기서 지키는 규칙 두 가지.
 *  1. 그래프 데이터가 바뀔 때만 인스턴스를 다시 만든다. 콜백이 바뀌었다고 다시 만들면
 *     화면이 깜빡이고 뷰포트가 초기화되므로 콜백은 ref 로 최신값만 갈아 끼운다.
 *  2. 필터는 요소를 다시 만들지 않고 `.hidden`(display:none) 클래스만 토글한다.
 *
 * cytoscape 는 모듈 상단에서 정적으로 불러온다(임포트 시점에 window 를 건드리지 않아
 * SSR 에서 안전하고, 실제 초기화는 useEffect 안에서만 한다).
 */

import cytoscape from "cytoscape";
import type { Core, EventObjectNode, StylesheetJson } from "cytoscape";
import * as React from "react";

import {
  hiddenNodeIds,
  toElements,
} from "@/app/(dashboard)/projects/[id]/graph-elements";
import type { GraphFilters, ProjectGraph, ProjectGraphNode } from "@/lib/types-graph";
import { cn } from "@/lib/utils";

/** `cy.fit()` 여백(px). */
const FIT_PADDING = 24;

/** 부모가 호출할 수 있는 캔버스 조작. */
export interface GraphCanvasHandle {
  /** 보이는 노드가 모두 들어오도록 뷰포트를 맞춘다. */
  fit: () => void;
  /** 네이티브 노드 선택(`:selected` 강조 테두리)을 푼다. 패널·시트를 닫을 때 함께 부른다. */
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
 * 상태 셀렉터는 항상 `type` 과 함께 건다. `unknown` 처럼 항목 판정과 증적 점검 결과가
 * 같은 문자열을 쓰는 경우가 있어, `node[status="unknown"]` 만으로는 증적까지 물든다.
 */
export function buildStylesheet(colors: GraphColors): StylesheetJson {
  return [
    /* 공통 ---------------------------------------------------------- */
    {
      selector: "node",
      style: {
        label: "data(label)",
        color: colors.foreground,
        "font-size": 9,
        "min-zoomed-font-size": 7,
        "text-wrap": "ellipsis",
        "text-max-width": "76px",
        "text-valign": "center",
        "text-halign": "center",
      },
    },

    /* 장 — compound 부모 -------------------------------------------- */
    {
      selector: 'node[type="chapter"]',
      style: {
        shape: "round-rectangle",
        "background-color": colors.muted,
        "background-opacity": 0.3,
        "border-width": 1,
        "border-color": colors.border,
        padding: "20px",
        "text-valign": "top",
        "text-margin-y": -4,
        "font-size": 13,
        "font-weight": "bold",
        color: colors.mutedForeground,
      },
    },

    /* 절 — compound 부모 -------------------------------------------- */
    {
      selector: 'node[type="section"]',
      style: {
        shape: "round-rectangle",
        "background-color": colors.secondary,
        "background-opacity": 0.45,
        "border-width": 1,
        "border-color": colors.border,
        padding: "12px",
        "text-valign": "top",
        "text-margin-y": -2,
        "font-size": 10,
        color: colors.mutedForeground,
      },
    },

    /* 인증기준 항목 -------------------------------------------------- */
    {
      selector: 'node[type="criterion"]',
      style: {
        shape: "ellipse",
        width: 22,
        height: 22,
        // 원 안에 "2.10.1" 은 들어가지 않으므로 코드를 아래에 붙인다.
        label: "data(code)",
        "text-valign": "bottom",
        "text-margin-y": 2,
        "text-max-width": "68px",
        "font-size": 8,
        "background-color": colors.secondary,
        "background-opacity": 0.7,
        "border-width": 1,
        "border-color": colors.border,
        color: colors.mutedForeground,
      },
    },
    {
      selector: 'node[type="criterion"][status="met"]',
      style: {
        "background-color": colors.success,
        "background-opacity": 1,
        "border-color": colors.success,
      },
    },
    {
      selector: 'node[type="criterion"][status="partial"]',
      style: {
        "background-color": colors.warning,
        "background-opacity": 1,
        "border-color": colors.warning,
      },
    },
    {
      selector: 'node[type="criterion"][status="unmet"]',
      style: {
        "background-color": colors.destructive,
        "background-opacity": 1,
        "border-color": colors.destructive,
      },
    },
    {
      selector: 'node[type="criterion"][status="unknown"]',
      style: {
        "background-color": colors.secondary,
        "background-opacity": 1,
        "border-color": colors.mutedForeground,
      },
    },

    /* 문서 · 증적 · 알림 --------------------------------------------- */
    {
      selector: 'node[type="document"]',
      style: {
        shape: "round-rectangle",
        width: 34,
        height: 24,
        "background-color": colors.primary,
        "background-opacity": 1,
        "border-width": 0,
        "text-valign": "bottom",
        "text-margin-y": 2,
        "font-size": 8,
        color: colors.foreground,
      },
    },
    {
      selector: 'node[type="evidence"]',
      style: {
        shape: "diamond",
        width: 22,
        height: 22,
        "background-color": colors.mutedForeground,
        "background-opacity": 1,
        "border-width": 0,
        "text-valign": "bottom",
        "text-margin-y": 2,
        "font-size": 8,
        color: colors.foreground,
      },
    },
    {
      selector: 'node[type="alert"]',
      style: {
        shape: "round-triangle",
        width: 22,
        height: 22,
        "background-color": colors.mutedForeground,
        "background-opacity": 1,
        "border-width": 0,
        "text-valign": "bottom",
        "text-margin-y": 2,
        "font-size": 8,
        color: colors.foreground,
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
        "line-color": colors.border,
        opacity: 0.6,
        "target-arrow-shape": "none",
      },
    },
    {
      selector: 'edge[type="cites_document"]',
      style: { "line-color": colors.mutedForeground },
    },
    {
      selector: 'edge[type="cites_evidence"]',
      style: { "line-color": colors.primary },
    },
    {
      selector: 'edge[type="maps_to"]',
      style: { "line-style": "dashed", opacity: 0.25 },
    },
    {
      selector: 'edge[type="triggered"]',
      style: { "line-color": colors.destructive, "line-style": "dashed" },
    },

    /* 상호작용 상태 --------------------------------------------------- */
    { selector: ".hl", style: { opacity: 1 } },
    { selector: "edge.hl", style: { width: 2, opacity: 1, "z-index": 10 } },
    { selector: ".dim", style: { opacity: 0.12 } },
    {
      selector: "node:selected",
      style: { "border-width": 3, "border-color": colors.foreground },
    },
    // 필터로 감춘 요소. 붙은 엣지도 Cytoscape 가 함께 감춘다.
    { selector: ".hidden", style: { display: "none" } },
  ];
}

export const GraphCanvas = React.forwardRef<
  GraphCanvasHandle,
  {
    graph: ProjectGraph;
    filters: GraphFilters;
    /** 노드를 누르면 원본 노드를, 배경을 누르면 null 을 준다. */
    onTapNode: (node: ProjectGraphNode | null) => void;
    className?: string;
  }
>(function GraphCanvas({ graph, filters, onTapNode, className }, ref) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const cyRef = React.useRef<Core | null>(null);
  // 콜백이 바뀔 때마다 인스턴스를 다시 만들지 않으려고 ref 에 최신값만 담아 둔다.
  const onTapRef = React.useRef(onTapNode);

  React.useEffect(() => {
    onTapRef.current = onTapNode;
  }, [onTapNode]);

  /** 보이는 노드에 맞춰 뷰포트를 정리한다. */
  const fitVisible = React.useCallback((cy: Core) => {
    const visible = cy.nodes(":visible");
    cy.fit(visible.length > 0 ? visible : undefined, FIT_PADDING);
  }, []);

  React.useImperativeHandle(
    ref,
    () => ({
      fit: () => {
        const cy = cyRef.current;
        if (cy) fitVisible(cy);
      },
      clearSelection: () => {
        cyRef.current?.elements(":selected").unselect();
      },
    }),
    [fitVisible],
  );

  // 그래프 데이터가 바뀔 때만 인스턴스를 새로 만든다.
  // StrictMode 의 이중 mount 는 cleanup 의 destroy() 로 정리된다.
  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
    const cy = cytoscape({
      container,
      elements: toElements(graph),
      style: buildStylesheet(readGraphColors()),
      layout: { name: "preset", fit: true, padding: FIT_PADDING },
      minZoom: 0.25,
      maxZoom: 3,
      wheelSensitivity: 0.2,
      boxSelectionEnabled: false,
      // 노드를 끌어 옮기면 결정적 배치가 깨진다.
      autoungrabify: true,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (event: EventObjectNode) => {
      onTapRef.current(nodeById.get(event.target.id()) ?? null);
    });
    cy.on("tap", (event) => {
      // 배경(코어)을 눌렀을 때만 선택을 푼다.
      if (event.target === cy) onTapRef.current(null);
    });
    cy.on("mouseover", "node", (event: EventObjectNode) => {
      const near = event.target.closedNeighborhood();
      cy.batch(() => {
        cy.elements().difference(near).addClass("dim");
        near.addClass("hl");
      });
    });
    cy.on("mouseout", "node", () => {
      cy.batch(() => {
        cy.elements().removeClass("dim").removeClass("hl");
      });
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [graph]);

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

  return <div ref={containerRef} className={cn("size-full", className)} />;
});
