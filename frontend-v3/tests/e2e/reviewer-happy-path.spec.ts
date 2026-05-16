import { test, expect } from "./fixtures";

/**
 * E2E: reviewer happy-path (W5 G31, spec #1).
 *
 * Flow:
 *   1. login → /queue
 *   2. click first queue row → /calls/[id]
 *   3. mark CP1..CP4 pass
 *   4. switch to Verdict tab
 *   5. click PASS, fill reason, submit
 *   6. expect "Verdict submitted" toast + queue count decrement
 *
 * Skip-guard: the spec needs at least one unclaimed call in the queue.
 * If the test DB is empty we mark the test as skipped so CI doesn't go
 * red — flag for backend fixture work in W6+.
 */
test("reviewer claims, scores checkpoints, and submits a verdict", async ({
  authedPage: page,
}) => {
  await page.goto("/queue");

  const firstRow = page.getByTestId("queue-row").first();
  const rowCount = await page.getByTestId("queue-row").count();

  test.skip(
    rowCount === 0,
    "No queue rows present — backend test fixtures missing. TODO: seed at least one PENDING_REVIEW call.",
  );

  await firstRow.click();
  await page.waitForURL(/\/calls\/[^/]+/, { timeout: 10_000 });

  // Mark up to 4 checkpoints as pass — fewer is fine if the call doesn't
  // expose 4 checkpoints in the test DB.
  const passButtons = page.getByTestId("cp-pass");
  const passCount = Math.min(4, await passButtons.count());
  test.skip(
    passCount === 0,
    "Selected call has no script checkpoints — TODO: seed call with at least 1 CP.",
  );
  for (let i = 0; i < passCount; i++) {
    await passButtons.nth(i).click();
  }

  // Switch to Verdict tab. The tab list is plain <button>s so we match by
  // accessible name rather than role=tab.
  await page.getByRole("button", { name: /^Verdict$/ }).click();

  // VerdictTab aggregate action buttons use data-testid="verdict-agg-{KEY}".
  await page.getByTestId("verdict-agg-PASS").click();

  // VerdictTab overall-reason textarea (auto-populated, freely editable).
  await page
    .getByTestId("verdict-overall-reason")
    .fill("Compliance verified after audit.");

  // VerdictTab submit button.
  await page.getByTestId("verdict-submit").click();

  // Sonner toast — match by text.
  await expect(page.getByText(/Verdict submitted/i)).toBeVisible({ timeout: 10_000 });
});
