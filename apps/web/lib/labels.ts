/** 화면 표기용 한국어 라벨과 포맷 도우미. */

import type {
  AssessmentStatus,
  CertType,
  CriterionChapter,
  DecidedBy,
  DocumentStatus,
  FindingStatus,
  UserRole,
} from "@/lib/types";

/** 역할 뱃지 문구. */
export const ROLE_LABELS: Record<UserRole, string> = {
  org_admin: "조직 관리자",
  org_member: "조직 담당자",
  reviewer: "심사 검수자",
  operator: "운영자",
};

/** 인증 종류 문구. 값 자체가 표기와 같지만 화면에서는 이 맵만 쓴다. */
export const CERT_TYPE_LABELS: Record<CertType, string> = {
  ISMS: "ISMS",
  "ISMS-P": "ISMS-P",
};

/** 문서 파싱 상태 문구. */
export const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  uploaded: "업로드됨",
  parsed: "분석 완료",
  failed: "분석 실패",
};

/** 문서 상태별 뱃지 스타일(shadcn Badge variant 기준 보조 클래스). */
export const DOCUMENT_STATUS_CLASSES: Record<DocumentStatus, string> = {
  uploaded: "border-transparent bg-secondary text-secondary-foreground",
  parsed: "border-transparent bg-success text-success-foreground",
  failed: "border-transparent bg-destructive text-destructive-foreground",
};

export function roleLabel(role: UserRole | string): string {
  return ROLE_LABELS[role as UserRole] ?? role;
}

export function documentStatusLabel(status: DocumentStatus | string): string {
  return DOCUMENT_STATUS_LABELS[status as DocumentStatus] ?? status;
}

/** ISO 날짜/일시를 `YYYY. MM. DD.` 로 표기한다. 값이 없으면 대시. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}. ${month}. ${day}.`;
}

/** 사후심사일까지 남은 일수. 값이 없으면 null. */
export function daysUntil(value: string | null | undefined): number | null {
  if (!value) return null;
  const target = new Date(`${value.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

/** 바이트 크기를 사람이 읽기 쉬운 문자열로 바꾼다. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ------------------------------------------------------------------ */
/* 모의심사 · 갭 리포트                                                */
/* ------------------------------------------------------------------ */

/** 모의심사 실행 상태 문구. */
export const ASSESSMENT_STATUS_LABELS: Record<AssessmentStatus, string> = {
  queued: "대기 중",
  running: "진행 중",
  done: "완료",
  failed: "실패",
};

/** 실행 상태별 뱃지 스타일. */
export const ASSESSMENT_STATUS_CLASSES: Record<AssessmentStatus, string> = {
  queued: "border-transparent bg-secondary text-secondary-foreground",
  running: "border-transparent bg-primary text-primary-foreground",
  done: "border-transparent bg-success text-success-foreground",
  failed: "border-transparent bg-destructive text-destructive-foreground",
};

/** 판정 문구(PRD F3). */
export const FINDING_STATUS_LABELS: Record<FindingStatus, string> = {
  met: "충족",
  partial: "부분충족",
  unmet: "미충족",
  unknown: "판단불가",
};

/** 판정별 뱃지 스타일. 충족=초록 / 부분충족=노랑 / 미충족=빨강 / 판단불가=회색. */
export const FINDING_STATUS_CLASSES: Record<FindingStatus, string> = {
  met: "border-transparent bg-success text-success-foreground",
  partial: "border-transparent bg-warning text-warning-foreground",
  unmet: "border-transparent bg-destructive text-destructive-foreground",
  unknown: "border-transparent bg-secondary text-secondary-foreground",
};

/** 판정 분포·필터에서 쓰는 표기 순서. */
export const FINDING_STATUS_ORDER: readonly FindingStatus[] = [
  "met",
  "partial",
  "unmet",
  "unknown",
] as const;

/** 판정 정렬 순서. 조치가 급한 미충족부터 보여 준다. */
const FINDING_STATUS_SEVERITY: Record<FindingStatus, number> = {
  unmet: 0,
  partial: 1,
  unknown: 2,
  met: 3,
};

/** 판정 기준 정렬용 가중치. 값이 작을수록 위로 온다. */
export function findingSeverity(status: FindingStatus | string): number {
  return FINDING_STATUS_SEVERITY[status as FindingStatus] ?? 99;
}

export function findingStatusLabel(status: FindingStatus | string): string {
  return FINDING_STATUS_LABELS[status as FindingStatus] ?? status;
}

/** 판정 주체 문구. */
export const DECIDED_BY_LABELS: Record<DecidedBy, string> = {
  rule: "규칙",
  llm: "AI",
  reviewer: "심사원",
};

export function decidedByLabel(value: DecidedBy | string): string {
  return DECIDED_BY_LABELS[value as DecidedBy] ?? value;
}

/** 장 전체 명칭. */
export const CHAPTER_LABELS: Record<CriterionChapter, string> = {
  "1": "1장 관리체계 수립 및 운영",
  "2": "2장 보호대책 요구사항",
  "3": "3장 개인정보 처리단계별 요구사항",
};

/** 장 축약 명칭. 카드 제목·필터에서 쓴다. */
export const CHAPTER_SHORT_LABELS: Record<CriterionChapter, string> = {
  "1": "1장 관리체계",
  "2": "2장 보호대책",
  "3": "3장 개인정보",
};

/** 표기 순서가 고정된 장 목록. */
export const CHAPTERS: readonly CriterionChapter[] = ["1", "2", "3"] as const;

/** 항목 코드에서 장 번호를 뽑는다. 형식이 다르면 null. */
export function chapterOf(code: string): CriterionChapter | null {
  const head = code.trim().split(".")[0];
  return head === "1" || head === "2" || head === "3" ? head : null;
}

/**
 * 비율(0~1)을 백분율로 바꾼다. 서버는 준비도·신뢰도를 모두 0~1 로 주지만,
 * 이미 백분율로 온 값(1 초과)은 그대로 두어 두 번 곱하지 않는다.
 */
export function toPercent(value: number | null | undefined): number | null {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  return value > 0 && value <= 1 ? value * 100 : value;
}

/** 백분율 표기. 값이 없으면 대시. */
export function formatPercent(
  value: number | null | undefined,
  fractionDigits = 0,
): string {
  const percent = toPercent(value);
  if (percent === null) return "—";
  return `${percent.toFixed(fractionDigits)}%`;
}

/** USD 비용 표기. 값이 없으면 대시. */
export function formatCostUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `$${value.toFixed(2)}`;
}

/** ISO 일시를 `YYYY. MM. DD. HH:MM` 로 표기한다. 값이 없으면 대시. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${formatDate(value)} ${hour}:${minute}`;
}

/** 파일명에 붙일 날짜 도장(YYYYMMDD). 인자가 없으면 오늘. */
export function fileDateStamp(value?: string | null): string {
  const date = value ? new Date(value) : new Date();
  const safe = Number.isNaN(date.getTime()) ? new Date() : date;
  const month = String(safe.getMonth() + 1).padStart(2, "0");
  const day = String(safe.getDate()).padStart(2, "0");
  return `${safe.getFullYear()}${month}${day}`;
}

/** 파일명에 쓸 수 없는 문자를 밑줄로 바꾼다. */
export function sanitizeFileName(value: string): string {
  return value.replace(/[\\/:*?"<>|]/g, "_").trim() || "프로젝트";
}
