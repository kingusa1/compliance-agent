import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for production smoke tests against the live Vercel deploy.
 * Does NOT start a local dev server — tests run against BASE_URL directly.
 *
 * Usage:
 *   npx playwright test --config=playwright.prod-smoke.config.ts
 */
export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: ["prod-smoke-2026-05-16.spec.ts"],
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: false,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report-prod" }]],
  outputDir: "test-results",
  use: {
    baseURL: process.env.BASE_URL ?? "https://compliance-agent-mu.vercel.app",
    trace: "on-first-retry",
    screenshot: "on",
    video: "retain-on-failure",
  },
  // No webServer — we hit the live URL directly
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
