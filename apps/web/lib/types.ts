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
