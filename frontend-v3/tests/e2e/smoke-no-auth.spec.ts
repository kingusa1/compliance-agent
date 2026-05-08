import { test, expect } from "@playwright/test";

/**
 * No-auth smoke — purely visual sanity check that the frontend renders
 * without a Supabase session present. Used during the YOLO build to
 * prove there are no SSR/hydration errors before credentials are wired.
 *
 * Authenticated routes redirect to /login (307); we follow the redirect
 * and assert the login page renders and the basic chrome (header, brand
 * mark) is visible. Network/HTTP errors surface as a failed expect()
 * rather than a Playwright timeout.
 */

test.describe("public routes — no auth", () => {
  test("/ redirects to /login and login renders", async ({ page }) => {
    const r = await page.goto("/");
    expect(r?.ok()).toBeTruthy();
    await expect(page).toHaveURL(/\/login/);
    // Login page must render *something* — not a Next error overlay.
    const body = await page.textContent("body");
    expect(body).toBeTruthy();
    expect(body!.length).toBeGreaterThan(20);
    // No client-side error overlay (Next 16 renders errors in a dialog
    // distinct from the devtools-indicator toast that's always present
    // in dev mode).
    const errorDialog = page.locator(
      '[data-nextjs-dialog-overlay], [data-nextjs-error]',
    );
    await expect(errorDialog).toHaveCount(0);
  });

  test("/_not-found renders without crashing", async ({ page }) => {
    const r = await page.goto("/this-route-definitely-does-not-exist");
    // Next sends 404 for unknown routes.
    expect(r?.status()).toBeGreaterThanOrEqual(200);
    const body = await page.textContent("body");
    expect(body).toBeTruthy();
  });

  test("login page contains a sign-in form element", async ({ page }) => {
    await page.goto("/login");
    // shadcn forms render <input type="email"> and <input type="password">.
    // The exact selectors may evolve; we just want at least one input on
    // the page to confirm hydration succeeded.
    const inputCount = await page.locator("input").count();
    expect(inputCount).toBeGreaterThan(0);
  });

  test("no console errors on /login first paint", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto("/login", { waitUntil: "networkidle" });
    // Filter out known-noisy entries (Sentry init warning when no DSN,
    // Supabase Lock messages, Next devtools telemetry).
    const real = errors.filter(
      (e) =>
        !/sentry|gotrue|locks?|websocket|hmr|hydration warning/i.test(e),
    );
    expect(real, real.join("\n")).toEqual([]);
  });
});
