"use client";

/**
 * 지식 그래프 탭 (PRD §7 F3·F5·F8).
 *
 * "각 판정이 어디에 근거하는가"를 한 장으로 보여 준다. 인증기준 101개를 하나의 링에
 * 코드순으로 세우고(장 → 절 → 항목), 근거가 되는 문서는 링 안쪽에, 클라우드 증적은
 * 링 바깥에 둔 방사형 focus+context 그래프다.
 *
 *   · 기본 화면 : 근거 인용선이 안개처럼 깔린 전체 그림 + 장별 상태
 *   · 탭(포커스): 그 노드와 이웃만 남기고 나머지를 죽인 뒤 정보 카드를 연다
 *
 * 화면이 보고 있는 심사는 `graph.assessment_id`(서버가 고른 최신 완료 심사) 하나다.
 * 판정 상세 시트도 같은 심사의 판정을 쓰므로 그래프와 시트가 어긋나지 않는다.
 */

import { X } from "lucide-react";
import * as React from "react";

import { FindingDetailSheet } from "@/app/(dashboard)/projects/[id]/finding-detail-sheet";
import {
  GraphCanvas,
  type GraphCanvasHandle,
} from "@/app/(dashboard)/projects/[id]/graph-canvas";
import {
  buildNeighborIndex,
  computeGraphStats,
  hiddenNodeIds,
} from "@/app/(dashboard)/projects/[id]/graph-elements";
import type { UseAssessmentsResult } from "@/app/(dashboard)/projects/[id]/use-assessments";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, assessmentsApi, toMessage } from "@/lib/api";
import { graphApi } from "@/lib/api-graph";
import {
  CHAPTERS,
  CHAPTER_SHORT_LABELS,
  DOCUMENT_STATUS_CLASSES,
  FINDING_STATUS_CLASSES,
  FINDING_STATUS_LABELS,
  FINDING_STATUS_ORDER,
  documentStatusLabel,
  formatDateTime,
  formatPercent,
} from "@/lib/labels";
import type { EvidenceStatus, FindingRow, FindingStatus } from "@/lib/types";
import type { AlertType } from "@/lib/types-dashboard";
import {
  DEFAULT_FILTERS,
  type GraphFilters,
  type ProjectGraph,
  type ProjectGraphNode,
} from "@/lib/types-graph";
import { cn } from "@/lib/utils";

/** 정보 카드를 우상단에 띄울 수 있는 최소 캔버스 폭(px). 그보다 좁으면 하단 카드. */
const WIDE_CARD_MIN_WIDTH = 640;
/** 문서·증적 카드에서 항목 코드를 몇 개까지 나열할지. */
const CODE_LIST_LIMIT = 10;
/** 가로 스크롤 칩 행에서 스크롤바를 감추는 유틸(전역 CSS 없이 처리한다). */
const HIDE_SCROLLBAR =
  "[-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden";

/** 증적 점검 결과 문구. */
const EVIDENCE_STATUS_LABELS: Record<EvidenceStatus, string> = {
  pass: "통과",
  fail: "실패",
  warn: "경고",
  unknown: "확인 불가",
};

/** 알림 종류 문구(대시보드 카드와 같은 표기). */
const ALERT_TYPE_LABELS: Record<AlertType, string> = {
  drift: "설정 변경",
  due: "일정",
  defect: "예상 결함",
};

/**
 * 판정 상태 글리프.
 *
 * 캔버스의 노드 모양과 1:1 로 맞춘다. 색만으로는 미충족(빨강)과 부분충족(주황)이
 * 적록색약에서 갈라지지 않아, 범례도 색이 아니라 모양으로 읽히게 만든다.
 */
function StatusGlyph({ status }: { status: FindingStatus }) {
  return (
    <svg aria-hidden viewBox="0 0 14 14" className="size-3.5 shrink-0">
      {status === "met" ? (
        <circle cx="7" cy="7" r="4" fill="hsl(var(--success))" />
      ) : null}
      {status === "partial" ? (
        <circle
          cx="7"
          cy="7"
          r="4"
          fill="hsl(var(--background))"
          stroke="hsl(var(--warning))"
          strokeWidth="2.5"
        />
      ) : null}
      {status === "unmet" ? (
        <circle
          cx="7"
          cy="7"
          r="5"
          fill="hsl(var(--destructive))"
          stroke="hsl(var(--foreground))"
          strokeWidth="1.5"
        />
      ) : null}
      {status === "unknown" ? (
        <circle
          cx="7"
          cy="7"
          r="4"
          fill="hsl(var(--background))"
          stroke="hsl(var(--muted-foreground))"
          strokeWidth="1.5"
        />
      ) : null}
    </svg>
  );
}

/** 문서·증적·알림·엣지 글리프. 캔버스 모양을 그대로 축소한 것이다. */
function ShapeGlyph({ kind }: { kind: "document" | "evidence" | "alert" | "bezier" | "spoke" }) {
  return (
    <svg aria-hidden viewBox="0 0 18 14" className="h-3.5 w-[18px] shrink-0">
      {kind === "document" ? (
        <rect x="3" y="3" width="12" height="9" rx="2" fill="hsl(var(--primary))" />
      ) : null}
      {kind === "evidence" ? (
        <path d="M9 2 L14 7 L9 12 L4 7 Z" fill="hsl(var(--muted-foreground))" />
      ) : null}
      {kind === "alert" ? (
        <circle cx="9" cy="7" r="4" fill="hsl(var(--destructive))" />
      ) : null}
      {kind === "bezier" ? (
        <path
          d="M1 11 Q9 1 17 11"
          fill="none"
          stroke="hsl(var(--muted-foreground))"
          strokeWidth="1.5"
        />
      ) : null}
      {kind === "spoke" ? (
        <path d="M1 7 H17" stroke="hsl(var(--muted-foreground))" strokeWidth="1.5" />
      ) : null}
    </svg>
  );
}

export function GraphTab({
  projectId,
  assessments,
  onGoToAssessment,
}: {
  projectId: string;
  assessments: UseAssessmentsResult;
  /** 아직 모의심사를 돌리지 않았을 때 모의심사 탭으로 보내는 콜백. */
  onGoToAssessment: () => void;
}) {
  const [graph, setGraph] = React.useState<ProjectGraph | null>(null);
  const [notReady, setNotReady] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [filters, setFilters] = React.useState<GraphFilters>(DEFAULT_FILTERS);
  const [selected, setSelected] = React.useState<ProjectGraphNode | null>(null);
  // 판정 상세 시트는 정보 카드의 버튼으로만 연다(탭 즉시 열면 그래프가 가려진다).
  const [sheetFinding, setSheetFinding] = React.useState<FindingRow | null>(null);
  const [findings, setFindings] = React.useState<FindingRow[] | null>(null);
  const [narrow, setNarrow] = React.useState(false);
  const canvasRef = React.useRef<GraphCanvasHandle | null>(null);
  const stageRef = React.useRef<HTMLDivElement | null>(null);

  // 모의심사가 끝나면 그래프도 새로 받아야 하므로 최신 완료 실행 id 를 의존성에 둔다.
  const latestDoneId = React.useMemo(
    () => assessments.assessments?.find((item) => item.status === "done")?.id ?? null,
    [assessments.assessments],
  );

  React.useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    setError(null);

    graphApi
      .get(projectId, controller.signal)
      .then((data) => {
        setGraph(data);
        setNotReady(false);
        // 새 그래프로 갈리면 이전 그래프의 노드를 가리키던 카드·시트를 정리한다.
        setSelected(null);
        setSheetFinding(null);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        // 아직 배포되지 않은 API 는 준비 중으로 표시한다(백엔드와 병렬 작업).
        if (err instanceof ApiError && (err.status === 404 || err.status === 501)) {
          setNotReady(true);
          setGraph(null);
          return;
        }
        setError(toMessage(err));
        setGraph(null);
      });

    return () => controller.abort();
  }, [latestDoneId, projectId]);

  const assessmentId = graph?.assessment_id ?? null;

  // 판정 상세 시트에 넘길 행. 101개뿐이라 한 번에 받아 코드로 색인한다.
  React.useEffect(() => {
    if (!assessmentId) {
      setFindings(null);
      return;
    }
    const controller = new AbortController();

    assessmentsApi
      .findings(projectId, assessmentId, {}, controller.signal)
      .then((rows) => setFindings(rows))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        // 그래프 자체는 이미 그려졌으므로 상세만 포기한다(정보 카드로 대체된다).
        setFindings([]);
      });

    return () => controller.abort();
  }, [assessmentId, projectId]);

  // 카드 위치(우상단 vs 하단)는 캔버스 실제 폭으로 정한다. 뷰포트 폭은 사이드바를 모른다.
  React.useEffect(() => {
    const stage = stageRef.current;
    if (!stage || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setNarrow(width > 0 && width < WIDE_CARD_MIN_WIDTH);
    });
    observer.observe(stage);
    return () => observer.disconnect();
  }, [graph]);

  const findingByCode = React.useMemo(
    () => new Map((findings ?? []).map((row) => [row.criterion_code, row])),
    [findings],
  );

  const neighbors = React.useMemo(
    () => (graph ? buildNeighborIndex(graph) : null),
    [graph],
  );

  const hidden = React.useMemo(
    () => (graph ? hiddenNodeIds(graph, filters) : new Set<string>()),
    [filters, graph],
  );
  const stats = React.useMemo(
    () => (graph ? computeGraphStats(graph, hidden) : null),
    [graph, hidden],
  );

  const handleTapNode = React.useCallback((node: ProjectGraphNode | null) => {
    // 장·절은 Cytoscape 노드가 아니지만 계약상 존재하므로 방어적으로 걸러 둔다.
    if (node && (node.type === "chapter" || node.type === "section")) {
      setSelected(null);
      return;
    }
    setSelected(node);
  }, []);

  const closeCard = React.useCallback(() => {
    setSelected(null);
    canvasRef.current?.clearSelection();
  }, []);

  function toggleStatus(status: FindingStatus) {
    setFilters((prev) => ({
      ...prev,
      statuses: prev.statuses.includes(status)
        ? prev.statuses.filter((item) => item !== status)
        : [...prev.statuses, status],
    }));
  }

  if (notReady) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-sm text-muted-foreground">
          지식 그래프 API 연동을 준비하고 있습니다. 연동이 끝나면 항목·문서·증적의 연결
          관계가 여기에 표시됩니다.
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <p
        role="alert"
        className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
      >
        {error}
      </p>
    );
  }

  if (!graph || !stats || !neighbors) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-[560px] w-full" />
      </div>
    );
  }

  // 판정 상세 버튼의 상태. 심사 유무·판정 로딩 상태에 따라 달라진다.
  const criterionNotice =
    assessmentId === null
      ? "모의심사를 실행하면 판정이 표시됩니다."
      : findings === null
        ? "판정을 불러오는 중입니다."
        : "이 항목의 판정을 불러오지 못했습니다.";

  return (
    <div className="space-y-4">
      {assessmentId === null ? (
        <Card>
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
            <p className="text-sm text-muted-foreground">
              아직 완료된 모의심사가 없어 인증기준 골격만 보여 줍니다. 모의심사를 실행하면
              항목마다 판정 색과 근거 연결선이 채워집니다.
            </p>
            <Button variant="outline" size="sm" onClick={onGoToAssessment}>
              모의심사 실행하러 가기
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardContent className="space-y-3 p-4 sm:p-5">
          {/* 통계 헤더 ------------------------------------------------ */}
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
              <p className="text-sm font-medium tabular-nums">
                {`항목 ${stats.criteria} / ${stats.criteriaTotal} · 문서 ${stats.documents} · 증적 ${stats.evidence} · 연결 ${stats.edges}건`}
              </p>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                {FINDING_STATUS_ORDER.map((status) => (
                  <span
                    key={status}
                    className="flex items-center gap-1 text-xs tabular-nums text-muted-foreground"
                  >
                    <StatusGlyph status={status} />
                    {FINDING_STATUS_LABELS[status]} {stats[status]}
                  </span>
                ))}
              </div>
            </div>

            <Button
              type="button"
              variant="outline"
              size="sm"
              className="shrink-0"
              onClick={() => canvasRef.current?.fit()}
            >
              보기 초기화
            </Button>
          </div>

          {/* 필터 — 좁은 화면에서는 한 줄 가로 스크롤 칩 행이 된다 ------ */}
          <div
            className={cn(
              "-mx-1 flex items-center gap-2 overflow-x-auto px-1 py-0.5",
              HIDE_SCROLLBAR,
            )}
          >
            <Select
              value={filters.chapter === "all" ? "all" : String(filters.chapter)}
              onValueChange={(value) =>
                setFilters((prev) => ({
                  ...prev,
                  chapter: value === "all" ? "all" : (Number(value) as 1 | 2 | 3),
                }))
              }
            >
              <SelectTrigger
                id="graph-chapter"
                aria-label="장 필터"
                className="h-8 w-[132px] shrink-0 text-xs"
              >
                <SelectValue placeholder="전체 장" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">전체 장</SelectItem>
                {CHAPTERS.map((chapter) => (
                  <SelectItem key={chapter} value={chapter}>
                    {CHAPTER_SHORT_LABELS[chapter]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <span aria-hidden className="h-5 w-px shrink-0 bg-border" />

            {FINDING_STATUS_ORDER.map((status) => {
              const active = filters.statuses.includes(status);
              return (
                <Button
                  key={status}
                  type="button"
                  size="sm"
                  variant={active ? "default" : "outline"}
                  aria-pressed={active}
                  className="h-8 shrink-0 gap-1.5 rounded-full px-3 text-xs"
                  onClick={() => toggleStatus(status)}
                >
                  {active ? null : <StatusGlyph status={status} />}
                  {FINDING_STATUS_LABELS[status]}
                </Button>
              );
            })}

            <span aria-hidden className="h-5 w-px shrink-0 bg-border" />

            <Button
              type="button"
              size="sm"
              variant={filters.showDocuments ? "default" : "outline"}
              aria-pressed={filters.showDocuments}
              className="h-8 shrink-0 rounded-full px-3 text-xs"
              onClick={() =>
                setFilters((prev) => ({ ...prev, showDocuments: !prev.showDocuments }))
              }
            >
              문서
            </Button>
            <Button
              type="button"
              size="sm"
              variant={filters.showEvidence ? "default" : "outline"}
              aria-pressed={filters.showEvidence}
              className="h-8 shrink-0 rounded-full px-3 text-xs"
              onClick={() =>
                setFilters((prev) => ({ ...prev, showEvidence: !prev.showEvidence }))
              }
            >
              증적
            </Button>
          </div>

          {/* 범례 ---------------------------------------------------- */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t pt-2.5 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">범례</span>
            <span className="flex items-center gap-1">
              <ShapeGlyph kind="document" />
              문서
            </span>
            <span className="flex items-center gap-1">
              <ShapeGlyph kind="evidence" />
              증적
            </span>
            <span className="flex items-center gap-1">
              <ShapeGlyph kind="alert" />
              알림
            </span>
            <span className="flex items-center gap-1">
              <ShapeGlyph kind="bezier" />
              문서 근거 인용
            </span>
            <span className="flex items-center gap-1">
              <ShapeGlyph kind="spoke" />
              증적 근거 인용
            </span>
            <span className="text-muted-foreground/70">
              노드를 누르면 그 근거만 남습니다
            </span>
          </div>
        </CardContent>
      </Card>

      {/* 캔버스 --------------------------------------------------- */}
      <div
        ref={stageRef}
        className="relative h-[min(88vw,560px)] w-full overflow-hidden rounded-md border bg-card sm:h-[640px]"
      >
        <GraphCanvas
          ref={canvasRef}
          graph={graph}
          filters={filters}
          stats={stats}
          onTapNode={handleTapNode}
        />
        {selected ? (
          <NodeInfoCard
            node={selected}
            narrow={narrow}
            neighbors={neighbors}
            findingRow={
              selected.type === "criterion"
                ? (findingByCode.get(selected.code) ?? null)
                : null
            }
            criterionNotice={criterionNotice}
            onOpenFinding={setSheetFinding}
            onClose={closeCard}
          />
        ) : null}
      </div>

      {assessmentId && sheetFinding ? (
        <FindingDetailSheet
          projectId={projectId}
          assessmentId={assessmentId}
          finding={sheetFinding}
          onOpenChange={(open) => {
            if (!open) setSheetFinding(null);
          }}
        />
      ) : null}
    </div>
  );
}

/**
 * 캔버스 위에 겹쳐 띄우는 노드 정보 카드.
 *
 * 넓은 화면은 우상단 카드, 좁은 화면은 하단 고정 카드다. 어느 쪽이든 캔버스가 계속
 * 보여야 해서 Sheet(모달)를 쓰지 않는다.
 */
function NodeInfoCard({
  node,
  narrow,
  neighbors,
  findingRow,
  criterionNotice,
  onOpenFinding,
  onClose,
}: {
  node: ProjectGraphNode;
  /** 좁은 캔버스에서는 하단 고정 카드로 바꾼다. */
  narrow: boolean;
  neighbors: ReturnType<typeof buildNeighborIndex>;
  /** 판정 상세 시트에 넘길 행. 아직 못 받았으면 null. */
  findingRow: FindingRow | null;
  criterionNotice: string;
  onOpenFinding: (row: FindingRow) => void;
  onClose: () => void;
}) {
  return (
    <Card
      className={cn(
        "absolute z-20 overflow-y-auto shadow-lg",
        narrow
          ? "inset-x-0 bottom-0 max-h-[45%] rounded-b-none rounded-t-xl border-x-0 border-b-0"
          : "right-3 top-3 max-h-[calc(100%-1.5rem)] w-72",
      )}
    >
      <CardContent className="space-y-2.5 p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 break-words text-sm font-medium leading-snug">
            {node.label}
          </p>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="-mr-2 -mt-2 size-7 shrink-0"
            aria-label="정보 카드 닫기"
            onClick={onClose}
          >
            <X className="size-4" />
          </Button>
        </div>

        {node.type === "criterion" ? (
          <div className="space-y-2.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="tabular-nums">
                {node.code}
              </Badge>
              {node.finding ? (
                <>
                  <Badge className={cn(FINDING_STATUS_CLASSES[node.finding.status])}>
                    {FINDING_STATUS_LABELS[node.finding.status]}
                  </Badge>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    신뢰도 {formatPercent(node.finding.confidence)}
                  </span>
                </>
              ) : (
                <Badge variant="secondary">판정 없음</Badge>
              )}
            </div>

            <LinkList
              term="인용 문서"
              items={neighbors.criterionDocuments.get(node.id) ?? []}
            />
            <LinkList
              term="인용 증적"
              items={neighbors.criterionEvidence.get(node.id) ?? []}
            />

            {findingRow ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="w-full"
                onClick={() => onOpenFinding(findingRow)}
              >
                판정 상세 보기
              </Button>
            ) : (
              <div className="space-y-1.5">
                <Button type="button" size="sm" variant="outline" className="w-full" disabled>
                  판정 상세 보기
                </Button>
                <p className="text-xs text-muted-foreground">{criterionNotice}</p>
              </div>
            )}
          </div>
        ) : null}

        {node.type === "document" ? (
          <div className="space-y-2">
            <Badge className={cn(DOCUMENT_STATUS_CLASSES[node.status])}>
              {documentStatusLabel(node.status)}
            </Badge>
            <CodeList
              term="이 문서를 인용한 항목"
              codes={neighbors.documentCriteria.get(node.id) ?? []}
            />
          </div>
        ) : null}

        {node.type === "evidence" ? (
          <div className="space-y-2">
            <dl className="space-y-1 text-xs">
              <Row term="수집원" value={node.source} />
              <Row term="점검" value={node.check_id} />
              <Row
                term="결과"
                value={EVIDENCE_STATUS_LABELS[node.status] ?? node.status}
              />
              <Row term="수집 시각" value={formatDateTime(node.collected_at)} />
            </dl>
            <CodeList
              term="매핑된 항목"
              codes={neighbors.evidenceCriteria.get(node.id) ?? []}
            />
          </div>
        ) : null}

        {node.type === "alert" ? (
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary" className="text-xs">
                {ALERT_TYPE_LABELS[node.alert_type] ?? node.alert_type}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {node.read ? "읽음" : "읽지 않음"}
              </span>
            </div>
            <LinkList
              term="발생 증적"
              items={
                neighbors.alertEvidence.has(node.id)
                  ? [neighbors.alertEvidence.get(node.id) as string]
                  : []
              }
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** 이름 목록 한 덩어리. 비어 있으면 "없음"으로 남긴다(빈 자리를 숨기지 않는다). */
function LinkList({ term, items }: { term: string; items: string[] }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">
        {term} {items.length > 0 ? items.length : ""}
      </p>
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground/70">없음</p>
      ) : (
        <ul className="space-y-0.5">
          {items.map((item) => (
            <li key={item} className="break-words text-xs leading-snug">
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 항목 코드 목록. 너무 길어지지 않게 앞쪽만 보여 주고 나머지는 개수로 접는다. */
function CodeList({ term, codes }: { term: string; codes: string[] }) {
  const shown = codes.slice(0, CODE_LIST_LIMIT);
  const rest = codes.length - shown.length;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">
        {term} {codes.length}
      </p>
      {codes.length === 0 ? (
        <p className="text-xs text-muted-foreground/70">없음</p>
      ) : (
        <div className="flex flex-wrap gap-1">
          {shown.map((code) => (
            <Badge key={code} variant="outline" className="tabular-nums font-normal">
              {code}
            </Badge>
          ))}
          {rest > 0 ? (
            <span className="text-xs text-muted-foreground">외 {rest}개</span>
          ) : null}
        </div>
      )}
    </div>
  );
}

/** 정보 카드의 용어–값 한 줄. */
function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words font-mono text-[11px]">{value}</dd>
    </div>
  );
}
