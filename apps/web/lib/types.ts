/**
 * 백엔드(FastAPI) 응답 계약을 그대로 옮긴 타입 정의.
 * 서버 스키마가 바뀌면 이 파일부터 맞춘다.
 */

/** PRD §3 역할. */
export type UserRole = "org_admin" | "org_member" | "reviewer" | "operator";

/** 인증 종류. */
export type CertType = "ISMS" | "ISMS-P";

/** 업로드 문서의 파싱 상태. */
export type DocumentStatus = "uploaded" | "parsed" | "failed";

/** `GET /auth/me`, `POST /auth/login` 응답. operator 는 org_id 가 없을 수 있다. */
export interface User {
  id: string;
  email: string;
  role: UserRole;
  org_id: string | null;
  created_at?: string;
}

/** `POST /auth/login` 요청 본문. */
export interface LoginInput {
  email: string;
  password: string;
}

/** `GET /projects`, `GET /projects/{id}` 응답. */
export interface Project {
  id: string;
  org_id: string;
  name: string;
  cert_type: CertType;
  is_simplified: boolean;
  scope_text: string | null;
  /** ISO 날짜(YYYY-MM-DD) 또는 null. */
  audit_due_date: string | null;
  created_at: string;
  updated_at?: string;
}

/** `POST /projects` 요청 본문. org_id 는 서버가 세션에서 정한다. */
export interface ProjectCreateInput {
  name: string;
  cert_type: CertType;
  is_simplified: boolean;
  scope_text?: string | null;
  audit_due_date?: string | null;
}

/** `GET /projects/{id}/documents` 응답 항목. `s3_key` 는 내부 경로라 응답에 없다. */
export interface ProjectDocument {
  id: string;
  project_id?: string;
  filename: string;
  mime: string;
  status: DocumentStatus;
  page_count: number | null;
  sha256?: string;
  created_at: string;
  /** status = "failed" 일 때만 채워진다. */
  failure_reason?: string | null;
}

/** `GET /projects/{id}/chunks/search` 질의 파라미터. */
export interface ChunkSearchParams {
  /** 인증 기준 코드(예: "2.1.1"). */
  criterion?: string;
  /** 자유 질의어. */
  q?: string;
  /** 상위 k 건. */
  k?: number;
}

/** `GET /projects/{id}/chunks/search` 결과 1건. */
export interface ChunkSearchHit {
  chunk_id: string;
  document_id: string;
  filename: string;
  page: number | null;
  /** 미리보기 본문. */
  snippet: string;
  /** 유사도 점수. */
  score: number;
}

/** `GET /projects/{id}/chunks/search` 응답. */
export interface ChunkSearchResponse {
  /** 항목 코드로 검색한 경우에만 채워진다. */
  criterion: string | null;
  criterion_title: string | null;
  /** 실제 임베딩에 사용된 질의 텍스트. */
  query: string;
  results: ChunkSearchHit[];
}

/* ------------------------------------------------------------------ */
/* 모의심사 · 갭 리포트                                                */
/* ------------------------------------------------------------------ */

/** 모의심사 1회 실행의 진행 상태. */
export type AssessmentStatus = "queued" | "running" | "done" | "failed";

/** 항목별 판정 결과. */
export type FindingStatus = "met" | "partial" | "unmet" | "unknown";

/** 판정을 확정한 주체. 규칙 판정이 LLM 판정을 덮어쓸 수 있다(PRD F3). */
export type DecidedBy = "rule" | "llm" | "reviewer";

/** 인증기준 장 번호. 1장 관리체계 / 2장 보호대책 / 3장 개인정보. */
export type CriterionChapter = "1" | "2" | "3";

/** 판정 분포. 전체 101개 항목의 합계. */
export interface FindingCounts {
  met: number;
  partial: number;
  unmet: number;
  unknown: number;
}

/**
 * 장별 집계. `readiness` 는 준비도로, PRD F8 기준
 * `(met + 0.5·partial) / (총계 − unknown)` 이다.
 * 서버가 비율(0~1)로 줄지 백분율(0~100)로 줄지 확정되지 않아
 * 화면에서는 `toPercent()` 로 정규화해 쓴다.
 */
export interface ChapterSummary extends FindingCounts {
  total: number;
  readiness: number;
}

/** 판정이 끝난 항목 수 / 전체 항목 수. 진행률 바에 쓴다. */
export interface AssessmentProgress {
  done: number;
  total: number;
}

/** `AssessmentOut.summary`. 실행이 시작되기 전에는 null 이다. */
export interface AssessmentSummary {
  counts: FindingCounts;
  /** 키는 장 번호 문자열("1" | "2" | "3"). */
  by_chapter: Partial<Record<CriterionChapter, ChapterSummary>>;
  progress: AssessmentProgress;
}

/** `POST/GET /projects/{id}/assessments` 응답(AssessmentOut). */
export interface Assessment {
  id: string;
  project_id: string;
  status: AssessmentStatus;
  started_at: string | null;
  finished_at: string | null;
  /** 판정에 사용한 LLM 모델 이름. */
  model: string | null;
  /** 실행 1회 누적 비용(USD). */
  cost_usd: number | null;
  summary: AssessmentSummary | null;
}

/** `GET …/findings` 응답 1건(FindingRow). */
export interface FindingRow {
  id: string;
  /** 인증기준 코드(예: "2.5.3"). */
  criterion_code: string;
  criterion_title: string;
  /** 소분류 명칭(예: "2.5 인증 및 권한관리"). */
  criterion_section: string;
  status: FindingStatus;
  /** 0~1 비율 또는 0~100 백분율. `toPercent()` 로 정규화한다. */
  confidence: number | null;
  decided_by: DecidedBy;
  predicted_defect: string | null;
  recommendation: string | null;
}

/** 판정 근거로 인용된 문서 청크. */
export interface EvidenceChunk {
  id: string;
  document_filename: string;
  page: number | null;
  text: string;
}

/** 커넥터가 수집한 클라우드 증적. */
export interface EvidenceItem {
  id: string;
  /** 수집원(예: "aws"). */
  source: string;
  /** 점검 항목 식별자(예: "s3_public_access"). */
  check_id: string;
  status: string;
  /** 점검 원본 값. 구조는 점검별로 다르다. */
  payload: unknown;
}

/** `GET …/findings/{fid}` 응답(FindingDetail). */
export interface FindingDetail extends FindingRow {
  rationale: string | null;
  evidence_chunks: EvidenceChunk[];
  evidence_items: EvidenceItem[];
}

/**
 * `GET …/findings` 정렬 키.
 * 계약에 값 목록이 명시되지 않아 화면 정렬은 클라이언트에서 처리하고,
 * 이 타입은 서버 정렬을 쓰게 될 때를 위해 남겨 둔다.
 */
export type FindingSort = "code" | "status" | "confidence" | "-confidence";

/** `GET …/findings` 질의 파라미터. */
export interface FindingListParams {
  /** 판정 상태. 다중 선택은 반복 파라미터(`status=met&status=unmet`)로 보낸다. */
  status?: FindingStatus | FindingStatus[];
  chapter?: CriterionChapter;
  /** 코드·항목명 자유 검색어. */
  q?: string;
  sort?: FindingSort;
}
