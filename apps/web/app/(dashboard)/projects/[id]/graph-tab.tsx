"use client";

/**
 * 지식 그래프 탭 (PRD §7 F3·F5·F8).
 *
 * "무엇이 무엇의 근거인가"를 한 장으로 보여 준다.
 *   장 → 절 → 항목(compound) · 문서 · 클라우드 증적 · 알림을 노드로,
 *   근거 인용(항목→문서 / 항목→증적) · 점검 매핑(증적→항목) · 알림 발생을 엣지로 그린다.
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
  computeGraphStats,
  hiddenNodeIds,
} from "@/app/(dashboard)/projects/[id]/graph-elements";
import type { UseAssessmentsResult } from "@/app/(dashboard)/projects/[id]/use-assessments";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
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
  FINDING_STATUS_LABELS,
  FINDING_STATUS_ORDER,
  documentStatusLabel,
  formatDateTime,
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

/** 판정별 도트 색. 리포트 뱃지와 같은 토큰을 쓴다. */
const STATUS_DOT_CLASSES: Record<FindingStatus, string> = {
  met: "bg-success",
  partial: "bg-warning",
  unmet: "bg-destructive",
  unknown: "bg-secondary border border-muted-foreground",
};

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

/** 범례에 쓰는 노드 종류 설명. */
const NODE_LEGEND: { label: string; className: string }[] = [
  { label: "충족", className: "bg-success" },
  { label: "부분충족", className: "bg-warning" },
  { label: "미충족", className: "bg-destructive" },
  { label: "판단불가", className: "bg-secondary border border-muted-foreground" },
  { label: "문서", className: "bg-primary" },
  { label: "증적", className: "bg-muted-foreground" },
];

/** 범례에 쓰는 엣지 종류 설명. */
const EDGE_LEGEND: { label: string; className: string }[] = [
  { label: "문서 근거 인용", className: "bg-muted-foreground" },
  { label: "증적 근거 인용", className: "bg-primary" },
  { label: "점검 항목 매핑", className: "bg-border" },
];

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
  // 판정 상세 시트는 탭 시점의 판정으로만 연다(로딩이 끝났다고 저절로 열리지 않게).
  const [sheetFinding, setSheetFinding] = React.useState<FindingRow | null>(null);
  const [findings, setFindings] = React.useState<FindingRow[] | null>(null);
  const canvasRef = React.useRef<GraphCanvasHandle | null>(null);

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
        // 그래프 자체는 이미 그려졌으므로 상세만 포기한다(정보 패널로 대체된다).
        setFindings([]);
      });

    return () => controller.abort();
  }, [assessmentId, projectId]);

  const findingByCode = React.useMemo(
    () => new Map((findings ?? []).map((row) => [row.criterion_code, row])),
    [findings],
  );
  // 탭 핸들러는 캔버스 재생성을 막으려 의존성이 없다. 최신 맵은 ref 로 넘겨받는다.
  const findingByCodeRef = React.useRef(findingByCode);
  React.useEffect(() => {
    findingByCodeRef.current = findingByCode;
  }, [findingByCode]);

  const hidden = React.useMemo(
    () => (graph ? hiddenNodeIds(graph, filters) : new Set<string>()),
    [filters, graph],
  );
  const stats = React.useMemo(
    () => (graph ? computeGraphStats(graph, hidden) : null),
    [graph, hidden],
  );

  const handleTapNode = React.useCallback((node: ProjectGraphNode | null) => {
    // 장·절 상자는 배경과 같이 취급한다(자체 정보가 없다).
    if (node && (node.type === "chapter" || node.type === "section")) {
      setSelected(null);
      setSheetFinding(null);
      canvasRef.current?.clearSelection();
      return;
    }
    // 항목에 판정이 이미 로드돼 있으면 바로 판정 상세 시트를 연다.
    if (node?.type === "criterion") {
      const row = findingByCodeRef.current.get(node.code);
      if (row) {
        setSelected(null);
        setSheetFinding(row);
        return;
      }
    }
    setSheetFinding(null);
    setSelected(node);
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

  if (!graph || !stats) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-[560px] w-full" />
      </div>
    );
  }

  // 판정 상세 시트로 열지 않은 선택(문서·증적·알림·판정 없는 항목)은 정보 패널로 보여 준다.
  const panelNode = selected;
  // 판정 없는 항목 패널의 안내 문구. 심사 유무·판정 로딩 상태에 따라 달라진다.
  const criterionNotice =
    assessmentId === null
      ? "모의심사를 실행하면 판정이 표시됩니다."
      : findings === null
        ? "판정을 불러오는 중입니다. 잠시 후 노드를 다시 클릭해 주세요."
        : panelNode?.type === "criterion" && findingByCode.has(panelNode.code)
          ? "판정을 불러왔습니다. 노드를 다시 클릭하면 판정 상세가 열립니다."
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
        <CardContent className="space-y-4 p-6">
          {/* 통계 헤더 ------------------------------------------------ */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-2">
              <p className="text-sm font-medium tabular-nums">
                {`항목 ${stats.criteria} / ${stats.criteriaTotal} · 문서 ${stats.documents} · 증적 ${stats.evidence} · 연결 ${stats.edges}건`}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                {FINDING_STATUS_ORDER.map((status) => (
                  <span
                    key={status}
                    className="flex items-center gap-1.5 text-xs text-muted-foreground"
                  >
                    <span
                      aria-hidden
                      className={cn("size-2.5 rounded-full", STATUS_DOT_CLASSES[status])}
                    />
                    {FINDING_STATUS_LABELS[status]} {stats[status]}
                  </span>
                ))}
              </div>
            </div>

            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => canvasRef.current?.fit()}
            >
              보기 초기화
            </Button>
          </div>

          {/* 필터 ---------------------------------------------------- */}
          <div className="grid gap-4 lg:grid-cols-[minmax(0,180px)_1fr_auto]">
            <div className="space-y-1.5">
              <Label htmlFor="graph-chapter" className="text-xs text-muted-foreground">
                장
              </Label>
              <Select
                value={filters.chapter === "all" ? "all" : String(filters.chapter)}
                onValueChange={(value) =>
                  setFilters((prev) => ({
                    ...prev,
                    chapter: value === "all" ? "all" : (Number(value) as 1 | 2 | 3),
                  }))
                }
              >
                <SelectTrigger id="graph-chapter">
                  <SelectValue placeholder="전체" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">전체</SelectItem>
                  {CHAPTERS.map((chapter) => (
                    <SelectItem key={chapter} value={chapter}>
                      {CHAPTER_SHORT_LABELS[chapter]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">판정 상태</Label>
              <div className="flex flex-wrap gap-2">
                {FINDING_STATUS_ORDER.map((status) => {
                  const active = filters.statuses.includes(status);
                  return (
                    <Button
                      key={status}
                      type="button"
                      size="sm"
                      variant={active ? "default" : "outline"}
                      aria-pressed={active}
                      onClick={() => toggleStatus(status)}
                    >
                      {FINDING_STATUS_LABELS[status]}
                    </Button>
                  );
                })}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">노드 종류</Label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant={filters.showDocuments ? "default" : "outline"}
                  aria-pressed={filters.showDocuments}
                  onClick={() =>
                    setFilters((prev) => ({
                      ...prev,
                      showDocuments: !prev.showDocuments,
                    }))
                  }
                >
                  문서
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={filters.showEvidence ? "default" : "outline"}
                  aria-pressed={filters.showEvidence}
                  onClick={() =>
                    setFilters((prev) => ({
                      ...prev,
                      showEvidence: !prev.showEvidence,
                    }))
                  }
                >
                  증적
                </Button>
              </div>
            </div>
          </div>

          {/* 범례 ---------------------------------------------------- */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-3 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">범례</span>
            {NODE_LEGEND.map((item) => (
              <span key={item.label} className="flex items-center gap-1.5">
                <span aria-hidden className={cn("size-2.5 rounded-full", item.className)} />
                {item.label}
              </span>
            ))}
            {EDGE_LEGEND.map((item) => (
              <span key={item.label} className="flex items-center gap-1.5">
                <span aria-hidden className={cn("h-px w-5", item.className)} />
                {item.label}
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* 캔버스 --------------------------------------------------- */}
      <div className="relative h-[560px] w-full rounded-md border bg-card">
        <GraphCanvas
          ref={canvasRef}
          graph={graph}
          filters={filters}
          onTapNode={handleTapNode}
        />
        {panelNode ? (
          <NodeInfoPanel
            node={panelNode}
            criterionNotice={criterionNotice}
            onClose={() => {
              setSelected(null);
              canvasRef.current?.clearSelection();
            }}
          />
        ) : null}
      </div>

      {assessmentId && sheetFinding ? (
        <FindingDetailSheet
          projectId={projectId}
          assessmentId={assessmentId}
          finding={sheetFinding}
          onOpenChange={(open) => {
            if (!open) {
              setSheetFinding(null);
              canvasRef.current?.clearSelection();
            }
          }}
        />
      ) : null}
    </div>
  );
}

/** 캔버스 위에 겹쳐 띄우는 노드 정보 패널. 판정 상세가 없는 노드를 위한 자리다. */
function NodeInfoPanel({
  node,
  criterionNotice,
  onClose,
}: {
  node: ProjectGraphNode;
  /** 판정 없는 항목 노드에 보여 줄 안내 문구. */
  criterionNotice: string;
  onClose: () => void;
}) {
  return (
    <Card className="absolute right-3 top-3 w-64 shadow-md">
      <CardContent className="space-y-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 break-words text-sm font-medium">{node.label}</p>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="-mr-2 -mt-2 size-7 shrink-0"
            aria-label="정보 패널 닫기"
            onClick={onClose}
          >
            <X className="size-4" />
          </Button>
        </div>

        {node.type === "criterion" ? (
          <div className="space-y-1.5">
            <Badge variant="outline" className="tabular-nums">
              {node.code}
            </Badge>
            <p className="text-xs text-muted-foreground">{criterionNotice}</p>
          </div>
        ) : null}

        {node.type === "document" ? (
          <div className="space-y-1.5">
            <Badge className={cn(DOCUMENT_STATUS_CLASSES[node.status])}>
              {documentStatusLabel(node.status)}
            </Badge>
            <p className="text-xs text-muted-foreground">업로드 문서</p>
          </div>
        ) : null}

        {node.type === "evidence" ? (
          <dl className="space-y-1 text-xs">
            <Row term="수집원" value={node.source} />
            <Row term="점검" value={node.check_id} />
            <Row term="결과" value={EVIDENCE_STATUS_LABELS[node.status] ?? node.status} />
            <Row term="수집 시각" value={formatDateTime(node.collected_at)} />
          </dl>
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
            <p className="text-xs text-muted-foreground">{node.label}</p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** 정보 패널의 용어–값 한 줄. */
function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words font-mono text-[11px]">{value}</dd>
    </div>
  );
}
