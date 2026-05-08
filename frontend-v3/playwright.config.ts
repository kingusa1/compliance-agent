import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright base config for frontend-v3 e2e tests.
 *
 * - Reads BASE_URL from env (defaults to :3005, matching `npm run dev`).
 * - `webServer.reuseExistingServer` lets a hand-started dev server be
 *   reused locally; CI boots its own.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3005",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev",
    port: 3005,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
