import { test as base, expect, type Page } from "@playwright/test";

/**
 * Shared E2E fixtures (W5).
 *
 * `authedPage` logs in via the regular Supabase form once per worker —
 * faster + more reliable than juggling tokens, and exercises the real
 * /login page along the way. Credentials come from the env so the suite
 * stays usable across local + CI; they default to test@fame.dev / test
 * (created by `backend/seed/test-users.sql`).
 */
const E2E_EMAIL = process.env.E2E_EMAIL ?? "test@fame.dev";
const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "test";

type Fixtures = {
  authedPage: Page;
};

export const test = base.extend<Fixtures>({
  authedPage: async ({ page }, use) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', E2E_EMAIL);
    await page.fill('input[type="password"]', E2E_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    // Successful auth lands on /queue (reviewer/lead) or /calls (admin/user).
    await page.waitForURL(/\/(queue|calls|customers|deals)/, { timeout: 15_000 });
    await use(page);
  },
});

export { expect };
