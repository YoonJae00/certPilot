/** 화면 표기용 한국어 라벨과 포맷 도우미. */

import type { CertType, DocumentStatus, UserRole } from "@/lib/types";

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
