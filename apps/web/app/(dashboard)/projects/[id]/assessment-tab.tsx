"use client";

/**
 * 모의심사 탭. 실행 버튼 · 진행률 · 장별 준비도 · 판정 분포 · 실행 이력.
 *
 * 실행 상태는 상위 페이지의 `useAssessments` 훅이 들고 있고(리포트 탭과 공유),
 * 이 컴포넌트는 표시와 실행 요청만 맡는다.
 */

import { Check } from "lucide-react";
import * as React from "react";

import type { UseAssessmentsResult } from "@/app/(dashboard)/projects/[id]/use-assessments";
import { ProgressBar } from "@/components/progress-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ASSESSMENT_STATUS_CLASSES,
  ASSESSMENT_STATUS_LABELS,
  CHAPTERS,
  CHAPTER_LABELS,
  CHAPTER_SHORT_LABELS,
  FINDING_STATUS_CLASSES,
  FINDING_STATUS_LABELS,
  FINDING_STATUS_ORDER,
  formatCostUsd,
  formatDateTime,
  formatPercent,
  toPercent,
} from "@/lib/labels";
import type {
  Assessment,
  ChapterSummary,
  CriterionChapter,
  FindingCounts,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export function AssessmentTab({
  assessments,
  onGoToReport,
}: {
  assessments: UseAssessmentsResult;
  /** 완료 후 리포트 탭으로 이동시키는 콜백. */
  onGoToReport: () => void;
}) {
  const {
    assessments: history,
    selected,
    selectedId,
    selectAssessment,
    isRunning,
    isStarting,
    notReady,
    error,
    start,
  } = assessments;

  const summaryJson = selected?.summary_json ?? null;
  const progress = summaryJson?.progress ?? null;
  const progressPercent =
    progress && progress.total > 0
      ? (progress.done / progress.total) * 100
      : null;
  // 집계(counts·by_chapter)는 판정이 끝난 뒤에야 채워진다. 진행 중에는 progress 만 온다.
  const counts = summaryJson?.counts ?? null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4 space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="text-base">모의심사</CardTitle>
            <CardDescription>
              업로드한 문서와 수집한 증적을 인증기준 101개 항목과 대조해 충족 여부를 판정합니다.
            </CardDescription>
          </div>
          <Button onClick={() => void start()} disabled={isStarting || isRunning}>
            {isStarting
              ? "실행 요청 중…"
              : isRunning
                ? "진행 중…"
                : "모의심사 실행"}
          </Button>
        </CardHeader>

        {selected ? (
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <Badge className={cn(ASSESSMENT_STATUS_CLASSES[selected.status])}>
                {ASSESSMENT_STATUS_LABELS[selected.status]}
              </Badge>
              <span className="text-muted-foreground">
                시작 {formatDateTime(selected.started_at)}
              </span>
              {selected.finished_at ? (
                <span className="text-muted-foreground">
                  종료 {formatDateTime(selected.finished_at)}
                </span>
              ) : null}
              {selected.model ? (
                <span className="text-muted-foreground">모델 {selected.model}</span>
              ) : null}
              <span className="text-muted-foreground">
                비용 {formatCostUsd(selected.cost_usd)}
              </span>
            </div>

            {isRunning ? (
              <div className="space-y-1.5">
                <ProgressBar
                  value={progressPercent}
                  label="모의심사 진행률"
                />
                <p className="text-xs text-muted-foreground">
                  {progress
                    ? `${progress.done} / ${progress.total} 항목 판정 완료 (${formatPercent(progressPercent)})`
                    : "판정을 준비하고 있습니다…"}
                  {" · 2초마다 자동으로 갱신됩니다."}
                </p>
              </div>
            ) : null}

            {selected.status === "failed" ? (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                이 실행은 실패했습니다. 문서 분석이 끝났는지 확인한 뒤 다시 실행해 주세요.
              </p>
            ) : null}

            {selected.status === "done" ? (
              <Button variant="outline" size="sm" onClick={onGoToReport}>
                리포트에서 항목별 판정 보기
              </Button>
            ) : null}
          </CardContent>
        ) : null}
      </Card>

      {error ? (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      ) : null}

      {notReady ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            모의심사 API 연동을 준비하고 있습니다. 연동이 끝나면 실행 결과가 여기에 표시됩니다.
          </CardContent>
        </Card>
      ) : history === null ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            실행 이력을 불러오는 중입니다…
          </CardContent>
        </Card>
      ) : history.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
            <p className="text-sm font-medium">아직 실행한 모의심사가 없습니다.</p>
            <p className="max-w-md text-sm text-muted-foreground">
              문서 탭에서 정책·지침 문서를 올리고 분석이 끝나면 ‘모의심사 실행’을 눌러 주세요.
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          {counts ? (
            <>
              <ChapterReadinessCards byChapter={summaryJson?.by_chapter ?? {}} />
              <StatusDistribution counts={counts} />
            </>
          ) : (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                {isRunning
                  ? "판정이 끝나면 장별 준비도와 판정 분포가 표시됩니다."
                  : "이 실행에는 집계 결과가 없습니다."}
              </CardContent>
            </Card>
          )}

          <HistoryTable
            history={history}
            selectedId={selectedId}
            onSelect={selectAssessment}
          />
        </>
      )}
    </div>
  );
}

/** 장별 준비도 카드 3개(1장 관리체계 / 2장 보호대책 / 3장 개인정보). */
function ChapterReadinessCards({
  byChapter,
}: {
  /** `summary_json.by_chapter`. 키는 장 번호 문자열. */
  byChapter: Partial<Record<CriterionChapter, ChapterSummary>>;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {CHAPTERS.map((chapter) => {
        const stats = byChapter[chapter];
        const percent = toPercent(stats?.readiness ?? null);

        return (
          <Card key={chapter}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">
                {CHAPTER_SHORT_LABELS[chapter]}
              </CardTitle>
              <CardDescription className="text-xs">
                {CHAPTER_LABELS[chapter]}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-semibold tabular-nums">
                  {percent === null ? "—" : `${Math.round(percent)}%`}
                </span>
                <span className="text-xs text-muted-foreground">준비도</span>
              </div>
              <ProgressBar
                value={percent}
                label={`${CHAPTER_SHORT_LABELS[chapter]} 준비도`}
              />
              <p className="text-xs text-muted-foreground">
                {stats
                  ? `총 ${stats.total}개 · 충족 ${stats.met} · 부분충족 ${stats.partial} · 미충족 ${stats.unmet} · 판단불가 ${stats.unknown}`
                  : "집계 결과가 없습니다."}
              </p>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

/** 전체 판정 분포 뱃지. */
function StatusDistribution({ counts }: { counts: FindingCounts }) {
  const total = FINDING_STATUS_ORDER.reduce(
    (sum, status) => sum + (counts[status] ?? 0),
    0,
  );

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">판정 분포</CardTitle>
        <CardDescription className="text-xs">
          전체 {total}개 항목 기준입니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-2">
        {FINDING_STATUS_ORDER.map((status) => (
          <Badge
            key={status}
            className={cn("gap-1.5 px-3 py-1", FINDING_STATUS_CLASSES[status])}
          >
            <span>{FINDING_STATUS_LABELS[status]}</span>
            <span className="tabular-nums">{counts[status] ?? 0}</span>
          </Badge>
        ))}
      </CardContent>
    </Card>
  );
}

/** 실행 이력. 행을 누르면 화면 전체가 그 실행 기준으로 바뀐다. */
function HistoryTable({
  history,
  selectedId,
  onSelect,
}: {
  history: Assessment[];
  selectedId: string | null;
  onSelect: (assessmentId: string) => void;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">실행 이력</CardTitle>
        <CardDescription className="text-xs">
          과거 실행을 선택하면 그때의 결과와 리포트를 볼 수 있습니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            {/* 좁은 화면에서는 일시·상태·선택 표시만 남기고 진행·비용은 일시 아래로 내린다. */}
            <TableRow>
              <TableHead className="whitespace-nowrap">실행 일시</TableHead>
              <TableHead className="w-[96px] sm:w-[110px]">상태</TableHead>
              <TableHead className="hidden w-[130px] sm:table-cell">진행</TableHead>
              <TableHead className="hidden w-[110px] sm:table-cell">비용</TableHead>
              <TableHead className="w-10 sm:w-[90px]" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {history.map((item) => {
              const isSelected = item.id === selectedId;
              const progress = item.summary_json?.progress;

              return (
                <TableRow
                  key={item.id}
                  className={cn("cursor-pointer", isSelected && "bg-muted/60")}
                  onClick={() => onSelect(item.id)}
                >
                  <TableCell className="whitespace-nowrap font-medium">
                    {formatDateTime(item.started_at ?? item.finished_at)}
                    <span className="mt-0.5 block whitespace-nowrap text-xs font-normal tabular-nums text-muted-foreground sm:hidden">
                      {progress ? `${progress.done} / ${progress.total} 항목` : "—"}
                      {` · ${formatCostUsd(item.cost_usd)}`}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge
                      className={cn(
                        "whitespace-nowrap",
                        ASSESSMENT_STATUS_CLASSES[item.status],
                      )}
                    >
                      {ASSESSMENT_STATUS_LABELS[item.status]}
                    </Badge>
                  </TableCell>
                  <TableCell className="hidden whitespace-nowrap tabular-nums text-muted-foreground sm:table-cell">
                    {progress ? `${progress.done} / ${progress.total}` : "—"}
                  </TableCell>
                  <TableCell className="hidden whitespace-nowrap tabular-nums sm:table-cell">
                    {formatCostUsd(item.cost_usd)}
                  </TableCell>
                  <TableCell className="px-1 text-center text-xs text-muted-foreground sm:px-2 sm:text-right">
                    {isSelected ? (
                      <span className="inline-flex items-center">
                        <Check className="size-4 shrink-0 sm:hidden" aria-hidden />
                        <span className="sr-only sm:not-sr-only">선택됨</span>
                      </span>
                    ) : null}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
