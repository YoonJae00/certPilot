/** 검수 화면 표기용 한국어 라벨. 조직 화면 라벨(lib/labels.ts)과 분리해 둔다. */

import type {
  DraftKind,
  DraftStatus,
  ReviewTaskStatus,
} from "@/lib/types-review";

/** 초안 종류 문구. */
export const DRAFT_KIND_LABELS: Record<DraftKind, string> = {
  sow: "운영명세서",
  policy: "정보보호 정책",
};

/** 초안 승인 상태 문구. */
export const DRAFT_STATUS_LABELS: Record<DraftStatus, string> = {
  draft: "작성 중",
  in_review: "검수 대기",
  approved: "승인 완료",
  returned: "반려됨",
};

/** 검수 과제 상태 문구. */
export const REVIEW_STATUS_LABELS: Record<ReviewTaskStatus, string> = {
  pending: "검수 대기",
  approved: "승인",
  returned: "반려",
};

/** 검수 과제 상태별 뱃지 스타일. */
export const REVIEW_STATUS_CLASSES: Record<ReviewTaskStatus, string> = {
  pending: "border-transparent bg-warning text-warning-foreground",
  approved: "border-transparent bg-success text-success-foreground",
  returned: "border-transparent bg-destructive text-destructive-foreground",
};

export function draftKindLabel(kind: DraftKind | string): string {
  return DRAFT_KIND_LABELS[kind as DraftKind] ?? kind;
}

export function draftStatusLabel(status: DraftStatus | string): string {
  return DRAFT_STATUS_LABELS[status as DraftStatus] ?? status;
}

export function reviewStatusLabel(status: ReviewTaskStatus | string): string {
  return REVIEW_STATUS_LABELS[status as ReviewTaskStatus] ?? status;
}
