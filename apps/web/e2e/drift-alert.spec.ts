import { expect, test, type Page } from "@playwright/test";

import { loginAsAdmin, openDemoProject } from "./helpers";

/**
 * "drift 알림" e2e 시나리오 (PRD 부록 B Task 10 완료 조건).
 *
 * 실행 전제:
 *  - 백엔드가 http://localhost:8000 에서 동작하고 데모 시드(`make demo`)가 적재돼 있다.
 *  - 시드는 커넥터 수집 2회 + 설정 변경으로 미읽음 `alerts(type=drift)` 를 만들어 둔다.
 *    읽음 처리는 그 알림을 소비하므로, 다시 확인하려면 `make demo` 로 되살린다.
 */

/** 알림 카드 안의 읽지 않은 항목(읽음 처리 버튼이 있는 항목). */
function alertItems(page: Page) {
  return page
    .locator("li")
    .filter({ has: page.getByRole("button", { name: "읽음 처리" }) });
}

test.describe("유지 대시보드 · drift 알림", () => {
  test.describe.configure({ mode: "serial" });

  test("프로젝트를 열면 대시보드 탭이 기본으로 보이고 카드가 채워진다", async ({
    page,
  }) => {
    await loginAsAdmin(page);
    await openDemoProject(page);

    // 기본 탭이 대시보드다(따로 클릭하지 않는다).
    await expect(page.getByRole("tab", { name: "대시보드" })).toHaveAttribute(
      "data-state",
      "active",
    );

    await expect(page.getByText("사후심사 D-day")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("검수 대기", { exact: true })).toBeVisible();
    await expect(page.getByText("문서 · 증적")).toBeVisible();
    await expect(page.getByText("미충족 Top 5")).toBeVisible();
    await expect(page.getByText("최근 알림")).toBeVisible();
  });

  test("drift 알림이 있으면 목록에 뜨고 읽음 처리할 수 있다", async ({ page }) => {
    await loginAsAdmin(page);
    await openDemoProject(page);
    await expect(page.getByText("최근 알림")).toBeVisible({ timeout: 30_000 });

    // 변경 감지 알림이 메시지와 함께 보인다.
    await expect(page.getByText("설정 변경", { exact: true }).first()).toBeVisible();

    const unread = alertItems(page).first();
    if ((await unread.count()) === 0) {
      // 같은 시드로 이미 읽음 처리한 뒤라면 0건 상태만 확인한다(`make demo` 로 되살린다).
      test.info().annotations.push({
        type: "skip-mark-read",
        description:
          "읽지 않은 알림이 없어 읽음 처리 단계를 건너뜀. `make demo` 로 미읽음 알림을 되살릴 수 있다.",
      });
      await expect(page.getByText("읽지 않은 알림 0건")).toBeVisible();
      return;
    }

    // 읽음 처리하면 그 항목의 버튼이 사라지고 "읽음" 표시로 바뀐다.
    // (필터 조건이 "읽음 처리 버튼이 있는 항목"이라 클릭 뒤에는 메시지로 다시 찾는다.)
    const message = (await unread.locator("p").innerText()).trim();
    await unread.getByRole("button", { name: "읽음 처리" }).click();

    const item = page.locator("li").filter({ hasText: message }).first();
    await expect(item.getByText("읽음", { exact: true })).toBeVisible();
    await expect(item.getByRole("button", { name: "읽음 처리" })).toHaveCount(0);
  });

  test("모두 읽음을 누르면 읽지 않은 알림이 0건이 된다", async ({ page }) => {
    await loginAsAdmin(page);
    await openDemoProject(page);

    const markAll = page.getByRole("button", { name: "모두 읽음" });
    await expect(markAll).toBeVisible({ timeout: 30_000 });

    if (await markAll.isDisabled()) {
      // 이미 다 읽은 상태면 그대로 0건이어야 한다.
      await expect(page.getByText("읽지 않은 알림 0건")).toBeVisible();
      return;
    }

    await markAll.click();
    await expect(page.getByText("읽지 않은 알림 0건")).toBeVisible();
    await expect(markAll).toBeDisabled();
    await expect(alertItems(page)).toHaveCount(0);
  });
});
