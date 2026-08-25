/**
 * 백엔드 API 클라이언트.
 *
 * - 인증은 서명 세션 쿠키(`certpilot_session`, HttpOnly)이므로 항상 `credentials: "include"`.
 * - 실패 시 FastAPI 기본 오류 형식 `{detail: string}` 에서 메시지를 뽑아 ApiError 로 던진다.
 */

import type {
  Assessment,
  ChunkSearchParams,
  ChunkSearchResponse,
  FindingDetail,
  FindingListParams,
  FindingRow,
  LoginInput,
  Project,
  ProjectCreateInput,
  ProjectDocument,
  User,
} from "@/lib/types";

/** API 기본 주소. 배포 환경에서는 NEXT_PUBLIC_API_URL 로 덮어쓴다. */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** API 호출 실패를 나타내는 오류. status 로 401(미인증) 등을 구분한다. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 401(미인증) 여부. 라우트 가드에서 /login 리다이렉트 판단에 쓴다. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** 상태 코드별 기본 한국어 메시지. detail 이 없을 때만 쓴다. */
function defaultMessage(status: number): string {
  switch (status) {
    case 400:
      return "요청 내용이 올바르지 않습니다.";
    case 401:
      return "로그인이 필요합니다.";
    case 403:
      return "권한이 없습니다.";
    case 404:
      return "요청한 자원을 찾을 수 없습니다.";
    case 409:
      return "이미 처리된 요청이거나 충돌이 발생했습니다.";
    case 413:
      return "파일 크기가 허용 범위를 넘었습니다.";
    case 422:
      return "입력값을 다시 확인해 주세요.";
    default:
      return status >= 500
        ? "서버에 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
        : "요청을 처리하지 못했습니다.";
  }
}

/** FastAPI 오류 본문에서 사람이 읽을 메시지를 뽑는다. */
function extractDetail(body: unknown, status: number): string {
  if (typeof body === "string" && body.trim() !== "") return body;
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim() !== "") return detail;
    // 422 검증 오류는 detail 이 배열이다.
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : null,
        )
        .filter((msg): msg is string => Boolean(msg));
      if (messages.length > 0) return messages.join(" / ");
    }
  }
  return defaultMessage(status);
}

interface RequestOptions {
  method?: string;
  /** JSON 본문. body 와 동시에 쓰지 않는다. */
  json?: unknown;
  /** FormData 등 원본 본문(multipart 업로드용). */
  body?: BodyInit;
  /** 쿼리 파라미터. undefined/null/빈 문자열은 제외하고, 배열은 반복 파라미터로 붙인다. */
  query?: Record<
    string,
    string | number | boolean | readonly string[] | undefined | null
  >;
  signal?: AbortSignal;
}

function buildUrl(
  path: string,
  query?: RequestOptions["query"],
): string {
  const url = `${API_BASE_URL}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      // 다중 값은 `key=a&key=b` 형태로 반복해서 보낸다.
      for (const item of value) {
        if (item === "") continue;
        params.append(key, String(item));
      }
      continue;
    }
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/** 공통 fetch 래퍼. 성공 시 파싱된 본문, 실패 시 ApiError 를 던진다. */
export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", json, body, query, signal } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined = body;

  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(json);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: payload,
      credentials: "include",
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "서버에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요.");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text) as unknown;
    } catch {
      parsed = text;
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(parsed, response.status));
  }

  return parsed as T;
}

/** 파일 다운로드용 fetch. XLSX 등 바이너리 응답을 Blob 으로 받는다. */
export async function apiFetchBlob(
  path: string,
  options: Pick<RequestOptions, "query" | "signal"> = {},
): Promise<Blob> {
  const { query, signal } = options;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "서버에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요.");
  }

  if (!response.ok) {
    // 오류 응답은 JSON({detail}) 이므로 본문을 텍스트로 읽어 메시지를 뽑는다.
    const text = await response.text();
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text) as unknown;
    } catch {
      /* JSON 이 아니면 원문을 그대로 쓴다. */
    }
    throw new ApiError(response.status, extractDetail(parsed, response.status));
  }

  return response.blob();
}

/* ------------------------------------------------------------------ */
/* 인증                                                                */
/* ------------------------------------------------------------------ */

export const authApi = {
  /** 로그인. 성공 시 Set-Cookie 로 세션이 발급된다. */
  login(input: LoginInput): Promise<User> {
    return apiFetch<User>("/auth/login", { method: "POST", json: input });
  },
  /** 로그아웃. 세션 쿠키를 만료시킨다. */
  logout(): Promise<void> {
    return apiFetch<void>("/auth/logout", { method: "POST" });
  },
  /** 현재 세션 사용자. 미인증이면 401(ApiError). */
  me(signal?: AbortSignal): Promise<User> {
    return apiFetch<User>("/auth/me", { signal });
  },
};

/* ------------------------------------------------------------------ */
/* 프로젝트                                                            */
/* ------------------------------------------------------------------ */

export const projectsApi = {
  /** 자기 조직 프로젝트 목록. */
  list(signal?: AbortSignal): Promise<Project[]> {
    return apiFetch<Project[]>("/projects", { signal });
  },
  /** 프로젝트 단건. */
  get(projectId: string, signal?: AbortSignal): Promise<Project> {
    return apiFetch<Project>(`/projects/${projectId}`, { signal });
  },
  /** 프로젝트 생성. org_admin 만 가능하다. */
  create(input: ProjectCreateInput): Promise<Project> {
    return apiFetch<Project>("/projects", { method: "POST", json: input });
  },
};

/* ------------------------------------------------------------------ */
/* 문서 · 청크 (백엔드 구현 진행 중, 계약대로 미리 작성)               */
/* ------------------------------------------------------------------ */

export const documentsApi = {
  /** 프로젝트 문서 목록. */
  list(projectId: string, signal?: AbortSignal): Promise<ProjectDocument[]> {
    return apiFetch<ProjectDocument[]>(`/projects/${projectId}/documents`, {
      signal,
    });
  },
  /** 문서 업로드(multipart/form-data). Content-Type 은 브라우저가 붙인다. */
  upload(projectId: string, file: File): Promise<ProjectDocument> {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<ProjectDocument>(`/projects/${projectId}/documents`, {
      method: "POST",
      body: form,
    });
  },
};

export const chunksApi = {
  /** 기준/질의어 기반 청크 검색. */
  search(
    projectId: string,
    params: ChunkSearchParams,
    signal?: AbortSignal,
  ): Promise<ChunkSearchResponse> {
    return apiFetch<ChunkSearchResponse>(
      `/projects/${projectId}/chunks/search`,
      {
        query: { criterion: params.criterion, q: params.q, k: params.k },
        signal,
      },
    );
  },
};

/* ------------------------------------------------------------------ */
/* 모의심사 · 갭 리포트                                                */
/* ------------------------------------------------------------------ */

export const assessmentsApi = {
  /** 모의심사 실행. 202 로 큐에 올라간 실행 정보를 돌려준다. */
  create(projectId: string): Promise<Assessment> {
    return apiFetch<Assessment>(`/projects/${projectId}/assessments`, {
      method: "POST",
    });
  },
  /** 실행 이력(최신순). */
  list(projectId: string, signal?: AbortSignal): Promise<Assessment[]> {
    return apiFetch<Assessment[]>(`/projects/${projectId}/assessments`, {
      signal,
    });
  },
  /** 실행 단건. 진행률 폴링에 쓴다. */
  get(
    projectId: string,
    assessmentId: string,
    signal?: AbortSignal,
  ): Promise<Assessment> {
    return apiFetch<Assessment>(
      `/projects/${projectId}/assessments/${assessmentId}`,
      { signal },
    );
  },
  /**
   * 항목별 판정 목록(FindingOut 배열).
   *
   * 화면에서는 101개 전체를 한 번만 받아 클라이언트에서 필터·정렬한다
   * (서버 `status` 필터가 단일 값만 받아 다중 상태 선택을 표현하지 못한다).
   * params 는 서버 필터를 쓰게 될 때를 위해 그대로 전달할 수 있게 열어 둔다.
   */
  findings(
    projectId: string,
    assessmentId: string,
    params: FindingListParams = {},
    signal?: AbortSignal,
  ): Promise<FindingRow[]> {
    return apiFetch<FindingRow[]>(
      `/projects/${projectId}/assessments/${assessmentId}/findings`,
      {
        query: {
          status: Array.isArray(params.status)
            ? params.status
            : params.status,
          chapter: params.chapter,
          q: params.q,
          sort: params.sort,
        },
        signal,
      },
    );
  },
  /** 판정 상세(근거 청크·클라우드 증적 포함). */
  finding(
    projectId: string,
    assessmentId: string,
    findingId: string,
    signal?: AbortSignal,
  ): Promise<FindingDetail> {
    return apiFetch<FindingDetail>(
      `/projects/${projectId}/assessments/${assessmentId}/findings/${findingId}`,
      { signal },
    );
  },
  /** 갭 리포트 XLSX 원본. 파일명은 호출 측에서 정한다. */
  report(
    projectId: string,
    assessmentId: string,
    signal?: AbortSignal,
  ): Promise<Blob> {
    return apiFetchBlob(
      `/projects/${projectId}/assessments/${assessmentId}/report.xlsx`,
      { signal },
    );
  },
};

/** 예상치 못한 오류까지 포함해 사용자에게 보여줄 문구를 만든다. */
export function toMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "알 수 없는 오류가 발생했습니다.";
}
