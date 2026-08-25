"use client";

/**
 * 리포트 탭. 선택된 모의심사 실행의 항목별 판정 테이블.
 *
 * 항목이 101개뿐이라 목록은 한 번만 받아 오고 필터·정렬은 화면에서 처리한다
 * (다중 상태 필터의 쿼리 인코딩이 계약에 정해져 있지 않아 서버 필터에 기대지 않는다).
 */

import * as React from "react";
import { toast } from "sonner";

import { FindingDetailSheet } from "@/app/(dashboard)/projects/[id]/finding-detail-sheet";
import type { UseAssessmentsResult } from "@/app/(dashboard)/projects/[id]/use-assessments";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, assessmentsApi, toMessage } from "@/lib/api";
import { dashboardApi } from "@/lib/api-dashboard";
import {
  CHAPTERS,
  CHAPTER_SHORT_LABELS,
  DECIDED_BY_LABELS,
  FINDING_STATUS_CLASSES,
  FINDING_STATUS_LABELS,
  FINDING_STATUS_ORDER,
  fileDateStamp,
  findingSeverity,
  formatPercent,
  sanitizeFileName,
  toPercent,
} from "@/lib/labels";
import type {
  CriterionChapter,
  FindingRow,
  FindingStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/** 테이블 정렬 기준. */
type SortKey = "code" | "status" | "confidence";

const SORT_LABELS: Record<SortKey, string> = {
  code: "코드순",
  status: "판정순(미충족 우선)",
  confidence: "신뢰도 높은순",
};

/** 받아 온 Blob 을 파일로 저장한다(XLSX·ZIP 공통). */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function ReportTab({
  projectId,
  projectName,
  assessments,
  onGoToAssessment,
}: {
  projectId: string;
  projectName: string;
  assessments: UseAssessmentsResult;
  /** 실행할 모의심사가 없을 때 모의심사 탭으로 보내는 콜백. */
  onGoToAssessment: () => void;
}) {
  const { selected, notReady } = assessments;
  const assessmentId = selected?.id ?? null;

  const [findings, setFindings] = React.useState<FindingRow[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [statusFilter, setStatusFilter] = React.useState<FindingStatus[]>([]);
  const [chapterFilter, setChapterFilter] = React.useState<
    CriterionChapter | "all"
  >("all");
  const [query, setQuery] = React.useState("");
  const [sortKey, setSortKey] = React.useState<SortKey>("code");
  const [openFinding, setOpenFinding] = React.useState<FindingRow | null>(null);
  const [downloading, setDownloading] = React.useState(false);
  const [packaging, setPackaging] = React.useState(false);

  const canShowFindings = selected?.status === "done";

  React.useEffect(() => {
    if (!assessmentId || !canShowFindings) {
      setFindings(null);
      return;
    }
    const controller = new AbortController();
    setFindings(null);
    setLoadError(null);

    assessmentsApi
      .findings(projectId, assessmentId, {}, controller.signal)
      .then((rows) => setFindings(rows))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && (err.status === 404 || err.status === 501)) {
          setFindings([]);
          setLoadError(
            "판정 목록 API 연동을 준비하고 있습니다. 연동이 끝나면 여기에 표시됩니다.",
          );
          return;
        }
        setFindings([]);
        setLoadError(toMessage(err));
      });

    return () => controller.abort();
  }, [assessmentId, canShowFindings, projectId]);

  const visible = React.useMemo(() => {
    if (!findings) return [];
    const term = query.trim().toLowerCase();

    const filtered = findings.filter((row) => {
      if (statusFilter.length > 0 && !statusFilter.includes(row.status)) {
        return false;
      }
      // 장 번호는 서버가 판정마다 숫자로 실어 준다(코드 파싱보다 이쪽이 확실하다).
      if (chapterFilter !== "all" && String(row.chapter) !== chapterFilter) {
        return false;
      }
      if (term) {
        const haystack = [
          row.criterion_code,
          row.title,
          row.section,
          row.predicted_defect ?? "",
          row.recommendation ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });

    const sorted = [...filtered];
    sorted.sort((a, b) => {
      if (sortKey === "status") {
        const diff = findingSeverity(a.status) - findingSeverity(b.status);
        if (diff !== 0) return diff;
      } else if (sortKey === "confidence") {
        // 신뢰도가 없는 항목은 뒤로 보낸다.
        const left = toPercent(a.confidence) ?? -1;
        const right = toPercent(b.confidence) ?? -1;
        if (left !== right) return right - left;
      }
      return a.criterion_code.localeCompare(b.criterion_code, "ko", {
        numeric: true,
      });
    });

    return sorted;
  }, [chapterFilter, findings, query, sortKey, statusFilter]);

  function toggleStatus(status: FindingStatus) {
    setStatusFilter((prev) =>
      prev.includes(status)
        ? prev.filter((item) => item !== status)
        : [...prev, status],
    );
  }

  async function handleExport() {
    if (!assessmentId) return;
    setDownloading(true);
    try {
      const blob = await assessmentsApi.report(projectId, assessmentId);
      const stamp = fileDateStamp(selected?.finished_at ?? selected?.started_at);
      saveBlob(blob, `갭리포트_${sanitizeFileName(projectName)}_${stamp}.xlsx`);
      toast.success("갭 리포트를 내려받았습니다.");
    } catch (err) {
      toast.error(toMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  /** 증적 패키지 ZIP 내려받기(PRD §7 F7). */
  async function handleExportPackage() {
    if (!assessmentId) return;
    setPackaging(true);
    try {
      const blob = await dashboardApi.evidencePackage(projectId, assessmentId);
      const stamp = fileDateStamp(selected?.finished_at ?? selected?.started_at);
      saveBlob(blob, `증적패키지_${sanitizeFileName(projectName)}_${stamp}.zip`);
      toast.success("증적 패키지를 내려받았습니다.");
    } catch (err) {
      toast.error(toMessage(err));
    } finally {
      setPackaging(false);
    }
  }

  if (notReady) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-sm text-muted-foreground">
          모의심사 API 연동을 준비하고 있습니다. 연동이 끝나면 갭 리포트가 여기에 표시됩니다.
        </CardContent>
      </Card>
    );
  }

  if (!selected) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <p className="text-sm font-medium">아직 볼 수 있는 갭 리포트가 없습니다.</p>
          <p className="max-w-md text-sm text-muted-foreground">
            모의심사 탭에서 실행을 마치면 항목별 판정과 XLSX 내보내기를 쓸 수 있습니다.
          </p>
          <Button variant="outline" size="sm" onClick={onGoToAssessment}>
            모의심사 탭으로 이동
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (!canShowFindings) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <p className="text-sm font-medium">
            {selected.status === "failed"
              ? "선택한 실행이 실패해 리포트를 만들 수 없습니다."
              : "모의심사가 진행 중입니다."}
          </p>
          <p className="max-w-md text-sm text-muted-foreground">
            {selected.status === "failed"
              ? "모의심사 탭에서 다시 실행해 주세요."
              : "판정이 끝나면 항목별 결과가 이곳에 표시됩니다."}
          </p>
          <Button variant="outline" size="sm" onClick={onGoToAssessment}>
            모의심사 탭으로 이동
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-4 p-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">판정 상태</Label>
              <div className="flex flex-wrap gap-2">
                {FINDING_STATUS_ORDER.map((status) => {
                  const active = statusFilter.includes(status);
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
                {statusFilter.length > 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => setStatusFilter([])}
                  >
                    선택 해제
                  </Button>
                ) : null}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                onClick={() => void handleExport()}
                disabled={downloading}
              >
                {downloading ? "내보내는 중…" : "XLSX 내보내기"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => void handleExportPackage()}
                disabled={packaging}
              >
                {packaging ? "패키지 만드는 중…" : "증적 패키지(ZIP)"}
              </Button>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="report-chapter" className="text-xs text-muted-foreground">
                장
              </Label>
              <Select
                value={chapterFilter}
                onValueChange={(value) =>
                  setChapterFilter(value as CriterionChapter | "all")
                }
              >
                <SelectTrigger id="report-chapter">
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
              <Label htmlFor="report-query" className="text-xs text-muted-foreground">
                검색어
              </Label>
              <Input
                id="report-query"
                value={query}
                placeholder="코드·항목명·결함 내용"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="report-sort" className="text-xs text-muted-foreground">
                정렬
              </Label>
              <Select
                value={sortKey}
                onValueChange={(value) => setSortKey(value as SortKey)}
              >
                <SelectTrigger id="report-sort">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
                    <SelectItem key={key} value={key}>
                      {SORT_LABELS[key]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            {findings === null
              ? "판정 목록을 불러오는 중입니다…"
              : `전체 ${findings.length}건 중 ${visible.length}건 표시`}
          </p>
        </CardContent>
      </Card>

      {loadError ? (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {loadError}
        </p>
      ) : null}

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[90px]">코드</TableHead>
                <TableHead>항목명</TableHead>
                <TableHead className="w-[110px]">판정</TableHead>
                <TableHead className="w-[90px]">신뢰도</TableHead>
                <TableHead className="w-[100px]">판정 주체</TableHead>
                <TableHead className="w-[280px]">예상 결함</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {findings === null ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-12 text-center text-sm text-muted-foreground"
                  >
                    판정 목록을 불러오는 중입니다…
                  </TableCell>
                </TableRow>
              ) : visible.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-12 text-center text-sm text-muted-foreground"
                  >
                    조건에 맞는 항목이 없습니다. 필터를 바꿔 보세요.
                  </TableCell>
                </TableRow>
              ) : (
                visible.map((row) => (
                  <TableRow
                    key={row.id}
                    className="cursor-pointer"
                    onClick={() => setOpenFinding(row)}
                  >
                    <TableCell className="font-medium tabular-nums">
                      {row.criterion_code}
                    </TableCell>
                    <TableCell>
                      <span className="font-medium">{row.title}</span>
                      <span className="ml-2 text-xs text-muted-foreground">
                        {row.section}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge className={cn(FINDING_STATUS_CLASSES[row.status])}>
                        {FINDING_STATUS_LABELS[row.status]}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {formatPercent(row.confidence)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {DECIDED_BY_LABELS[row.decided_by] ?? row.decided_by}
                    </TableCell>
                    <TableCell
                      className="max-w-[280px] truncate text-muted-foreground"
                      title={row.predicted_defect ?? undefined}
                    >
                      {row.predicted_defect?.trim() ? row.predicted_defect : "—"}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {assessmentId ? (
        <FindingDetailSheet
          projectId={projectId}
          assessmentId={assessmentId}
          finding={openFinding}
          onOpenChange={(open) => {
            if (!open) setOpenFinding(null);
          }}
          highlight={query}
        />
      ) : null}
    </div>
  );
}
