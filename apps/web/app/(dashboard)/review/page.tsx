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
                <TableRow>
                  <TableHead className="w-[140px]">초안 종류</TableHead>
                  <TableHead className="w-[72px]">버전</TableHead>
                  <TableHead>프로젝트</TableHead>
                  <TableHead className="w-[160px]">조직</TableHead>
                  <TableHead className="w-[120px]">확인 필요</TableHead>
                  <TableHead className="w-[110px]">상태</TableHead>
                  <TableHead className="w-[170px]">생성일</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.map((task) => (
                  <TableRow
                    key={task.id}
                    className="cursor-pointer"
                    onClick={() => router.push(`/review/${task.id}`)}
                  >
                    <TableCell className="font-medium">
                      {draftKindLabel(task.draft.kind)}
                    </TableCell>
                    <TableCell>v{task.draft.version}</TableCell>
                    <TableCell>{task.draft.project_name}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {task.draft.org_name}
                    </TableCell>
                    <TableCell>
                      <NeedsReviewCell count={task.draft.stats?.needs_review} />
                    </TableCell>
                    <TableCell>
                      <Badge className={REVIEW_STATUS_CLASSES[task.status]}>
                        {reviewStatusLabel(task.status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
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
