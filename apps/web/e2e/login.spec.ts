import { expect, test } from "@playwright/test";

import { ADMIN_EMAIL, ADMIN_PASSWORD, login } from "./helpers";

/**
 * 로그인 e2e 시나리오.
 *
 * 실행 전제: 백엔드가 http://localhost:8000 에서 동작하고 데모 시드(`make demo`) 계정이
 * 존재해야 한다. 계정은 E2E_EMAIL / E2E_PASSWORD 로 덮어쓸 수 있다.
 */

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
    await page.getByLabel("이메일").fill(ADMIN_EMAIL);
    await page.getByLabel("비밀번호").fill("wrong-password");
    await page.getByRole("button", { name: "로그인" }).click();

    // Next.js 가 문서 끝에 붙이는 라우트 안내(role=alert)와 섞이지 않게 폼 안에서 찾는다.
    await expect(page.locator("form").getByRole("alert")).toContainText(
      "이메일 또는 비밀번호가 올바르지 않습니다.",
    );
    await expect(page).toHaveURL(/\/login$/);
  });

  test("로그인에 성공하면 프로젝트 목록으로 이동한다", async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

    await expect(
      page.getByRole("heading", { name: "프로젝트", level: 1 }),
    ).toBeVisible();
    // 상단 네비에 로그인 사용자와 로그아웃 버튼이 보인다.
    await expect(page.getByText(ADMIN_EMAIL)).toBeVisible();
    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  });

  test("로그아웃하면 다시 로그인 화면으로 돌아간다", async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

    await page.getByRole("button", { name: "로그아웃" }).click();
    await page.waitForURL("**/login");
    await expect(page.getByRole("heading", { name: "로그인" })).toBeVisible();
  });
});
