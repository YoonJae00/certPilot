"use client";

/**
 * 모의심사 실행 이력 상태를 모아 두는 훅.
 *
 * 모의심사 탭(실행·진행률)과 리포트 탭(판정 테이블)이 같은 실행을 가리켜야 해서
 * 프로젝트 상세 페이지에서 한 번만 호출하고 두 탭에 내려 준다.
 */

import * as React from "react";
import { toast } from "sonner";

import { ApiError, assessmentsApi, toMessage } from "@/lib/api";
import type { Assessment } from "@/lib/types";

/** 진행 중일 때 실행 상태를 다시 확인하는 주기(ms). */
const POLL_INTERVAL_MS = 2_000;

export interface UseAssessmentsResult {
  /** 실행 이력(최신순). 로딩 중이면 null. */
  assessments: Assessment[] | null;
  /** 현재 화면이 보고 있는 실행. */
  selected: Assessment | null;
  selectedId: string | null;
  selectAssessment: (assessmentId: string) => void;
  /** 선택된 실행이 아직 끝나지 않았는지. */
  isRunning: boolean;
  /** 실행 요청을 보내는 중인지. */
  isStarting: boolean;
  /** 백엔드 API 가 아직 없을 때(404/501). */
  notReady: boolean;
  error: string | null;
  start: () => Promise<void>;
}

/** 실행이 아직 진행 중인지 판단한다. */
export function isPending(assessment: Assessment | null): boolean {
  return assessment?.status === "queued" || assessment?.status === "running";
}

export function useAssessments(projectId: string): UseAssessmentsResult {
  const [assessments, setAssessments] = React.useState<Assessment[] | null>(
    null,
  );
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [notReady, setNotReady] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [isStarting, setIsStarting] = React.useState(false);

  const load = React.useCallback(
    async (signal?: AbortSignal) => {
      try {
        const list = await assessmentsApi.list(projectId, signal);
        setAssessments(list);
        setNotReady(false);
        setError(null);
        // 기본 선택은 최신 실행. 이미 고른 실행이 살아 있으면 유지한다.
        setSelectedId((prev) =>
          prev && list.some((item) => item.id === prev)
            ? prev
            : (list[0]?.id ?? null),
        );
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        // 아직 배포되지 않은 API 는 준비 중으로 표시한다.
        if (err instanceof ApiError && (err.status === 404 || err.status === 501)) {
          setNotReady(true);
          setAssessments([]);
          return;
        }
        setError(toMessage(err));
        setAssessments([]);
      }
    },
    [projectId],
  );

  React.useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, projectId]);

  const selected = React.useMemo(
    () => assessments?.find((item) => item.id === selectedId) ?? null,
    [assessments, selectedId],
  );
  const running = isPending(selected);

  // 진행 중인 실행만 2초 간격으로 다시 조회한다.
  React.useEffect(() => {
    if (!running || !selectedId || !projectId) return;
    const controller = new AbortController();

    const timer = setInterval(() => {
      assessmentsApi
        .get(projectId, selectedId, controller.signal)
        .then((fresh) => {
          setAssessments((prev) =>
            prev
              ? prev.map((item) => (item.id === fresh.id ? fresh : item))
              : [fresh],
          );
          if (fresh.status === "done") {
            toast.success(
              "모의심사가 끝났습니다. 리포트 탭에서 항목별 판정을 확인해 주세요.",
            );
          } else if (fresh.status === "failed") {
            toast.error("모의심사가 실패했습니다. 잠시 후 다시 실행해 주세요.");
          }
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          // 폴링 실패는 다음 주기에 다시 시도하므로 화면 상태만 남긴다.
          setError(toMessage(err));
        });
    }, POLL_INTERVAL_MS);

    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [projectId, running, selectedId]);

  const start = React.useCallback(async () => {
    setIsStarting(true);
    try {
      const created = await assessmentsApi.create(projectId);
      setAssessments((prev) => (prev ? [created, ...prev] : [created]));
      setSelectedId(created.id);
      setNotReady(false);
      setError(null);
      toast.success("모의심사를 시작했습니다. 진행률이 자동으로 갱신됩니다.");
    } catch (err) {
      toast.error(toMessage(err));
    } finally {
      setIsStarting(false);
    }
  }, [projectId]);

  return {
    assessments,
    selected,
    selectedId,
    selectAssessment: setSelectedId,
    isRunning: running,
    isStarting,
    notReady,
    error,
    start,
  };
}
