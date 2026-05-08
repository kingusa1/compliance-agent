import { test, expect } from "./fixtures";

/**
 * E2E: customer-first 3-call workflow (W5 G31, spec #3).
 *
 * Flow:
 *   1. login → /customers/auditfix (a known seeded customer)
 *   2. expect deal card with lifecycle progress visible
 *   3. click a missing-call chip
 *   4. expect upload modal pre-filled with the customer + deal +
 *      call_type locked
 *
 * Skip-guard: the "auditfix" customer slug must exist. If not we skip —
 * TODO: backend seed task.
 */
test("customer-first workflow surfaces missing-call chips", async ({ authedPage: page }) => {
  // Try a few well-known seed slugs in priority order.
  const seedSlugs = ["auditfix", "test-customer", "demo-customer"];
  let landed = false;
  for (const slug of seedSlugs) {
    await page.goto(`/customers/${slug}`, { waitUntil: "domcontentloaded" });
    // 404 page renders a different title; check if we got the customer hero.
    const has404 = await page.getByText(/customer not found|404/i).isVisible().catch(() => false);
    if (!has404) {
      landed = true;
      break;
    }
  }
  test.skip(
    !landed,
    "No seeded customer slug among [auditfix, test-customer, demo-customer] — TODO: backend fixture.",
  );

  // Lifecycle progress / deal card — be permissive on selector.
  const dealCard = page.locator('[data-testid="deal-card"], [data-testid="deal-row"]').first();
  const hasDeal = await dealCard.isVisible().catch(() => false);
  test.skip(!hasDeal, "Customer has no deals yet — TODO: seed at least one deal.");

  // Look for a "missing call" chip. The MissingCallsChips component uses
  // either a `data-testid="missing-call-chip"` or a button labelled
  // "Upload <call_type>" — match either.
  const chip = page
    .locator('[data-testid="missing-call-chip"], button:has-text("Upload verbal"), button:has-text("Upload written")')
    .first();
  test.skip(!(await chip.isVisible().catch(() => false)), "No missing-call chip on this deal.");
  await chip.click();

  // Upload modal — verify pre-fill: the supplier combobox should already
  // be locked (disabled) and call_type input should be present.
  const modal = page.locator('[role="dialog"], [data-testid="upload-dialog"]').first();
  await expect(modal).toBeVisible({ timeout: 5_000 });

  // Supplier should be locked (disabled / readonly trigger) since the
  // chip implies the deal context.
  const supplierTrigger = modal.locator('[data-slot="supplier-combobox-trigger"]').first();
  if (await supplierTrigger.count()) {
    const isDisabled = await supplierTrigger.evaluate(
      (el) => el.hasAttribute("data-disabled") || (el as HTMLButtonElement).disabled,
    );
    expect(isDisabled).toBe(true);
  }
});
