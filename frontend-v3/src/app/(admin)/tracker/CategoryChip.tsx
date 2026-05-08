/**
 * 8-category chip — Watt's 5-color hex palette per XLSX deep-dive §2.8.
 *
 * Source XLSX cell-fill colors (taken verbatim from real Watt rows):
 *   ADMIN_ERROR         → FF0000 red
 *   PROCESS_FAILURE     → FFC000 amber
 *   VERBAL_SALES_ERROR  → 00B0F0 cyan
 *   COMPLIANCE_ISSUE    → FFFF00 yellow
 *   COMPLIANCE_ERROR    → 92D050 green
 *   PRICING_ISSUE       → FF8080 (lighter red — extension)
 *   DOCUSIGN_ERROR      → BDD7EE (lighter cyan — extension)
 *   FAILED_CREDIT_CHECK → FFD966 (lighter amber — extension)
 */

const CATEGORY_HEX: Record<string, string> = {
  ADMIN_ERROR: "#FF0000",
  PROCESS_FAILURE: "#FFC000",
  VERBAL_SALES_ERROR: "#00B0F0",
  COMPLIANCE_ISSUE: "#FFFF00",
  COMPLIANCE_ERROR: "#92D050",
  PRICING_ISSUE: "#FF8080",
  PRICING_ERROR: "#C00000",
  DOCUSIGN_ERROR: "#BDD7EE",
  FAILED_CREDIT_CHECK: "#FFD966",
};

const CATEGORY_LABEL: Record<string, string> = {
  ADMIN_ERROR: "Admin error",
  PROCESS_FAILURE: "Process failure",
  VERBAL_SALES_ERROR: "Verbal sales err",
  COMPLIANCE_ISSUE: "Compliance issue",
  COMPLIANCE_ERROR: "Compliance error",
  PRICING_ISSUE: "Pricing issue",
  PRICING_ERROR: "Pricing error",
  DOCUSIGN_ERROR: "DocuSign error",
  FAILED_CREDIT_CHECK: "Failed credit check",
};

export function CategoryChip({ category }: { category: string | null }) {
  if (!category) return <span className="text-[var(--text-muted)]">—</span>;
  const hex = CATEGORY_HEX[category] ?? "#9ca3af";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ backgroundColor: hex + "26", color: "#f4f4f5", border: `1px solid ${hex}` }}
    >
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ backgroundColor: hex }}
      />
      {CATEGORY_LABEL[category] ?? category}
    </span>
  );
}

export const CATEGORY_KEYS = Object.keys(CATEGORY_HEX);
export { CATEGORY_HEX, CATEGORY_LABEL };
