/**
 * e2e 공용 헬퍼.
 *
 * 시나리오는 모두 데모 시드(`make demo`)가 만든 조직·프로젝트를 그대로 쓴다.
 * 임시 프로젝트를 만들지 않으므로 실행을 반복해도 데이터가 쌓이지 않는다.
 */

import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** 백엔드 주소. 화면이 쓰는 NEXT_PUBLIC_API_URL 과 같은 값이어야 한다. */
export const API_BASE_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

/** 조직 관리자(데모 시드). */
export const ADMIN_EMAIL = process.env.E2E_EMAIL ?? "admin@demofintech.kr";
export const ADMIN_PASSWORD = process.env.E2E_PASSWORD ?? "demo1234!";

/** 심사원(데모 시드). 조직에 속하지 않고 검수 큐만 쓴다. */
export const REVIEWER_EMAIL =
  process.env.E2E_REVIEWER_EMAIL ?? "reviewer@certpilot.kr";
export const REVIEWER_PASSWORD =
  process.env.E2E_REVIEWER_PASSWORD ?? "demo1234!";

/** 데모 시드가 만드는 프로젝트 이름. 시나리오는 이 프로젝트를 재사용한다. */
export const DEMO_PROJECT_NAME =
  process.env.E2E_PROJECT_NAME ?? "데모핀테크 ISMS-P 간편인증";

/** 로그인 화면에서 계정으로 들어간다. `waitFor` 로 도착 주소를 지정한다. */
export async function login(
  page: Page,
  email: string,
  password: string,
  waitFor = "**/projects",
): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(password);
  await page.getByRole("button", { name: "로그인" }).click();
  await page.waitForURL(waitFor);
}

/** 조직 관리자로 로그인한다. */
export function loginAsAdmin(page: Page): Promise<void> {
  return login(page, ADMIN_EMAIL, ADMIN_PASSWORD);
}

/**
 * 심사원으로 로그인한다.
 *
 * 로그인 화면은 역할과 무관하게 /projects 로 보내고, 헤더 가드가 곧바로 검수 큐로
 * 되돌린다. 그래서 도착지는 /review 다.
 */
export function loginAsReviewer(page: Page): Promise<void> {
  return login(page, REVIEWER_EMAIL, REVIEWER_PASSWORD, "**/review");
}

/**
 * 프로젝트 목록에서 데모 프로젝트를 연다.
 *
 * 목록은 클라이언트에서 받아 오므로 표가 그려질 때까지 기다린 뒤 이름으로 고른다
 * (첫 행을 그냥 누르면 다른 프로젝트가 열릴 수 있다).
 */
export async function openDemoProject(page: Page): Promise<void> {
  await page.goto("/projects");

  const row = page
    .locator("tbody tr")
    .filter({ hasText: DEMO_PROJECT_NAME })
    .first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  await row.click();

  await page.waitForURL(/\/projects\/[^/]+$/);
  await expect(
    page.getByRole("heading", { level: 1, name: DEMO_PROJECT_NAME }),
  ).toBeVisible();
}

/** 백엔드에 직접 로그인한 API 컨텍스트를 만든다(브라우저 세션과 별개다). */
export async function apiLogin(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<void> {
  const response = await request.post(`${API_BASE_URL}/auth/login`, {
    data: { email, password },
  });
  expect(response.ok(), `API 로그인 실패: ${response.status()}`).toBeTruthy();
}

/** 데모 프로젝트 id 를 API 로 찾는다. */
export async function findDemoProjectId(
  request: APIRequestContext,
): Promise<string> {
  const response = await request.get(`${API_BASE_URL}/projects`);
  expect(response.ok(), `프로젝트 목록 조회 실패: ${response.status()}`).toBeTruthy();
  const projects = (await response.json()) as { id: string; name: string }[];
  const project =
    projects.find((item) => item.name === DEMO_PROJECT_NAME) ?? projects[0];
  expect(project, "데모 프로젝트가 없다. `make demo` 를 먼저 실행한다.").toBeTruthy();
  return project.id;
}

/**
 * 검수 대기 초안을 1건 만들어 큐에 올린다(조직 관리자 권한).
 *
 * 승인·반려는 과제를 소비하므로, 각 시나리오가 자기 과제를 직접 준비한다.
 * 데모 시드의 과제 1건에 의존하지 않아 연속 실행에도 안전하다.
 */
export async function createPendingDraft(
  request: APIRequestContext,
  kind: "sow" | "policy" = "sow",
): Promise<string> {
  await apiLogin(request, ADMIN_EMAIL, ADMIN_PASSWORD);
  const projectId = await findDemoProjectId(request);

  const response = await request.post(
    `${API_BASE_URL}/projects/${projectId}/drafts`,
    { data: { kind } },
  );
  expect(response.ok(), `초안 생성 실패: ${response.status()}`).toBeTruthy();
  const draft = (await response.json()) as { id: string };
  return draft.id;
}
