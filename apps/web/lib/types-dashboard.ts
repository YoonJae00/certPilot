/**
 * 유지 대시보드·알림 API 응답 계약 (PRD §7 F8).
 *
 * `lib/types.ts` 와 분리해 둔다. 대시보드는 나중에 추가된 계약이라 서버 스키마가
 * 바뀔 때 이 파일만 맞추면 되도록 했다. 공통 타입(장 번호·실행 상태)은 그대로 재사용한다.
 */

import type { AssessmentStatus, CriterionChapter } from "@/lib/types";

/** 알림 종류. drift=설정 변경 감지 / due=사후심사 일정 / defect=예상 결함. */
export type AlertType = "drift" | "due" | "defect";

/** `GET /projects/{id}/alerts` 응답 1건. */
export interface DashboardAlert {
  id: string;
  type: AlertType;
  message: string;
  /** drift 알림이 가리키는 증적. 없을 수 있다. */
  evidence_id: string | null;
  /** 읽음 처리 시각. null 이면 아직 읽지 않았다. */
  read_at: string | null;
  created_at: string;
}

/** 장별 판정 집계와 준비도. */
export interface ChapterReadiness {
  total: number;
  met: number;
  partial: number;
  unmet: number;
  unknown: number;
  /** 0~1 비율. `toPercent()` 로 백분율로 바꿔 쓴다. */
  readiness: number;
}

/** 전체·장별 준비도. 완료된 모의심사가 없으면 응답 자체가 null 이다. */
export interface DashboardReadiness {
  overall: number;
  by_chapter: Partial<Record<CriterionChapter, ChapterReadiness>>;
}

/** 미충족 Top 5 항목 1건. */
export interface TopUnmetItem {
  criterion_code: string;
  title: string;
  confidence: number;
  predicted_defect: string | null;
}

/** 사후심사 예정일과 남은 일수. 지났으면 `d_day` 가 음수다. */
export interface AuditDue {
  /** ISO 날짜(YYYY-MM-DD). */
  date: string;
  d_day: number;
}

/** 가장 최근 모의심사 실행(상태 무관). */
export interface LastAssessment {
  id: string;
  status: AssessmentStatus;
  finished_at: string | null;
}

/** `GET /projects/{id}/dashboard` 응답. */
export interface ProjectDashboard {
  readiness: DashboardReadiness | null;
  top_unmet: TopUnmetItem[];
  recent_alerts: DashboardAlert[];
  unread_alert_count: number;
  audit_due: AuditDue | null;
  /** 검수 대기(in_review) 초안 수. */
  pending_review_count: number;
  /** 커넥터 증적을 마지막으로 수집한 시각. */
  last_collected_at: string | null;
  document_count: number;
  last_assessment: LastAssessment | null;
}

/** `GET /projects/{id}/alerts` 질의 파라미터. */
export interface AlertListParams {
  type?: AlertType;
  unread_only?: boolean;
  limit?: number;
}

/** `PATCH /projects/{id}/alerts/read-all` 응답. */
export interface AlertReadAllResult {
  updated: number;
}
