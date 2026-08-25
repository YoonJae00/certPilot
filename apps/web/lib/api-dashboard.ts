/**
 * 유지 대시보드·알림·증적 패키지 API 클라이언트 (PRD §7 F7, F8).
 *
 * 공통 fetch 래퍼(`apiFetch`/`apiFetchBlob`)는 `lib/api.ts` 것을 그대로 쓴다.
 */

import { apiFetch, apiFetchBlob } from "@/lib/api";
import type {
  AlertListParams,
  AlertReadAllResult,
  DashboardAlert,
  ProjectDashboard,
} from "@/lib/types-dashboard";

export const dashboardApi = {
  /** 대시보드 한 화면에 필요한 수치를 한 번에 받는다. */
  get(projectId: string, signal?: AbortSignal): Promise<ProjectDashboard> {
    return apiFetch<ProjectDashboard>(`/projects/${projectId}/dashboard`, {
      signal,
    });
  },

  /** 알림 목록(최신순). */
  alerts(
    projectId: string,
    params: AlertListParams = {},
    signal?: AbortSignal,
  ): Promise<DashboardAlert[]> {
    return apiFetch<DashboardAlert[]>(`/projects/${projectId}/alerts`, {
      query: {
        type: params.type,
        // false 는 서버 기본값과 같으므로 아예 보내지 않는다.
        unread_only: params.unread_only ? true : undefined,
        limit: params.limit,
      },
      signal,
    });
  },

  /** 알림 1건 읽음 처리(멱등). */
  markAlertRead(projectId: string, alertId: string): Promise<DashboardAlert> {
    return apiFetch<DashboardAlert>(
      `/projects/${projectId}/alerts/${alertId}/read`,
      { method: "PATCH" },
    );
  },

  /** 읽지 않은 알림 전체 읽음 처리. */
  markAllAlertsRead(projectId: string): Promise<AlertReadAllResult> {
    return apiFetch<AlertReadAllResult>(
      `/projects/${projectId}/alerts/read-all`,
      { method: "PATCH" },
    );
  },

  /** 증적 패키지 ZIP 원본. 파일명은 호출 측에서 정한다. */
  evidencePackage(
    projectId: string,
    assessmentId: string,
    signal?: AbortSignal,
  ): Promise<Blob> {
    return apiFetchBlob(
      `/projects/${projectId}/assessments/${assessmentId}/evidence-package.zip`,
      { signal },
    );
  },
};
