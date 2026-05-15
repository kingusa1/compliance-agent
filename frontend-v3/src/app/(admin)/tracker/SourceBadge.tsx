"use client";

import type { TrackerFieldSource } from "@/lib/queries/tracker";

const STYLES: Record<TrackerFieldSource, { label: string; bg: string; fg: string }> = {
  human: { label: "✓ Human", bg: "rgba(16,185,129,0.15)", fg: "#34d399" },
  // 2026-05-15: deal-level edits via the tracker side panel stamp this
  // value on customer_deals.field_sources. Renders the same emerald
  // "✓ Edited" chip so reviewers see provenance is human-driven.
  reviewer_edit: { label: "✓ Edited", bg: "rgba(16,185,129,0.15)", fg: "#34d399" },
  xlsx_import: { label: "XLSX", bg: "rgba(96,165,250,0.15)", fg: "#60a5fa" },
  integration: { label: "API", bg: "rgba(167,139,250,0.15)", fg: "#a78bfa" },
  ai: { label: "AI", bg: "rgba(245,158,11,0.15)", fg: "#fbbf24" },
  placeholder: { label: "—", bg: "transparent", fg: "var(--text-dim)" },
};

export function SourceBadge({
  source,
  previousValue,
}: {
  source: TrackerFieldSource;
  previousValue?: string | null;
}) {
  // Plan §5c: drop "AI" labels in tracker cells — every value is AI-derived
  // unless a reviewer has confirmed it, so the badge is redundant. We keep
  // the "Human / XLSX / API" sources visible so reviewers still see what
  // they've signed off on; AI + placeholder render nothing.
  if (source === "ai" || source === "placeholder") return null;
  // Defensive: if the backend ever emits a new source value the frontend
  // doesn't know about yet, render nothing rather than crashing the whole
  // tracker page with "Cannot read properties of undefined (reading 'bg')".
  // That's exactly how the page-couldn't-load incident on 2026-05-15 fired
  // when reviewer_edit was stamped without being added to STYLES.
  const s = STYLES[source];
  if (!s) return null;
  return (
    <span
      title={previousValue ? `Previously ${source}: ${previousValue}` : `Source: ${source}`}
      style={{
        fontSize: 9,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        padding: "1px 5px",
        borderRadius: 3,
        background: s.bg,
        color: s.fg,
        marginLeft: 6,
        whiteSpace: "nowrap",
      }}
    >
      {s.label}
    </span>
  );
}
