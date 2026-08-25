import { expect, test, type Page } from "@playwright/test";

/**
 * "drift 알림" e2e 시나리오 (PRD 부록 B Task 10 완료 조건).
 *
 * 실행 전제:
 *  - 백엔드가 http://localhost:8000 에서 동작한다.
 *  - 아래 시드 계정이 존재하고, 그 조직에 프로젝트가 하나 이상 있다.
 *  - 대상 프로젝트에 커넥터 수집이 두 번 이상 돌아 `alerts(type=drift)` 가 하나 이상 있다
 *    (없으면 알림 검증 단계는 건너뛴다 — 변경 감지는 실제 계정 설정 변경이 필요하다).
 *
 * 이번 태스크에서는 시나리오만 작성하고 실행은 하지 않는다(`npx playwright test --list` 로 인식만 확인).
 */
const EMAIL = process.env.E2E_EMAIL ?? "admin-a@example.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "fixture-password-1234";

/** 프로젝트가 하나도 없을 때 만들 이름. */
const PROJECT_NAME = `e2e 대시보드 ${Date.now()}`;

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("이메일").fill(EMAIL);
  await page.getByLabel("비밀번호").fill(PASSWORD);
  await page.getByRole("button", { name: "로그인" }).click();
  await page.waitForURL("**/projects");
}

/** 목록에서 첫 프로젝트로 들어간다. 없으면 새로 만든다. */
async function openProject(page: Page): Promise<void> {
  const firstRow = page.locator("tbody tr").first();

  if ((await firstRow.count()) === 0) {
    await page.getByRole("button", { name: "새 프로젝트" }).click();
    await page.getByLabel("프로젝트 이름").fill(PROJECT_NAME);
    await page.getByRole("button", { name: "만들기" }).click();
    await expect(page.getByText(PROJECT_NAME)).toBeVisible();
  }

  await page.locator("tbody tr").first().click();
  await page.waitForURL(/\/projects\/[^/]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

/** 알림 카드 안의 목록 항목. */
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
    await login(page);
    await openProject(page);

    // 기본 탭이 대시보드다(따로 클릭하지 않는다).
    await expect(page.getByRole("tab", { name: "대시보드" })).toHaveAttribute(
      "data-state",
      "active",
    );

    await expect(page.getByText("사후심사 D-day")).toBeVisible();
    await expect(page.getByText("검수 대기", { exact: true })).toBeVisible();
    await expect(page.getByText("문서 · 증적")).toBeVisible();
    await expect(page.getByText("미충족 Top 5")).toBeVisible();
    await expect(page.getByText("최근 알림")).toBeVisible();
  });

  test("drift 알림이 있으면 목록에 뜨고 읽음 처리할 수 있다", async ({ page }) => {
    await login(page);
    await openProject(page);

    const driftBadge = page.getByText("설정 변경", { exact: true }).first();
    if ((await driftBadge.count()) === 0) {
      test.info().annotations.push({
        type: "skip-drift",
        description:
          "이 프로젝트에 drift 알림이 없어 읽음 처리 검증을 건너뜀(수집 2회 + 설정 변경 필요).",
      });
      return;
    }

    // 변경 감지 알림이 메시지와 함께 보인다.
    await expect(driftBadge).toBeVisible();

    const unread = alertItems(page).first();
    await expect(unread).toBeVisible();

    // 읽음 처리하면 그 항목의 버튼이 사라지고 "읽음" 표시로 바뀐다.
    await unread.getByRole("button", { name: "읽음 처리" }).click();
    await expect(unread.getByText("읽음", { exact: true })).toBeVisible();
  });

  test("모두 읽음을 누르면 읽지 않은 알림이 0건이 된다", async ({ page }) => {
    await login(page);
    await openProject(page);

    const markAll = page.getByRole("button", { name: "모두 읽음" });
    await expect(markAll).toBeVisible();

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
