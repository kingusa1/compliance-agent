import { defineConfig, devices } from "@playwright/test";

/**
 * Production-only Playwright config.
 * Runs against the live Vercel deployment — no local dev server needed.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 180_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: false,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "https://compliance-agent-mu.vercel.app",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  // No webServer — tests hit live Vercel production directly.
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
