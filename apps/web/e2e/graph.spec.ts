import { expect, test } from "@playwright/test";

import { loginAsAdmin, openDemoProject } from "./helpers";

/**
 * 지식 그래프 탭 e2e 시나리오.
 *
 * 실행 전제:
 *  - 백엔드가 http://localhost:8000 에서 동작하고 데모 시드(`make demo`)가 적재돼 있다.
 *  - `GET /projects/{id}/graph` 가 배포돼 있다(미배포면 탭이 "준비 중" 안내만 보여 준다).
 *  - 시나리오는 시드 프로젝트를 그대로 쓴다(임시 프로젝트를 만들지 않는다).
 *
 * Cytoscape 는 컨테이너 안에 canvas 를 여러 장 겹쳐 그리므로 가시성은 첫 장으로 본다.
 */

/** 그래프·판정 목록을 받아 오는 데 주는 여유 시간. */
const LOAD_TIMEOUT_MS = 30_000;

/** 지식 그래프 탭을 연다. */
async function openGraphTab(page: import("@playwright/test").Page): Promise<void> {
  await loginAsAdmin(page);
  await openDemoProject(page);
  await page.getByRole("tab", { name: "지식 그래프" }).click();
}

test.describe("지식 그래프", () => {
  test("탭에 들어가면 범례·통계와 그래프 캔버스가 보인다", async ({ page }) => {
    await openGraphTab(page);

    // 통계 헤더: 인증기준은 101개 고정이다(PRD §6 — 장별 16/64/21).
    await expect(page.getByText("항목 101")).toBeVisible({
      timeout: LOAD_TIMEOUT_MS,
    });
    await expect(page.getByText(/문서 \d+ · 증적 \d+ · 연결 \d+건/)).toBeVisible();

    // 범례와 판정 분포 도트.
    await expect(page.getByText("범례", { exact: true })).toBeVisible();
    await expect(page.getByText("문서 근거 인용")).toBeVisible();
    await expect(page.getByText("증적 근거 인용")).toBeVisible();

    // Cytoscape 캔버스.
    await expect(page.locator("canvas").first()).toBeVisible();
  });

  test("장 필터를 2장으로 바꾸면 통계의 항목 수가 줄어든다", async ({ page }) => {
    await openGraphTab(page);
    await expect(page.getByText("항목 101")).toBeVisible({
      timeout: LOAD_TIMEOUT_MS,
    });

    await page.getByRole("combobox").first().click();
    await page.getByRole("option", { name: "2장 보호대책" }).click();

    // 2장은 64개 항목이다.
    await expect(page.getByText("항목 64")).toBeVisible();
  });

  test("판정 상태 토글은 눌린 상태(aria-pressed)를 유지한다", async ({ page }) => {
    await openGraphTab(page);
    await expect(page.getByText("항목 101")).toBeVisible({
      timeout: LOAD_TIMEOUT_MS,
    });

    const unmetButton = page.getByRole("button", { name: "미충족", exact: true });
    await expect(unmetButton).toHaveAttribute("aria-pressed", "false");

    await unmetButton.click();
    await expect(unmetButton).toHaveAttribute("aria-pressed", "true");

    await unmetButton.click();
    await expect(unmetButton).toHaveAttribute("aria-pressed", "false");
  });

  test("보기 초기화를 눌러도 캔버스가 그대로 살아 있다", async ({ page }) => {
    await openGraphTab(page);
    await expect(page.getByText("항목 101")).toBeVisible({
      timeout: LOAD_TIMEOUT_MS,
    });

    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();

    await page.getByRole("button", { name: "보기 초기화" }).click();
    await expect(canvas).toBeVisible();
    await expect(page.getByText("항목 101")).toBeVisible();
  });
});
