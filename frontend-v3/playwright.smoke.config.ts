import { defineConfig, devices } from "@playwright/test";

/**
 * Smoke-only Playwright config — assumes the dev server is already running
 * (so we don't fight port 3000 vs 3005). Point BASE_URL at whatever's up.
 *
 *   BASE_URL=http://localhost:3000 npx playwright test \
 *     --config playwright.smoke.config.ts tests/e2e/login.spec.ts
 *
 * No `webServer` block — that's the whole point.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
