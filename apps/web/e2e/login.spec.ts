import { expect, test } from "@playwright/test";

/**
 * 로그인 e2e 시나리오.
 *
 * 실행 전제: 백엔드가 http://localhost:8000 에서 동작하고 아래 시드 계정이 존재해야 한다.
 * 기본값은 API 테스트 픽스처(`apps/api/tests/conftest.py`)의 org_admin 계정을 따르며,
 * 시드 스크립트가 확정되면 E2E_EMAIL / E2E_PASSWORD 환경변수로 덮어쓴다.
 */
const EMAIL = process.env.E2E_EMAIL ?? "admin-a@example.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "fixture-password-1234";

test.describe("로그인", () => {
  test("미인증 상태로 접근하면 로그인 화면으로 이동한다", async ({ page }) => {
    await page.goto("/projects");
    await page.waitForURL("**/login");
    await expect(
      page.getByRole("heading", { name: "로그인" }),
    ).toBeVisible();
  });

  test("잘못된 비밀번호는 한국어 오류를 보여 준다", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("이메일").fill(EMAIL);
    await page.getByLabel("비밀번호").fill("wrong-password");
    await page.getByRole("button", { name: "로그인" }).click();

    await expect(page.getByRole("alert")).toContainText(
      "이메일 또는 비밀번호가 올바르지 않습니다.",
    );
    await expect(page).toHaveURL(/\/login$/);
  });

  test("로그인에 성공하면 프로젝트 목록으로 이동한다", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("이메일").fill(EMAIL);
    await page.getByLabel("비밀번호").fill(PASSWORD);
    await page.getByRole("button", { name: "로그인" }).click();

    await page.waitForURL("**/projects");
    await expect(
      page.getByRole("heading", { name: "프로젝트", level: 1 }),
    ).toBeVisible();
    // 상단 네비에 로그인 사용자와 로그아웃 버튼이 보인다.
    await expect(page.getByText(EMAIL)).toBeVisible();
    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  });

  test("로그아웃하면 다시 로그인 화면으로 돌아간다", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("이메일").fill(EMAIL);
    await page.getByLabel("비밀번호").fill(PASSWORD);
    await page.getByRole("button", { name: "로그인" }).click();
    await page.waitForURL("**/projects");

    await page.getByRole("button", { name: "로그아웃" }).click();
    await page.waitForURL("**/login");
    await expect(page.getByRole("heading", { name: "로그인" })).toBeVisible();
  });
});
