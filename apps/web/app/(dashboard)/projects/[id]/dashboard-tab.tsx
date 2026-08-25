"use client";

/**
 * 유지 대시보드 탭 (PRD §7 F8).
 *
 * 카드: 장별 준비도 · 미충족 Top 5 · 최근 알림 · 사후심사 D-day · 검수 대기 · 문서/수집.
 * 수치는 `GET /projects/{id}/dashboard` 한 번으로 받는다(카드마다 호출하지 않는다).
 */

import { CalendarClock, ShieldAlert, TriangleAlert } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
import { Skeleton } from "@/components/ui/skeleton";
import { dashboardApi } from "@/lib/api-dashboard";
import { toMessage } from "@/lib/api";
import {
  CHAPTERS,
  CHAPTER_LABELS,
  CHAPTER_SHORT_LABELS,
  formatDate,
  formatDateTime,
  formatPercent,
  toPercent,
} from "@/lib/labels";
import type {
  AlertType,
  DashboardAlert,
  ProjectDashboard,
} from "@/lib/types-dashboard";
import { cn } from "@/lib/utils";

/** 알림 종류 문구. */
const ALERT_TYPE_LABELS: Record<AlertType, string> = {
  drift: "설정 변경",
  due: "일정",
  defect: "예상 결함",
};

/** 알림 종류별 아이콘. 변경 감지=경고 / 기한=일정 / 결함=방패. */
const ALERT_TYPE_ICONS: Record<
  AlertType,
  React.ComponentType<{ className?: string }>
> = {
  drift: TriangleAlert,
  due: CalendarClock,
  defect: ShieldAlert,
};

/** 알림 종류별 아이콘 색. */
const ALERT_TYPE_CLASSES: Record<AlertType, string> = {
  drift: "text-warning",
  due: "text-muted-foreground",
  defect: "text-destructive",
};

export function DashboardTab({
  projectId,
  /** 모의심사 탭으로 보내는 콜백(빈 상태에서 쓴다). */
  onGoToAssessment,
}: {
  projectId: string;
  onGoToAssessment: () => void;
}) {
  const [data, setData] = React.useState<ProjectDashboard | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busyAlertId, setBusyAlertId] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    setData(null);
    setError(null);

    dashboardApi
      .get(projectId, controller.signal)
      .then((payload) => setData(payload))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(toMessage(err));
      });

    return () => controller.abort();
  }, [projectId]);

  /** 알림 1건 읽음 처리. 응답으로 받은 값을 화면 상태에 반영한다. */
  async function handleMarkRead(alert: DashboardAlert) {
    if (alert.read_at) return;
    setBusyAlertId(alert.id);
    try {
      const updated = await dashboardApi.markAlertRead(projectId, alert.id);
      setData((prev) =>
        prev
          ? {
              ...prev,
              recent_alerts: prev.recent_alerts.map((item) =>
                item.id === updated.id ? updated : item,
              ),
              unread_alert_count: Math.max(0, prev.unread_alert_count - 1),
            }
          : prev,
      );
    } catch (err) {
      toast.error(toMessage(err));
    } finally {
      setBusyAlertId(null);
    }
  }

  /** 읽지 않은 알림을 모두 읽음 처리한다. */
  async function handleMarkAllRead() {
    setBusyAlertId("all");
    try {
      const result = await dashboardApi.markAllAlertsRead(projectId);
      const readAt = new Date().toISOString();
      setData((prev) =>
        prev
          ? {
              ...prev,
              recent_alerts: prev.recent_alerts.map((item) =>
                item.read_at ? item : { ...item, read_at: readAt },
              ),
              unread_alert_count: 0,
            }
          : prev,
      );
      toast.success(`알림 ${result.updated}건을 읽음으로 표시했습니다.`);
    } catch (err) {
      toast.error(toMessage(err));
    } finally {
      setBusyAlertId(null);
    }
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

  if (!data) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <Skeleton key={index} className="h-40 w-full" />
        ))}
      </div>
    );
  }

  const hasAssessment = data.last_assessment !== null;

  return (
    <div className="space-y-4">
      {hasAssessment && data.readiness ? (
        <ChapterReadinessCards readiness={data.readiness} />
      ) : (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <p className="text-sm font-medium">모의심사를 먼저 실행하세요.</p>
            <p className="max-w-md text-sm text-muted-foreground">
              {hasAssessment
                ? "실행이 끝나면 장별 준비도와 미충족 항목이 이곳에 표시됩니다."
                : "문서를 올리고 모의심사를 한 번 실행하면 준비도와 미충족 항목을 볼 수 있습니다."}
            </p>
            <Button variant="outline" size="sm" onClick={onGoToAssessment}>
              모의심사 탭으로 이동
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <AuditDueCard dashboard={data} />
        <PendingReviewCard count={data.pending_review_count} />
        <CollectionCard dashboard={data} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <TopUnmetCard dashboard={data} />
        <AlertsCard
          dashboard={data}
          busyAlertId={busyAlertId}
          onMarkRead={handleMarkRead}
          onMarkAllRead={handleMarkAllRead}
        />
      </div>
    </div>
  );
}

/** ① 장별 준비도 카드 3개. */
function ChapterReadinessCards({
  readiness,
}: {
  readiness: NonNullable<ProjectDashboard["readiness"]>;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {CHAPTERS.map((chapter) => {
        const stats = readiness.by_chapter[chapter];
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

/** ④ 사후심사 D-day. 날짜가 없으면 프로젝트 설정을 안내한다. */
function AuditDueCard({ dashboard }: { dashboard: ProjectDashboard }) {
  const due = dashboard.audit_due;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">사후심사 D-day</CardTitle>
        <CardDescription className="text-xs">
          {due ? formatDate(due.date) : "프로젝트 예정일 기준"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {due ? (
          <>
            <p
              className={cn(
                "text-4xl font-semibold tabular-nums",
                due.d_day < 0
                  ? "text-destructive"
                  : due.d_day <= 30
                    ? "text-warning"
                    : undefined,
              )}
            >
              {due.d_day < 0 ? `+${Math.abs(due.d_day)}` : `D-${due.d_day}`}
            </p>
            <p className="text-xs text-muted-foreground">
              {due.d_day < 0
                ? `예정일이 ${Math.abs(due.d_day)}일 지났습니다.`
                : due.d_day === 0
                  ? "오늘이 사후심사 예정일입니다."
                  : `${due.d_day}일 남았습니다.`}
            </p>
          </>
        ) : (
          <>
            <p className="text-4xl font-semibold text-muted-foreground">미설정</p>
            <p className="text-xs text-muted-foreground">
              프로젝트 설정에서 사후심사 예정일을 입력하면 남은 일수를 보여 줍니다.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** ⑤ 검수 대기 건수. */
function PendingReviewCard({ count }: { count: number }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">검수 대기</CardTitle>
        <CardDescription className="text-xs">
          심사원 확인을 기다리는 초안입니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        <p className="text-4xl font-semibold tabular-nums">{count}건</p>
        <p className="text-xs text-muted-foreground">
          {count === 0
            ? "대기 중인 검수 요청이 없습니다."
            : "승인되면 산출물 다운로드가 열립니다."}
        </p>
      </CardContent>
    </Card>
  );
}

/** ⑥ 문서 수와 최근 증적 수집 시각. */
function CollectionCard({ dashboard }: { dashboard: ProjectDashboard }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">문서 · 증적</CardTitle>
        <CardDescription className="text-xs">
          판정 근거가 되는 자료 현황입니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-baseline gap-2">
          <span className="text-4xl font-semibold tabular-nums">
            {dashboard.document_count}
          </span>
          <span className="text-xs text-muted-foreground">개 문서</span>
        </div>
        <p className="text-xs text-muted-foreground">
          최근 증적 수집 {formatDateTime(dashboard.last_collected_at)}
        </p>
        {dashboard.last_assessment ? (
          <p className="text-xs text-muted-foreground">
            최근 모의심사 {formatDateTime(dashboard.last_assessment.finished_at)}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** ② 미충족 Top 5. */
function TopUnmetCard({ dashboard }: { dashboard: ProjectDashboard }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">미충족 Top 5</CardTitle>
        <CardDescription className="text-xs">
          확신도가 높은 미충족 항목부터 조치하세요.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {dashboard.top_unmet.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {dashboard.readiness
              ? "미충족으로 판정된 항목이 없습니다."
              : "모의심사를 실행하면 미충족 항목이 표시됩니다."}
          </p>
        ) : (
          <ul className="space-y-3">
            {dashboard.top_unmet.map((item) => (
              <li key={item.criterion_code} className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className="tabular-nums">
                    {item.criterion_code}
                  </Badge>
                  <span className="text-sm font-medium">{item.title}</span>
                  <span className="text-xs text-muted-foreground">
                    확신도 {formatPercent(item.confidence)}
                  </span>
                </div>
                <p className="truncate text-xs text-muted-foreground">
                  {item.predicted_defect?.trim()
                    ? item.predicted_defect
                    : "예상 결함 설명이 없습니다."}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** ③ 최근 알림. */
function AlertsCard({
  dashboard,
  busyAlertId,
  onMarkRead,
  onMarkAllRead,
}: {
  dashboard: ProjectDashboard;
  busyAlertId: string | null;
  onMarkRead: (alert: DashboardAlert) => void;
  onMarkAllRead: () => void;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div className="space-y-1.5">
          <CardTitle className="text-sm font-medium">최근 알림</CardTitle>
          <CardDescription className="text-xs">
            읽지 않은 알림 {dashboard.unread_alert_count}건
          </CardDescription>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={dashboard.unread_alert_count === 0 || busyAlertId === "all"}
          onClick={onMarkAllRead}
        >
          모두 읽음
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {dashboard.recent_alerts.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            아직 도착한 알림이 없습니다.
          </p>
        ) : (
          <ul className="space-y-3">
            {dashboard.recent_alerts.map((alert) => {
              const Icon = ALERT_TYPE_ICONS[alert.type];
              const unread = alert.read_at === null;

              return (
                <li
                  key={alert.id}
                  className={cn(
                    "flex items-start gap-3 rounded-md border p-3",
                    unread ? "bg-muted/40" : "opacity-70",
                  )}
                >
                  <Icon
                    className={cn("mt-0.5 size-4 shrink-0", ALERT_TYPE_CLASSES[alert.type])}
                  />
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary" className="text-xs">
                        {ALERT_TYPE_LABELS[alert.type]}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {formatDateTime(alert.created_at)}
                      </span>
                    </div>
                    <p className="text-sm">{alert.message}</p>
                  </div>
                  {unread ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={busyAlertId === alert.id}
                      onClick={() => onMarkRead(alert)}
                    >
                      읽음 처리
                    </Button>
                  ) : (
                    <span className="shrink-0 text-xs text-muted-foreground">읽음</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
