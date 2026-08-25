import { expect, test } from "@playwright/test";

import { createPendingDraft, loginAsReviewer } from "./helpers";

/**
 * "검수 큐 → 편집 → 승인" e2e 시나리오 (PRD §7 F6, 부록 B Task 9 완료 조건).
 *
 * 실행 전제:
 *  - 백엔드가 http://localhost:8000 에서 동작하고 데모 시드(`make demo`)가 적재돼 있다.
 *  - 심사원 계정은 E2E_REVIEWER_EMAIL / E2E_REVIEWER_PASSWORD 로 덮어쓸 수 있다.
 *
 * 승인·반려는 검수 과제를 소비하므로, 결정이 필요한 시나리오는 API 로 초안을 1건씩
 * 새로 만들어 자기 과제를 준비한다(시드가 만든 과제 1건에 기대지 않는다).
 */

test.describe("검수 워크플로", () => {
  test.describe.configure({ mode: "serial" });

  test("심사원으로 로그인하면 검수 큐가 기본 화면이다", async ({ page }) => {
    await loginAsReviewer(page);

    await expect(page.getByRole("heading", { name: "검수 큐", level: 1 })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "초안 종류" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "확인 필요" })).toBeVisible();
    // 심사원에게는 조직 화면 링크를 노출하지 않는다.
    await expect(page.getByRole("link", { name: "프로젝트" })).toHaveCount(0);
  });

  test("심사원이 /projects 로 가면 검수 큐로 되돌아온다", async ({ page }) => {
    await loginAsReviewer(page);

    await page.goto("/projects");
    await page.waitForURL("**/review");
    await expect(page.getByRole("heading", { name: "검수 큐", level: 1 })).toBeVisible();
  });

  test("초안을 열어 한 칸을 고치고 승인한다", async ({ page, request }) => {
    // 이 시나리오가 승인할 과제를 직접 만든다(큐에서 가장 위에 올라온다).
    await createPendingDraft(request);
    await loginAsReviewer(page);

    // 1) 큐에서 첫 과제를 연다. 여는 순간 나에게 배정된다.
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 30_000 });
    await rows.first().click();
    await page.waitForURL(/\/review\/[^/]+$/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    // 확인 필요 뱃지와 하단 고정 바 두 곳에 같은 문구가 나온다.
    await expect(page.getByText(/확인 필요/).first()).toBeVisible();

    // 2) 첫 편집 칸을 클릭해 입력으로 바꾸고 값을 채운다.
    //    운영명세서는 "… 운영 현황" 버튼, 정책 초안은 "… 본문" textarea 다.
    const sowCell = page.getByRole("button", { name: /운영 현황$/ }).first();
    const policyBody = page.getByRole("textbox", { name: /본문$/ }).first();
    const reviewedText = `심사원 확인 ${Date.now()}`;

    if ((await sowCell.count()) > 0) {
      await sowCell.click();
      const editor = page.getByRole("textbox", { name: /운영 현황$/ }).first();
      await editor.fill(reviewedText);
      await editor.blur();
    } else {
      await policyBody.fill(reviewedText);
    }

    // 3) 하단 고정 바에서 저장한다. 서버가 DOCX 를 다시 만든다.
    const save = page.getByRole("button", { name: "저장" });
    await expect(save).toBeEnabled();
    await save.click();
    await expect(
      page.getByText("편집 내용을 저장했습니다. 문서를 다시 만들었습니다."),
    ).toBeVisible({ timeout: 30_000 });

    // 4) 승인 다이얼로그를 확인하고 승인한다.
    await page.getByRole("button", { name: "승인" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("초안을 승인할까요?")).toBeVisible();
    await dialog.getByRole("button", { name: "승인" }).click();

    // 5) 큐로 돌아오고 성공 토스트가 뜬다.
    await page.waitForURL("**/review");
    await expect(
      page.getByText("승인했습니다. 고객사가 문서를 내려받을 수 있습니다."),
    ).toBeVisible();
  });

  test("반려에는 사유가 필요하다", async ({ page, request }) => {
    // 앞 시나리오가 과제를 승인해 버리므로 반려할 과제도 직접 만든다.
    await createPendingDraft(request);
    await loginAsReviewer(page);

    const pendingRow = page
      .locator("tbody tr")
      .filter({ hasText: "검수 대기" })
      .first();
    await expect(pendingRow).toBeVisible({ timeout: 30_000 });
    await pendingRow.click();
    await page.waitForURL(/\/review\/[^/]+$/);

    await page.getByRole("button", { name: "반려" }).click();
    const dialog = page.getByRole("dialog");
    // 사유 없이 누르면 화면에서 먼저 막는다(서버도 400 으로 막는다).
    await dialog.getByRole("button", { name: "반려" }).click();
    await expect(dialog.getByRole("alert")).toContainText(
      "반려 사유를 입력해 주세요.",
    );

    await dialog
      .getByLabel("반려 사유 (필수)")
      .fill("3조 보관 기간이 내부 규정과 다릅니다. 확인 후 다시 제출해 주세요.");
    await dialog.getByRole("button", { name: "반려" }).click();

    await page.waitForURL("**/review");
    await expect(
      page.getByText("반려했습니다. 고객사에 사유를 전달했습니다."),
    ).toBeVisible();
  });
});
