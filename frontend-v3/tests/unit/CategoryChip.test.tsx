import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { CategoryChip, CategoryBadgeLarge } from "@/app/(admin)/rejections/CategoryChip";
import {
  REJECTION_CATEGORIES,
  REJECTION_CATEGORY_COLORS,
  REJECTION_CATEGORY_LABELS,
} from "@/lib/schemas/rejections";

/**
 * CategoryChip — Watt's hand-typed 5-color category palette is load-bearing.
 * If any of these constants drift, the /rejections page silently mis-codes
 * the category and Watt's reviewers stop trusting the chip.
 */
describe("CategoryChip", () => {
  it("ships exactly 9 categories with W2-locked hex colors", () => {
    expect(REJECTION_CATEGORIES).toHaveLength(9);
    expect(REJECTION_CATEGORY_COLORS.ADMIN_ERROR).toBe("#FFC000");
    expect(REJECTION_CATEGORY_COLORS.PROCESS_FAILURE).toBe("#00B0F0");
    expect(REJECTION_CATEGORY_COLORS.VERBAL_SALES_ERROR).toBe("#FF0000");
    expect(REJECTION_CATEGORY_COLORS.COMPLIANCE_ISSUE).toBe("#FFFF00");
    expect(REJECTION_CATEGORY_COLORS.PRICING_ISSUE).toBe("#92D050");
  });

  it("renders the small chip with category data attribute + label", () => {
    const { getByText, container } = render(
      <CategoryChip category="ADMIN_ERROR" />,
    );
    expect(getByText(REJECTION_CATEGORY_LABELS.ADMIN_ERROR)).toBeTruthy();
    const chip = container.querySelector("[data-slot=category-chip]");
    expect(chip).not.toBeNull();
    expect(chip?.getAttribute("data-category")).toBe("ADMIN_ERROR");
  });

  it("renders the large badge for the detail panel header", () => {
    const { container } = render(<CategoryBadgeLarge category="VERBAL_SALES_ERROR" />);
    const badge = container.querySelector("[data-slot=category-badge-large]");
    expect(badge).not.toBeNull();
    expect((badge as HTMLElement).style.background).toBe("rgb(255, 0, 0)");
  });

  it("falls back to a mono label when given an unknown category", () => {
    const { getByText } = render(<CategoryChip category="UNKNOWN_VALUE" />);
    expect(getByText("UNKNOWN_VALUE")).toBeTruthy();
  });

  it("uses the same hex for paired categories (W2 grouping)", () => {
    // The XLSX deep-dive groups COMPLIANCE_ISSUE + COMPLIANCE_ERROR under
    // the same yellow fill, ADMIN_ERROR + DOCUSIGN_ERROR under the same
    // amber, PROCESS_FAILURE + FAILED_CREDIT_CHECK under blue. These pairs
    // are intentional — drift would falsely separate them visually.
    expect(REJECTION_CATEGORY_COLORS.COMPLIANCE_ISSUE).toBe(
      REJECTION_CATEGORY_COLORS.COMPLIANCE_ERROR,
    );
    expect(REJECTION_CATEGORY_COLORS.ADMIN_ERROR).toBe(
      REJECTION_CATEGORY_COLORS.DOCUSIGN_ERROR,
    );
    expect(REJECTION_CATEGORY_COLORS.PROCESS_FAILURE).toBe(
      REJECTION_CATEGORY_COLORS.FAILED_CREDIT_CHECK,
    );
  });
});
