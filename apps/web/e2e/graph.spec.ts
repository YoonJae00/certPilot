import { expect, test, type Page } from "@playwright/test";

import { RING_RADIUS } from "@/app/(dashboard)/projects/[id]/graph-elements";

import { loginAsAdmin, openDemoProject } from "./helpers";

/**
 * 지식 그래프 탭 e2e 시나리오.
 *
 * 실행 전제:
 *  - 백엔드가 http://localhost:8000 에서 동작하고 데모 시드(`make demo`)가 적재돼 있다.
 *  - `GET /projects/{id}/graph` 가 배포돼 있다(미배포면 탭이 "준비 중" 안내만 보여 준다).
 *  - 시나리오는 시드 프로젝트를 그대로 쓴다(임시 프로젝트를 만들지 않는다).
 *
 * 그래프는 방사형(항목 101개가 하나의 링) 배치라 노드에 DOM 이 없다. Cytoscape 는
 * 컨테이너 안에 canvas 를 여러 장 겹쳐 그리므로 가시성은 첫 장으로 보고, 노드 클릭은
 * 캔버스 좌표를 훑어서 맞힌다(아래 `focusCriterion` 참고).
 */

/** 그래프·판정 목록을 받아 오는 데 주는 여유 시간. */
const LOAD_TIMEOUT_MS = 30_000;

/** 지식 그래프 탭을 연다. */
async function openGraphTab(page: Page): Promise<void> {
  await loginAsAdmin(page);
  await openDemoProject(page);
  await page.getByRole("tab", { name: "지식 그래프" }).click();
  await expect(page.getByText("항목 101")).toBeVisible({ timeout: LOAD_TIMEOUT_MS });
  await page.locator("canvas").first().scrollIntoViewIfNeeded();
}

/**
 * 링 위의 인증기준 노드 하나를 눌러 포커스한다.
 *
 * 노드는 canvas 안에 그려져 DOM 이 없다. 대신 같은 좌표계를 쓰는 구조 언더레이(SVG)가
 * DOM 이라는 점을 이용한다. 루트 `<g>` 의 `translate(pan) scale(zoom)` 에서 모델 원점과
 * 배율을 읽고, "2장 보호대책" 라벨이 가리키는 방향으로 링 반지름만큼 나가면 항목이 있다.
 * 항목 간격이 10px 안팎이라 접선 방향으로 조금씩 옮겨 가며 눌러 한 개를 맞힌다.
 */
async function focusCriterion(page: Page): Promise<boolean> {
  const underlay = page.locator("svg").filter({ hasText: "2장 보호대책" }).first();
  const transform =
    (await underlay.locator("g[transform]").first().getAttribute("transform")) ?? "";
  const parsed = /translate\(([-\d.]+),\s*([-\d.]+)\)\s*scale\(([-\d.]+)\)/.exec(
    transform,
  );
  const underlayBox = await underlay.boundingBox();
  if (!parsed || !underlayBox) return false;

  // 모델 원점(0,0) 은 언더레이 좌상단에서 pan 만큼 떨어진 곳이다.
  const centerX = underlayBox.x + Number(parsed[1]);
  const centerY = underlayBox.y + Number(parsed[2]);
  const zoom = Number(parsed[3]);

  const labelBox = await underlay
    .locator("text")
    .filter({ hasText: "2장 보호대책" })
    .first()
    .boundingBox();
  if (!labelBox) return false;

  const dx = labelBox.x + labelBox.width / 2 - centerX;
  const dy = labelBox.y + labelBox.height / 2 - centerY;
  const length = Math.hypot(dx, dy);
  if (length < 1) return false;
  const unitX = dx / length;
  const unitY = dy / length;
  const ringRadius = RING_RADIUS * zoom;

  const detailButton = page.getByRole("button", { name: "판정 상세 보기" });
  // 접선 방향(수직 벡터)으로 훑는다. 절 사이 갭에 빠져도 빠져나올 만큼 범위를 잡는다.
  for (let step = 0; step <= 24; step += 3) {
    for (const sign of step === 0 ? [1] : [1, -1]) {
      const offset = step * sign;
      await page.mouse.click(
        centerX + unitX * ringRadius - unitY * offset,
        centerY + unitY * ringRadius + unitX * offset,
      );
      await page.waitForTimeout(60);
      if (await detailButton.isVisible().catch(() => false)) return true;
    }
  }
  return false;
}

test.describe("지식 그래프", () => {
  test("탭에 들어가면 범례·통계와 그래프 캔버스가 보인다", async ({ page }) => {
    await openGraphTab(page);

    // 통계 헤더: 인증기준은 101개 고정이다(PRD §6 — 장별 16/64/21).
    await expect(page.getByText(/문서 \d+ · 증적 \d+ · 연결 \d+건/)).toBeVisible();

    // 범례(노드 모양 + 엣지 2종)와 판정 분포.
    await expect(page.getByText("범례", { exact: true })).toBeVisible();
    await expect(page.getByText("문서 근거 인용")).toBeVisible();
    await expect(page.getByText("증적 근거 인용")).toBeVisible();
    await expect(page.getByText(/충족 \d+/).first()).toBeVisible();

    // Cytoscape 캔버스와 그 아래 구조 언더레이(장 라벨).
    await expect(page.locator("canvas").first()).toBeVisible();
    await expect(page.locator("svg text", { hasText: "2장 보호대책" })).toBeVisible();
  });

  test("장 필터를 2장으로 바꾸면 통계의 항목 수가 줄어든다", async ({ page }) => {
    await openGraphTab(page);

    await page.getByRole("combobox").first().click();
    await page.getByRole("option", { name: "2장 보호대책" }).click();

    // 2장은 64개 항목이다.
    await expect(page.getByText("항목 64")).toBeVisible();
  });

  test("판정 상태 토글은 눌린 상태(aria-pressed)를 유지한다", async ({ page }) => {
    await openGraphTab(page);

    const unmetButton = page.getByRole("button", { name: "미충족", exact: true });
    await expect(unmetButton).toHaveAttribute("aria-pressed", "false");

    await unmetButton.click();
    await expect(unmetButton).toHaveAttribute("aria-pressed", "true");

    await unmetButton.click();
    await expect(unmetButton).toHaveAttribute("aria-pressed", "false");
  });

  test("보기 초기화를 눌러도 캔버스가 그대로 살아 있다", async ({ page }) => {
    await openGraphTab(page);

    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();

    await page.getByRole("button", { name: "보기 초기화" }).click();
    await expect(canvas).toBeVisible();
    await expect(page.getByText("항목 101")).toBeVisible();
  });

  test("항목 노드를 누르면 근거를 담은 정보 카드가 열린다", async ({ page }) => {
    await openGraphTab(page);
    // 판정 목록까지 받아야 "판정 상세 보기" 버튼이 살아난다.
    await page.waitForTimeout(1_500);

    expect(
      await focusCriterion(page),
      "링 위에서 인증기준 노드를 찾지 못했다",
    ).toBeTruthy();

    // 카드에는 항목 코드 뱃지와 인용 목록, 상세 시트로 가는 버튼이 있다.
    await expect(page.getByRole("button", { name: "정보 카드 닫기" })).toBeVisible();
    await expect(page.getByText("인용 문서", { exact: false }).first()).toBeVisible();

    await page.getByRole("button", { name: "판정 상세 보기" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });
});
