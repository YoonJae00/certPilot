import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright 설정.
 *
 * 실제 실행에는 백엔드(http://localhost:8000)와 시드 계정이 필요하다.
 * 이번 태스크에서는 설정과 시나리오만 작성하고 실행은 이후 태스크에서 한다.
 * 브라우저 바이너리는 `npx playwright install` 로 따로 내려받아야 한다.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    locale: "ko-KR",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // 로컬에서는 개발 서버를 자동으로 띄운다(이미 떠 있으면 재사용).
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
