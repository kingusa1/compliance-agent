"use client";

/**
 * Watt's exact 5-color hand-typed category chip — ported from
 * design/handoff-bundle/project/hifi/rejections-hifi.jsx (REJ_CATS +
 * CategoryChip + CategoryBadgeLarge).
 *
 * Two sizes:
 *   - `sm` (table cell): muted background w/ glowing dot.
 *   - `lg` (detail panel header): solid hex background w/ ink color.
 */
import {
  REJECTION_CATEGORY_COLORS,
  REJECTION_CATEGORY_INK,
  REJECTION_CATEGORY_LABELS,
  type RejectionCategory,
} from "@/lib/schemas/rejections";

export function CategoryChip({
  category,
  size = "sm",
}: {
  category: RejectionCategory | string;
  size?: "sm" | "lg";
}) {
  const cat = category as RejectionCategory;
  const hex = REJECTION_CATEGORY_COLORS[cat];
  const label = REJECTION_CATEGORY_LABELS[cat];
  if (!hex || !label) {
    return (
      <span
        style={{
          fontSize: 11,
          color: "var(--text-dim)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {String(category)}
      </span>
    );
  }
  const dims =
    size === "lg"
      ? { h: 28, px: 12, fs: 12, dot: 8 }
      : { h: 22, px: 8, fs: 11, dot: 7 };
  return (
    <span
      data-slot="category-chip"
      data-category={cat}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        height: dims.h,
        padding: `0 ${dims.px}px`,
        fontSize: dims.fs,
        fontWeight: 600,
        letterSpacing: "0.02em",
        borderRadius: 4,
        background: `${hex}14`,
        border: `1px solid ${hex}66`,
        color: hex,
        whiteSpace: "nowrap",
        fontFamily: "var(--font-sans)",
      }}
    >
      <span
        style={{
          width: dims.dot,
          height: dims.dot,
          borderRadius: 2,
          background: hex,
          boxShadow: `0 0 6px ${hex}88`,
          flexShrink: 0,
        }}
      />
      {label}
    </span>
  );
}

export function CategoryBadgeLarge({
  category,
}: {
  category: RejectionCategory | string;
}) {
  const cat = category as RejectionCategory;
  const hex = REJECTION_CATEGORY_COLORS[cat];
  const ink = REJECTION_CATEGORY_INK[cat];
  const label = REJECTION_CATEGORY_LABELS[cat];
  if (!hex || !label) return null;
  return (
    <div
      data-slot="category-badge-large"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 14px",
        background: hex,
        color: ink,
        borderRadius: 6,
        fontSize: 12,
        fontWeight: 700,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        fontFamily: "var(--font-sans)",
        boxShadow: `0 0 0 1px rgba(0,0,0,0.25), 0 4px 12px ${hex}55`,
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: 2,
          background: ink,
          opacity: 0.7,
        }}
      />
      {label}
    </div>
  );
}
