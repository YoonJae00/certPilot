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

/** 커넥터 증적 1건의 점검 결과(EvidenceStatus). */
export type EvidenceStatus = "pass" | "fail" | "warn" | "unknown";

/** 판정을 확정한 주체. 규칙 판정이 LLM 판정을 덮어쓸 수 있다(PRD F3). */
export type DecidedBy = "rule" | "llm" | "reviewer";

/**
 * 인증기준 장 번호. 1장 관리체계 / 2장 보호대책 / 3장 개인정보.
 * `summary_json.by_chapter` 의 키 형식(JSON 객체 키라 문자열)이다.
 * 판정 1건의 `chapter` 는 숫자(1|2|3)로 온다.
 */
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
 * 서버는 0~1 비율로 주지만 화면에서는 `toPercent()` 로 정규화해 쓴다.
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

/**
 * `AssessmentOut.summary_json`.
 *
 * 서버는 실행 단계에 따라 채우는 키가 다르다.
 *  - queued: 필드 자체가 null
 *  - running: `progress` 만
 *  - done: `counts` · `by_chapter` · `progress` · `readiness`
 *  - failed: 그때까지의 내용 + `reason`
 * 그래서 하위 키는 모두 선택 항목으로 둔다.
 */
export interface AssessmentSummary {
  counts?: FindingCounts;
  /** 키는 장 번호 문자열("1" | "2" | "3"). */
  by_chapter?: Partial<Record<CriterionChapter, ChapterSummary>>;
  progress?: AssessmentProgress;
  /** 전체 준비도(0~1). 완료된 실행에만 있다. */
  readiness?: number;
  /** 실패 사유. status = "failed" 일 때만 있다. */
  reason?: string;
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
  summary_json: AssessmentSummary | null;
  created_at: string;
}

/** `GET …/findings` 응답 1건(서버 FindingOut). 응답 본문은 이 객체의 배열이다. */
export interface FindingRow {
  id: string;
  /** 인증기준 코드(예: "2.5.3"). */
  criterion_code: string;
  /** 장 번호(1 | 2 | 3). 서버는 숫자로 준다. */
  chapter: number;
  /** 소분류 명칭(예: "2.5 인증 및 권한관리"). */
  section: string;
  /** 인증기준 항목명. */
  title: string;
  status: FindingStatus;
  /** 0~1 비율. `toPercent()` 로 백분율로 바꿔 쓴다. */
  confidence: number;
  /** 판정 근거 문장. 목록 응답에도 들어 있다. */
  rationale: string;
  /** 근거로 인용한 청크 ID 목록. 본문은 상세 응답의 `chunks` 에 있다. */
  evidence_chunk_ids: string[];
  /** 근거로 인용한 증적 ID 목록. 본문은 상세 응답의 `evidence` 에 있다. */
  evidence_ids: string[];
  predicted_defect: string | null;
  recommendation: string | null;
  decided_by: DecidedBy;
  created_at: string;
}

/** 판정 근거로 인용된 문서 청크(서버 FindingChunkOut). */
export interface FindingChunk {
  chunk_id: string;
  document_id: string;
  /** 원본 문서 파일명. */
  filename: string;
  page: number | null;
  text: string;
}

/** 커넥터가 수집한 클라우드 증적(서버 FindingEvidenceOut). */
export interface FindingEvidence {
  evidence_id: string;
  /** 수집원(예: "aws"). */
  source: string;
  /** 점검 항목 식별자(예: "s3_public_access"). */
  check_id: string;
  status: EvidenceStatus;
  collected_at: string;
  /** 점검 원본 값. 구조는 점검별로 다르다. */
  payload_json: Record<string, unknown>;
}

/** `GET …/findings/{fid}` 응답(서버 FindingDetailOut). */
export interface FindingDetail extends FindingRow {
  /** 인증기준 원문 요구사항. */
  criterion_requirement: string;
  chunks: FindingChunk[];
  evidence: FindingEvidence[];
}

/** `GET …/findings` 정렬 키. 서버가 받는 값 목록 그대로다. */
export type FindingSort = "code" | "status" | "confidence" | "-confidence";

/** `GET …/findings` 질의 파라미터. */
export interface FindingListParams {
  /**
   * 판정 상태. 배열로 주면 반복 파라미터(`status=met&status=unmet`)로 나간다.
   * 다만 현재 서버는 단일 값만 바인딩하므로(마지막 값이 이긴다) 화면은 서버 필터에
   * 기대지 않고 목록을 한 번 받아 클라이언트에서 필터한다.
   */
  status?: FindingStatus | FindingStatus[];
  /** 장 번호. 서버는 1~3 정수로 받는다(쿼리 문자열이라 "1" 로 보내도 같다). */
  chapter?: CriterionChapter;
  /** 코드·항목명·소분류·판정 근거 자유 검색어. */
  q?: string;
  sort?: FindingSort;
}
