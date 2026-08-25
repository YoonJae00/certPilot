/**
 * 검수 워크플로(F6) 응답 계약.
 *
 * 심사원은 조직 스코프 API 를 쓸 수 없어서, 검수 화면에 필요한 조직·프로젝트 이름이
 * 검수 응답 안에 함께 들어온다(`/reviews/...`). 그래서 이 타입들은 lib/types.ts 의
 * 프로젝트 타입에 의존하지 않는다.
 */

/** 초안 종류. sow = 운영명세서. */
export type DraftKind = "sow" | "policy";

/** 초안 승인 상태. approved 여야만 고객이 내려받을 수 있다. */
export type DraftStatus = "draft" | "in_review" | "approved" | "returned";

/** 검수 과제 상태. */
export type ReviewTaskStatus = "pending" | "approved" | "returned";

/** 운영명세서 1행. 심사원이 고칠 수 있는 칸은 운영 현황·담당 부서·비고뿐이다. */
export interface SowRow {
  criterion_code: string;
  criterion_title: string;
  /** 소분류 명칭(예: "2.5 인증 및 권한관리"). */
  section: string;
  operation_status: string;
  related_refs: string[];
  owner_dept: string;
  note: string;
}

/** 정책 초안의 조항 1개. */
export interface PolicySection {
  heading: string;
  body: string;
}

/** 초안 통계. `needs_review` 는 사람이 채워야 할 `[확인 필요]` 칸 수다. */
export interface DraftStats {
  total?: number;
  needs_review?: number;
  needs_review_rows?: number;
  by_status?: Record<string, number>;
}

/** 초안 본문. 종류에 따라 rows 또는 sections 중 하나가 채워진다. */
export interface DraftContent {
  title?: string;
  assessment_id?: string;
  rows?: SowRow[];
  sections?: PolicySection[];
  stats?: DraftStats;
}

/** 검수 대상 초안 요약. */
export interface ReviewDraftSummary {
  id: string;
  project_id: string;
  project_name: string;
  org_id: string;
  org_name: string;
  kind: DraftKind;
  version: number;
  status: DraftStatus;
  created_at: string;
  stats: DraftStats;
}

/** `GET /reviews/queue` 응답 1건. */
export interface ReviewTask {
  id: string;
  status: ReviewTaskStatus;
  /** null 이면 아직 아무도 잡지 않은 과제다. */
  reviewer_id: string | null;
  comment: string | null;
  decided_at: string | null;
  created_at: string;
  /** 내가 잡은 과제인지. 미배정 과제는 열람하는 순간 배정된다. */
  assigned_to_me: boolean;
  draft: ReviewDraftSummary;
}

/** `GET /reviews/{taskId}` 응답. 편집 대상 본문을 포함한다. */
export interface ReviewTaskDetail extends ReviewTask {
  content_json: DraftContent;
}

/** 운영명세서 행에서 편집 가능한 칸. */
export type SowEditableField = "operation_status" | "owner_dept" | "note";

/** `PATCH /reviews/{taskId}/content` 의 행 수정 항목. */
export interface SowRowEdit {
  row_index: number;
  fields: Partial<Record<SowEditableField, string>>;
}

/** `PATCH /reviews/{taskId}/content` 의 조항 수정 항목. */
export interface PolicySectionEdit {
  section_index: number;
  body: string;
}

/** `PATCH /reviews/{taskId}/content` 요청 본문. 초안 종류에 맞는 배열만 채운다. */
export interface ReviewContentPatch {
  rows?: SowRowEdit[];
  sections?: PolicySectionEdit[];
}
