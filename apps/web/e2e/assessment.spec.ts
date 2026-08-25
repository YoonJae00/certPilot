import { existsSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

/**
 * "업로드 → 모의심사 → 리포트" e2e 시나리오 (PRD 부록 B Task 6 완료 조건).
 *
 * 실행 전제:
 *  - 백엔드가 http://localhost:8000 에서 동작하고, Celery 워커가 `ingest`/`assess` 잡을 처리한다.
 *  - 아래 시드 계정이 존재하고, 그 조직에 프로젝트가 하나 이상 있다(없으면 첫 테스트가 만든다).
 *  - 업로드용 샘플 문서는 E2E_SAMPLE_DOC 로 지정한다. 없으면 문서 업로드 단계는 건너뛴다.
 *
 * 이번 태스크에서는 시나리오만 작성하고 실행은 하지 않는다(`npx playwright test --list` 로 인식만 확인).
 */
const EMAIL = process.env.E2E_EMAIL ?? "admin-a@example.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "fixture-password-1234";

/** 업로드할 샘플 문서. 시드 스크립트가 만드는 데모 문서를 기본으로 본다. */
const SAMPLE_DOC =
  process.env.E2E_SAMPLE_DOC ??
  path.resolve(__dirname, "../../../data/samples/demo_policy.pdf");

/** 모의심사 완료까지 기다리는 최대 시간. PRD 성능 기준은 101항목 10분. */
const ASSESSMENT_TIMEOUT_MS = Number(
  process.env.E2E_ASSESSMENT_TIMEOUT_MS ?? 10 * 60_000,
);

/** 프로젝트 이름. 실행마다 겹치지 않게 시각을 붙인다. */
const PROJECT_NAME = `e2e 모의심사 ${Date.now()}`;

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

test.describe("모의심사 → 갭 리포트", () => {
  // 판정 완료까지 기다려야 해서 기본 타임아웃보다 길게 잡는다.
  test.describe.configure({ mode: "serial", timeout: ASSESSMENT_TIMEOUT_MS + 60_000 });

  test("문서를 올리고 모의심사를 실행하면 리포트에서 판정과 근거를 볼 수 있다", async ({
    page,
  }) => {
    await login(page);
    await openProject(page);

    // 1) 문서 탭에서 근거 문서를 업로드하고 분석이 끝날 때까지 기다린다.
    await page.getByRole("tab", { name: "문서" }).click();
    await expect(
      page.getByText("문서 상태가 ‘분석 완료’로 바뀌면 모의심사 탭에서 모의심사를 실행하세요."),
    ).toBeVisible();

    if (existsSync(SAMPLE_DOC)) {
      await page.getByLabel("업로드할 문서 파일").setInputFiles(SAMPLE_DOC);
      await page.getByRole("button", { name: "업로드" }).click();
      await expect(
        page.getByText("분석 완료").first(),
      ).toBeVisible({ timeout: 120_000 });
    } else {
      test.info().annotations.push({
        type: "skip-upload",
        description: `샘플 문서가 없어 업로드 단계를 건너뜀: ${SAMPLE_DOC}`,
      });
    }

    // 2) 모의심사 탭에서 실행한다.
    await page.getByRole("tab", { name: "모의심사" }).click();
    const runButton = page.getByRole("button", { name: "모의심사 실행" });
    await expect(runButton).toBeEnabled();
    await runButton.click();

    // 3) 진행률 바가 뜨고, 완료되면 장별 준비도 카드가 나타난다.
    //    아주 빨리 끝나면 진행률 바를 못 볼 수도 있어 완료 뱃지도 함께 본다.
    await expect(
      page
        .getByRole("progressbar", { name: "모의심사 진행률" })
        .or(page.getByText("완료", { exact: true }))
        .first(),
    ).toBeVisible({ timeout: 30_000 });

    // 상태 뱃지가 "완료"로 바뀔 때까지 기다린다(진행률 문구와 섞이지 않게 exact).
    await expect(page.getByText("완료", { exact: true }).first()).toBeVisible({
      timeout: ASSESSMENT_TIMEOUT_MS,
    });
    await expect(page.getByText("1장 관리체계", { exact: true })).toBeVisible();
    await expect(page.getByText("2장 보호대책", { exact: true })).toBeVisible();
    await expect(page.getByText("3장 개인정보", { exact: true })).toBeVisible();
    await expect(page.getByText("판정 분포")).toBeVisible();

    // 4) 리포트 탭에서 판정 테이블을 확인한다.
    await page.getByRole("tab", { name: "리포트" }).click();
    await expect(page.getByRole("columnheader", { name: "코드" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "판정" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "신뢰도" })).toBeVisible();

    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 30_000 });
    const totalRows = await rows.count();
    expect(totalRows).toBeGreaterThan(0);

    // 5) 상태 필터(미충족)를 적용하면 표시 건수가 줄거나 같다.
    await page.getByRole("button", { name: "미충족", exact: true }).click();
    await expect(page.getByText(/전체 \d+건 중 \d+건 표시/)).toBeVisible();
    await page.getByRole("button", { name: "선택 해제" }).click();

    // 6) 행을 클릭하면 상세 드로어가 열리고 근거 영역이 보인다.
    await rows.first().click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText(/근거 문서 \(\d+건\)/)).toBeVisible();
    await expect(drawer.getByText("판정 근거")).toBeVisible();
    await expect(drawer.getByText("예상 결함")).toBeVisible();
    await expect(drawer.getByText("개선 권고")).toBeVisible();
    await drawer.getByRole("button", { name: "닫기" }).click();
    await expect(drawer).toBeHidden();
  });

  test("갭 리포트를 XLSX 로 내려받는다", async ({ page }) => {
    await login(page);
    await openProject(page);
    await page.getByRole("tab", { name: "리포트" }).click();

    const exportButton = page.getByRole("button", { name: "XLSX 내보내기" });
    await expect(exportButton).toBeVisible({ timeout: 30_000 });

    const downloadPromise = page.waitForEvent("download");
    await exportButton.click();
    const download = await downloadPromise;

    expect(download.suggestedFilename()).toMatch(/^갭리포트_.+_\d{8}\.xlsx$/);
  });
});
