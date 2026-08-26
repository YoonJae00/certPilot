"use client";

/**
 * 검수 상세 화면(PRD §7 F6).
 *
 * 열면 그 순간 과제가 나에게 배정된다(서버가 claim 한다). 편집은 로컬에서 모아 두었다가
 * "저장"으로 한 번에 보내고, 서버는 그때 DOCX 를 다시 만든다. 승인·반려는 되돌릴 수
 * 없으므로 확인 다이얼로그를 거친다.
 */

import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  ApproveDialog,
  ReturnDialog,
} from "@/app/(dashboard)/review/[taskId]/decision-dialogs";
import { PolicyEditor } from "@/app/(dashboard)/review/[taskId]/policy-editor";
import { SowEditor } from "@/app/(dashboard)/review/[taskId]/sow-editor";
import {
  REVIEW_STATUS_CLASSES,
  draftKindLabel,
  reviewStatusLabel,
} from "@/app/(dashboard)/review/labels";
import { useUser } from "@/components/user-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { isUnauthorized, toMessage } from "@/lib/api";
import { needsReviewCount, reviewApi } from "@/lib/api-review";
import { formatDateTime } from "@/lib/labels";
import { cn } from "@/lib/utils";
import type {
  PolicySection,
  ReviewContentPatch,
  ReviewTaskDetail,
  SowEditableField,
  SowRow,
} from "@/lib/types-review";

export default function ReviewTaskPage() {
  const params = useParams<{ taskId: string }>();
  const taskId = params.taskId;
  const router = useRouter();
  const { user } = useUser();

  const [task, setTask] = React.useState<ReviewTaskDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // 서버에 보내기 전 로컬 편집본. 저장에 성공하면 서버 응답으로 갈아 끼운다.
  const [rows, setRows] = React.useState<SowRow[]>([]);
  const [sections, setSections] = React.useState<PolicySection[]>([]);
  const [dirtyRows, setDirtyRows] = React.useState<Set<number>>(new Set());
  const [dirtySections, setDirtySections] = React.useState<Set<number>>(
    new Set(),
  );

  const [saving, setSaving] = React.useState(false);
  const [deciding, setDeciding] = React.useState(false);
  const [approveOpen, setApproveOpen] = React.useState(false);
  const [returnOpen, setReturnOpen] = React.useState(false);

  /** 서버 응답을 화면 상태에 반영하고 편집 표시를 지운다. */
  const applyTask = React.useCallback((next: ReviewTaskDetail) => {
    setTask(next);
    setRows(next.content_json.rows ?? []);
    setSections(next.content_json.sections ?? []);
    setDirtyRows(new Set());
    setDirtySections(new Set());
  }, []);

  React.useEffect(() => {
    const controller = new AbortController();

    reviewApi
      .get(taskId, controller.signal)
      .then(applyTask)
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (isUnauthorized(err)) {
          router.replace("/login");
          return;
        }
        setError(toMessage(err));
      });

    return () => controller.abort();
  }, [taskId, applyTask, router]);

  const isReviewer = user?.role === "reviewer";
  const isPending = task?.status === "pending";
  // 운영자는 열람만 한다. 이미 결정된 과제도 읽기 전용이다.
  const readOnly = !isReviewer || !isPending;
  const dirtyCount = dirtyRows.size + dirtySections.size;

  const needsReview = React.useMemo(() => {
    if (!task) return 0;
    if (task.draft.kind === "sow") return needsReviewCount({ rows });
    return needsReviewCount({ sections });
  }, [task, rows, sections]);

  function handleRowChange(
    rowIndex: number,
    field: SowEditableField,
    value: string,
  ) {
    setRows((prev) =>
      prev.map((row, index) =>
        index === rowIndex ? { ...row, [field]: value } : row,
      ),
    );
    setDirtyRows((prev) => new Set(prev).add(rowIndex));
  }

  function handleSectionChange(sectionIndex: number, body: string) {
    setSections((prev) =>
      prev.map((section, index) =>
        index === sectionIndex ? { ...section, body } : section,
      ),
    );
    setDirtySections((prev) => new Set(prev).add(sectionIndex));
  }

  async function handleSave() {
    if (!task || dirtyCount === 0) return;

    const ascending = (a: number, b: number) => a - b;
    const patch: ReviewContentPatch =
      task.draft.kind === "sow"
        ? {
            rows: Array.from(dirtyRows)
              .sort(ascending)
              .map((index) => ({
                row_index: index,
                fields: {
                  operation_status: rows[index].operation_status,
                  owner_dept: rows[index].owner_dept,
                  note: rows[index].note,
                },
              })),
          }
        : {
            sections: Array.from(dirtySections)
              .sort(ascending)
              .map((index) => ({
                section_index: index,
                body: sections[index].body,
              })),
          };

    setSaving(true);
    try {
      applyTask(await reviewApi.editContent(task.id, patch));
      toast.success("편집 내용을 저장했습니다. 문서를 다시 만들었습니다.");
    } catch (err) {
      toast.error(toMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleApprove(comment: string) {
    if (!task) return;
    setDeciding(true);
    try {
      await reviewApi.approve(task.id, comment || undefined);
      toast.success("승인했습니다. 고객사가 문서를 내려받을 수 있습니다.");
      setApproveOpen(false);
      router.push("/review");
    } catch (err) {
      toast.error(toMessage(err));
      setDeciding(false);
    }
  }

  async function handleReturn(comment: string) {
    if (!task) return;
    setDeciding(true);
    try {
      await reviewApi.sendBack(task.id, comment);
      toast.success("반려했습니다. 고객사에 사유를 전달했습니다.");
      setReturnOpen(false);
      router.push("/review");
    } catch (err) {
      toast.error(toMessage(err));
      setDeciding(false);
    }
  }

  if (error) {
    return (
      <div className="space-y-4">
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
        <Button variant="outline" onClick={() => router.push("/review")}>
          검수 큐로 돌아가기
        </Button>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const draftTitle = `${draftKindLabel(task.draft.kind)} v${task.draft.version}`;

  return (
    <div className="space-y-6 pb-20 sm:pb-24">
      <div className="space-y-3">
        <button
          type="button"
          className="-ml-2 inline-flex h-9 items-center rounded-md px-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          onClick={() => router.push("/review")}
        >
          ← 검수 큐
        </button>

        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{draftTitle}</h1>
            <p className="break-keep text-sm text-muted-foreground">
              {task.draft.org_name} · {task.draft.project_name} ·{" "}
              {formatDateTime(task.draft.created_at)} 생성
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              className={cn(
                "whitespace-nowrap",
                REVIEW_STATUS_CLASSES[task.status],
              )}
            >
              {reviewStatusLabel(task.status)}
            </Badge>
            {needsReview > 0 ? (
              <Badge variant="outline" className="whitespace-nowrap text-warning">
                확인 필요 {needsReview}칸
              </Badge>
            ) : (
              <Badge variant="outline" className="whitespace-nowrap">
                확인 필요 없음
              </Badge>
            )}
          </div>
        </div>
      </div>

      {readOnly ? (
        <p className="rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
          {isReviewer
            ? "이미 결정된 과제입니다. 내용은 열람만 할 수 있습니다."
            : "운영자는 검수 내용을 열람만 할 수 있습니다."}
        </p>
      ) : null}

      {task.comment ? (
        <p className="rounded-md bg-muted px-3 py-2 text-sm">
          검수 의견: {task.comment}
        </p>
      ) : null}

      <Card>
        <CardContent className="p-0 sm:p-2">
          {task.draft.kind === "sow" ? (
            <SowEditor
              rows={rows}
              dirtyRows={dirtyRows}
              disabled={readOnly}
              onChange={handleRowChange}
            />
          ) : (
            <div className="p-4">
              <PolicyEditor
                sections={sections}
                dirtySections={dirtySections}
                disabled={readOnly}
                onChange={handleSectionChange}
              />
            </div>
          )}
        </CardContent>
      </Card>

      {readOnly ? null : (
        // 좁은 화면에서 두 줄(93px)로 부풀어 표를 가리던 바를 한 줄로 눌렀다.
        <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 backdrop-blur">
          <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-2 px-4 py-2 sm:gap-3 sm:px-6 sm:py-3">
            <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground sm:text-sm">
              <span className="sm:hidden">
                {dirtyCount > 0 ? `변경 ${dirtyCount}` : "변경 없음"}
                {` · 확인 ${needsReview}칸`}
              </span>
              <span className="hidden sm:inline">
                {dirtyCount > 0
                  ? `저장하지 않은 변경 ${dirtyCount}건`
                  : "변경 사항 없음"}
                {" · "}
                확인 필요 {needsReview}칸
              </span>
            </p>
            <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
              <Button
                variant="outline"
                className="h-9 px-3 sm:px-4"
                onClick={handleSave}
                disabled={saving || deciding || dirtyCount === 0}
              >
                {saving ? "저장 중…" : "저장"}
              </Button>
              <Button
                variant="destructive"
                className="h-9 px-3 sm:px-4"
                onClick={() => setReturnOpen(true)}
                disabled={saving || deciding}
              >
                반려
              </Button>
              <Button
                className="h-9 px-3 sm:px-4"
                onClick={() => setApproveOpen(true)}
                disabled={saving || deciding}
              >
                승인
              </Button>
            </div>
          </div>
        </div>
      )}

      <ApproveDialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        title={draftTitle}
        needsReview={needsReview}
        pending={deciding}
        onConfirm={handleApprove}
      />
      <ReturnDialog
        open={returnOpen}
        onOpenChange={setReturnOpen}
        title={draftTitle}
        pending={deciding}
        onConfirm={handleReturn}
      />
    </div>
  );
}
