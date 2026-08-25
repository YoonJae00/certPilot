import { existsSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { loginAsAdmin, openDemoProject } from "./helpers";

/**
 * "업로드 → 모의심사 → 리포트" e2e 시나리오 (PRD 부록 B Task 6 완료 조건).
 *
 * 실행 전제:
 *  - 백엔드가 http://localhost:8000 에서 동작하고 데모 시드(`make demo`)가 적재돼 있다.
 *  - 시나리오는 시드 프로젝트를 그대로 쓴다(임시 프로젝트를 만들지 않는다).
 *  - 업로드 단계는 선택이다. E2E_SAMPLE_DOC 로 파일을 지정하면 업로드까지 확인한다
 *    (분석은 Celery 워커가 처리하므로 워커 없이 돌릴 때는 지정하지 않는다).
 */

/** 업로드할 샘플 문서. 지정하지 않으면 업로드 단계를 건너뛴다. */
const SAMPLE_DOC = process.env.E2E_SAMPLE_DOC ?? "";

/** 모의심사 완료까지 기다리는 최대 시간. PRD 성능 기준은 101항목 10분. */
const ASSESSMENT_TIMEOUT_MS = Number(
  process.env.E2E_ASSESSMENT_TIMEOUT_MS ?? 10 * 60_000,
);

test.describe("모의심사 → 갭 리포트", () => {
  // 판정 완료까지 기다려야 해서 기본 타임아웃보다 길게 잡는다.
  test.describe.configure({ mode: "serial", timeout: ASSESSMENT_TIMEOUT_MS + 60_000 });

  test("문서를 올리고 모의심사를 실행하면 리포트에서 판정과 근거를 볼 수 있다", async ({
    page,
  }) => {
    await loginAsAdmin(page);
    await openDemoProject(page);

    // 1) 문서 탭에서 근거 문서가 분석 완료 상태인지 본다.
    await page.getByRole("tab", { name: "문서" }).click();
    await expect(
      page.getByText("문서 상태가 ‘분석 완료’로 바뀌면 모의심사 탭에서 모의심사를 실행하세요."),
    ).toBeVisible();
    await expect(page.getByText("분석 완료").first()).toBeVisible({
      timeout: 30_000,
    });

    if (SAMPLE_DOC && existsSync(SAMPLE_DOC)) {
      await page.getByLabel("업로드할 문서 파일").setInputFiles(SAMPLE_DOC);
      await page.getByRole("button", { name: "업로드" }).click();
      await expect(
        page.getByText("문서를 업로드했습니다. 분석이 끝나면 상태가 바뀝니다."),
      ).toBeVisible({ timeout: 60_000 });
    } else {
      test.info().annotations.push({
        type: "skip-upload",
        description:
          "E2E_SAMPLE_DOC 가 없어 업로드 단계를 건너뜀(시드 문서로 분석 완료 상태만 확인).",
      });
    }

    // 2) 모의심사 탭에서 실행한다.
    await page.getByRole("tab", { name: "모의심사" }).click();
    const runButton = page.getByRole("button", { name: "모의심사 실행" });
    await expect(runButton).toBeEnabled({ timeout: 30_000 });

    // 실행 이력은 최신순이라 방금 만든 실행이 맨 위에 새 행으로 붙는다.
    const historyRows = page.locator("tbody tr");
    await expect(historyRows.first()).toBeVisible({ timeout: 30_000 });
    const historyBefore = await historyRows.count();
    await runButton.click();

    // 3) 새 실행이 이력 맨 위에 올라오고, 끝나면 상태가 "완료"로 바뀐다.
    //    (아주 빨리 끝나면 진행률 바를 못 볼 수 있어 완료 상태만 기다린다.)
    await expect(historyRows).toHaveCount(historyBefore + 1, { timeout: 30_000 });
    await expect(historyRows.first()).toContainText("완료", {
      timeout: ASSESSMENT_TIMEOUT_MS,
    });

    // 완료된 실행의 집계가 장별 준비도·판정 분포로 그려진다.
    await expect(page.getByText("1장 관리체계", { exact: true })).toBeVisible();
    await expect(page.getByText("2장 보호대책", { exact: true })).toBeVisible();
    await expect(page.getByText("3장 개인정보", { exact: true })).toBeVisible();
    await expect(page.getByText("판정 분포")).toBeVisible();

    // 4) 리포트 탭에서 판정 테이블을 확인한다.
    await page.getByRole("tab", { name: "리포트" }).click();
    await expect(page.getByText(/전체 \d+건 중 \d+건 표시/)).toBeVisible({
      timeout: 30_000,
    });
    // "판정"은 "판정 주체" 헤더와도 겹치므로 정확히 일치하는 것만 찾는다.
    await expect(
      page.getByRole("columnheader", { name: "코드", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "판정", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "신뢰도", exact: true }),
    ).toBeVisible();

    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible();
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
    await expect(drawer.getByText(/근거 문서 \(\d+건\)/)).toBeVisible({
      timeout: 30_000,
    });
    await expect(drawer.getByText("판정 근거")).toBeVisible();
    await expect(drawer.getByText("예상 결함")).toBeVisible();
    await expect(drawer.getByText("개선 권고")).toBeVisible();
    await drawer.getByRole("button", { name: "닫기" }).click();
    await expect(drawer).toBeHidden();
  });

  test("갭 리포트를 XLSX 로 내려받는다", async ({ page }) => {
    await loginAsAdmin(page);
    await openDemoProject(page);
    await page.getByRole("tab", { name: "리포트" }).click();

    const exportButton = page.getByRole("button", { name: "XLSX 내보내기" });
    await expect(exportButton).toBeVisible({ timeout: 30_000 });

    const downloadPromise = page.waitForEvent("download");
    await exportButton.click();
    const download = await downloadPromise;

    expect(download.suggestedFilename()).toMatch(/^갭리포트_.+_\d{8}\.xlsx$/);
  });
});
