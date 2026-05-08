import { test, expect } from "@playwright/test";
import path from "node:path";

test("R8c call-detail re-capture (longer wait)", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/login");
  await page.fill('input[type="email"]', "test@fame.dev");
  await page.fill('input[type="password"]', "test");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/(queue|calls|customers|deals)/, { timeout: 15_000 });

  await page.goto("/calls/8b413400-49be-4004-be52-666b6a4c9aa8", {
    waitUntil: "domcontentloaded",
  });
  // Wait for transcript or "Verdict" tab to land.
  await page.waitForSelector("text=/Verdict|Transcript|Checkpoints/i", {
    timeout: 30_000,
  }).catch(() => {});
  await page.waitForTimeout(3000);
  await page.screenshot({
    path: path.resolve(
      __dirname,
      "../../../.planning/v3-rebuild/screenshots/R8c-04-call-detail.png",
    ),
    fullPage: false,
  });
  expect(true).toBe(true);
});
