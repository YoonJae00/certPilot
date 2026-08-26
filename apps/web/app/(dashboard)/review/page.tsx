"use client";

/** 검수 큐(PRD §7 F6). 심사원의 기본 화면이다. */

import { useRouter } from "next/navigation";
import * as React from "react";

import {
  REVIEW_STATUS_CLASSES,
  draftKindLabel,
  reviewStatusLabel,
} from "@/app/(dashboard)/review/labels";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { isUnauthorized, toMessage } from "@/lib/api";
import { reviewApi } from "@/lib/api-review";
import { formatDateTime } from "@/lib/labels";
import { cn } from "@/lib/utils";
import type { ReviewTask } from "@/lib/types-review";

export default function ReviewQueuePage() {
  const router = useRouter();
  const [tasks, setTasks] = React.useState<ReviewTask[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();

    reviewApi
      .queue(controller.signal)
      .then((data) => setTasks(data))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (isUnauthorized(err)) {
          router.replace("/login");
          return;
        }
        setError(toMessage(err));
        setTasks([]);
      });

    return () => controller.abort();
  }, [router]);

  const pendingCount = (tasks ?? []).filter(
    (task) => task.status === "pending",
  ).length;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">검수 큐</h1>
        <p className="text-sm text-muted-foreground">
          {tasks === null
            ? "검수 과제를 불러오는 중입니다…"
            : `검수 대기 ${pendingCount}건. 과제를 열면 나에게 배정됩니다.`}
        </p>
      </div>

      {error ? (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      ) : null}

      {tasks === null ? (
        <QueueSkeleton />
      ) : tasks.length === 0 ? (
        <EmptyState />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                {/* 좁은 화면에서는 종류·프로젝트·상태만 남긴다. 나머지는 프로젝트 아래 한 줄로 요약한다. */}
                <TableRow>
                  <TableHead className="w-[92px] sm:w-[140px]">
                    초안 종류
                  </TableHead>
                  <TableHead className="hidden w-[72px] md:table-cell">
                    버전
                  </TableHead>
                  <TableHead>프로젝트</TableHead>
                  <TableHead className="hidden w-[160px] lg:table-cell">
                    조직
                  </TableHead>
                  <TableHead className="hidden w-[120px] sm:table-cell">
                    확인 필요
                  </TableHead>
                  <TableHead className="w-[88px] sm:w-[110px]">상태</TableHead>
                  <TableHead className="hidden w-[170px] md:table-cell">
                    생성일
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.map((task) => (
                  <TableRow
                    key={task.id}
                    className="cursor-pointer"
                    onClick={() => router.push(`/review/${task.id}`)}
                  >
                    <TableCell className="break-keep align-top font-medium sm:align-middle">
                      {draftKindLabel(task.draft.kind)}
                    </TableCell>
                    <TableCell className="hidden whitespace-nowrap md:table-cell">
                      v{task.draft.version}
                    </TableCell>
                    <TableCell className="break-keep align-top sm:align-middle">
                      {task.draft.project_name}
                      <span className="mt-0.5 block break-keep text-xs text-muted-foreground lg:hidden">
                        {task.draft.org_name}
                      </span>
                      {/* 버전·생성일 열은 md 미만에서 숨으므로 그 구간엔 여기로 보여 준다. */}
                      <span className="mt-0.5 block whitespace-nowrap text-xs text-muted-foreground md:hidden">
                        v{task.draft.version} · {formatDateTime(task.draft.created_at)}
                      </span>
                      <span className="mt-0.5 block whitespace-nowrap text-xs text-muted-foreground sm:hidden">
                        확인 필요 {needsReviewText(task.draft.stats?.needs_review)}
                      </span>
                    </TableCell>
                    <TableCell className="hidden break-keep text-muted-foreground lg:table-cell">
                      {task.draft.org_name}
                    </TableCell>
                    <TableCell className="hidden whitespace-nowrap sm:table-cell">
                      <NeedsReviewCell count={task.draft.stats?.needs_review} />
                    </TableCell>
                    <TableCell className="align-top sm:align-middle">
                      <Badge
                        className={cn(
                          "whitespace-nowrap",
                          REVIEW_STATUS_CLASSES[task.status],
                        )}
                      >
                        {reviewStatusLabel(task.status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="hidden whitespace-nowrap text-muted-foreground md:table-cell">
                      {formatDateTime(task.draft.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/** 좁은 화면 요약줄에 쓰는 "확인 필요" 문구. 집계가 없으면 —. */
function needsReviewText(count: number | undefined): string {
  return count === undefined ? "—" : `${count}칸`;
}

/** 사람이 채워야 하는 칸 수. 0이면 강조하지 않는다. */
function NeedsReviewCell({ count }: { count: number | undefined }) {
  if (count === undefined) return <span className="text-muted-foreground">—</span>;
  if (count === 0) return <span className="text-muted-foreground">없음</span>;
  return <span className="font-medium text-warning">{count}칸</span>;
}

function QueueSkeleton() {
  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-5 w-1/2" />
      </CardContent>
    </Card>
  );
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <p className="text-sm font-medium">검수할 초안이 없습니다.</p>
        <p className="max-w-md text-sm text-muted-foreground">
          고객사가 운영명세서·정책 초안을 생성하면 이곳에 검수 과제로 올라옵니다.
        </p>
      </CardContent>
    </Card>
  );
}
