"use client";

/**
 * RubricBadge — surfaces "what rubric is this graded against?" for a
 * segment or per-checkpoint verdict.
 *
 *   phrase_pack_lead_gen   → 88-rule Lead Gen phrase pack    (emerald)
 *   phrase_pack_pre_sales  → 88-rule Pre-Sales phrase pack   (blue)
 *   supplier_script_verbal → Verbal contract script · …      (amber)
 *   supplier_script_loa    → LOA script · …                  (violet)
 *   v1_fallback            → V1 third-party-disclosure fallback (neutral)
 *
 * The label is server-computed (see app/routes.py:_resolve_segment_rubric);
 * we only do the visual treatment here.
 */
export type RubricKind =
  | "phrase_pack_lead_gen"
  | "phrase_pack_pre_sales"
  | "supplier_script_verbal"
  | "supplier_script_loa"
  | "v1_fallback"
  | string;

const RUBRIC_VISUAL: Record<
  string,
  { icon: string; fg: string; bg: string; border: string }
> = {
  phrase_pack_lead_gen: {
    icon: "📋",
    fg: "var(--emerald-400)",
    bg: "var(--emerald-bg)",
    border: "var(--emerald-border)",
  },
  phrase_pack_pre_sales: {
    icon: "📋",
    fg: "var(--blue)",
    bg: "var(--blue-bg)",
    border: "var(--blue-border)",
  },
  supplier_script_verbal: {
    icon: "📜",
    fg: "var(--amber-400)",
    bg: "var(--amber-bg)",
    border: "var(--amber-border)",
  },
  supplier_script_loa: {
    icon: "📜",
    fg: "var(--violet)",
    bg: "var(--violet-bg)",
    border: "var(--violet-border)",
  },
  v1_fallback: {
    icon: "🛟",
    fg: "var(--text-muted)",
    bg: "var(--bg-elev3)",
    border: "var(--border-subtle)",
  },
};

export function RubricBadge({
  kind,
  label,
  compact = false,
}: {
  kind: RubricKind | null | undefined;
  label: string | null | undefined;
  compact?: boolean;
}) {
  if (!label) return null;
  const v = RUBRIC_VISUAL[kind ?? ""] ?? RUBRIC_VISUAL.v1_fallback;
  return (
    <span
      data-testid="rubric-badge"
      data-kind={kind ?? "unknown"}
      title={label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: compact ? 9.5 : 10.5,
        fontWeight: 600,
        letterSpacing: "0.02em",
        padding: compact ? "2px 6px" : "3px 8px",
        borderRadius: 999,
        background: v.bg,
        color: v.fg,
        border: `1px solid ${v.border}`,
        whiteSpace: "nowrap",
        maxWidth: compact ? 180 : 260,
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}
    >
      <span aria-hidden style={{ fontSize: compact ? 10 : 11 }}>{v.icon}</span>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
        {label}
      </span>
    </span>
  );
}
