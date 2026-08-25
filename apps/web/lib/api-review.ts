/**
 * 검수 워크플로(F6) API 클라이언트.
 *
 * 인증·오류 처리는 lib/api.ts 의 `apiFetch` 를 그대로 쓴다. 검수 경로만 여기 모은다.
 */

import { apiFetch } from "@/lib/api";
import type {
  DraftContent,
  ReviewContentPatch,
  ReviewTask,
  ReviewTaskDetail,
} from "@/lib/types-review";

/** 초안에서 사람이 채워야 하는 칸에 남는 표시. 서버(`draft_common.NEEDS_REVIEW`)와 같은 값. */
export const NEEDS_REVIEW = "[확인 필요]";

export const reviewApi = {
  /** 검수 큐: 미배정 + 내게 배정된 대기 과제, 그리고 내가 처리한 이력. */
  queue(signal?: AbortSignal): Promise<ReviewTask[]> {
    return apiFetch<ReviewTask[]>("/reviews/queue", { signal });
  },
  /** 과제 상세. 미배정 과제를 열면 그 순간 나에게 배정된다. */
  get(taskId: string, signal?: AbortSignal): Promise<ReviewTaskDetail> {
    return apiFetch<ReviewTaskDetail>(`/reviews/${taskId}`, { signal });
  },
  /** 초안 편집. 서버가 DOCX 를 다시 만들고 갱신된 본문을 돌려준다. */
  editContent(
    taskId: string,
    patch: ReviewContentPatch,
  ): Promise<ReviewTaskDetail> {
    return apiFetch<ReviewTaskDetail>(`/reviews/${taskId}/content`, {
      method: "PATCH",
      json: patch,
    });
  },
  /** 승인. 이 시점부터 고객이 산출물을 내려받을 수 있다. */
  approve(taskId: string, comment?: string): Promise<ReviewTaskDetail> {
    return apiFetch<ReviewTaskDetail>(`/reviews/${taskId}/approve`, {
      method: "POST",
      json: { comment: comment ?? null },
    });
  },
  /** 반려. 코멘트는 필수다(서버도 빈 값을 400 으로 막는다). */
  sendBack(taskId: string, comment: string): Promise<ReviewTaskDetail> {
    return apiFetch<ReviewTaskDetail>(`/reviews/${taskId}/return`, {
      method: "POST",
      json: { comment },
    });
  },
};

/** 값 안에 `[확인 필요]` 가 남은 **칸** 수. 서버 통계와 같은 방식으로 센다. */
export function countNeedsReview(value: unknown): number {
  if (typeof value === "string") return value.includes(NEEDS_REVIEW) ? 1 : 0;
  if (Array.isArray(value)) {
    return value.reduce<number>((sum, item) => sum + countNeedsReview(item), 0);
  }
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).reduce<number>(
      (sum, item) => sum + countNeedsReview(item),
      0,
    );
  }
  return 0;
}

/** 화면에 표시할 `[확인 필요]` 칸 수. 저장 전 로컬 편집분까지 반영해 다시 센다. */
export function needsReviewCount(content: DraftContent): number {
  if (content.rows) return countNeedsReview(content.rows);
  if (content.sections) return countNeedsReview(content.sections);
  return content.stats?.needs_review ?? 0;
}
