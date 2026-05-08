"use client";

/**
 * DeadReasonChip — W4.6 (v3-watt-coverage).
 *
 * Two render modes, one component:
 *
 *   <DeadReasonChip reason="in_contract" />
 *     Row-level read-only chip — small label + tooltip-via-`title`,
 *     used inside RejectionsTable on the Dead tab to show why a row
 *     was killed.
 *
 *   <DeadReasonChip reason="in_contract" mode="filter" active onClick={...} />
 *     Clickable filter chip used in the Dead-tab filter bar.
 *     Multi-select handled by the parent (toggles state per click).
 *
 * The 5-key vocabulary lives in `@/lib/schemas/rejections` (DEAD_REASONS)
 * and is mirrored in the backend by ``rejections_routes.DEAD_REASONS``.
 * The gloss text is fetched live via `useDeadReasonsQuery()` so a backend
 * vocab tweak doesn't need a frontend deploy.
 */
import { useMemo } from "react";

import { useDeadReasonsQuery } from "@/lib/queries/rejections";
import {
  DEAD_REASON_LABELS,
  type DeadReason,
} from "@/lib/schemas/rejections";

const ROW_BASE: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  height: 22,
  padding: "0 8px",
  fontSize: 11,
  fontWeight: 500,
  borderRadius: 5,
  border: "1px solid var(--border-subtle)",
  background: "var(--bg-elev2)",
  color: "var(--text-muted)",
  letterSpacing: "-0.003em",
  whiteSpace: "nowrap",
};

const FILTER_BASE: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  height: 26,
  padding: "0 10px",
  fontSize: 11.5,
  fontWeight: 500,
  borderRadius: 6,
  background: "var(--bg-elev2)",
  color: "var(--text-muted)",
  border: "1px solid var(--border-subtle)",
  cursor: "pointer",
  letterSpacing: "-0.003em",
  whiteSpace: "nowrap",
};

const ACTIVE_RED: React.CSSProperties = {
  background: "rgba(220, 38, 38, 0.13)",
  color: "var(--red-400, #f87171)",
  borderColor: "rgba(220, 38, 38, 0.55)",
};

function _label(reason: string | null | undefined): string {
  if (!reason) return "—";
  if (reason in DEAD_REASON_LABELS)
    return DEAD_REASON_LABELS[reason as DeadReason];
  // Backend may add new buckets; fall back to humanised key.
  return reason
    .split("_")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" ");
}

export type DeadReasonChipProps = {
  reason: DeadReason | string | null | undefined;
  mode?: "row" | "filter";
  /** Filter mode only — render visually as selected. */
  active?: boolean;
  /** Filter mode only — fired on click. */
  onClick?: () => void;
};

export function DeadReasonChip({
  reason,
  mode = "row",
  active = false,
  onClick,
}: DeadReasonChipProps) {
  // Pull glosses for tooltips. Cache is 24h so this resolves once per session.
  const glosses = useDeadReasonsQuery();
  const tooltip = useMemo(() => {
    if (!reason) return undefined;
    const found = glosses.data?.dead_reasons.find((d) => d.key === reason);
    return found?.gloss;
  }, [glosses.data, reason]);

  const label = _label(reason);

  if (mode === "filter") {
    return (
      <button
        type="button"
        onClick={onClick}
        title={tooltip}
        data-slot="dead-reason-filter-chip"
        data-reason={reason ?? ""}
        data-active={active ? "1" : "0"}
        style={{
          ...FILTER_BASE,
          ...(active ? ACTIVE_RED : {}),
        }}
      >
        <span
          aria-hidden
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: active ? "currentColor" : "var(--text-dim)",
          }}
        />
        {label}
      </button>
    );
  }

  // Row mode — read-only.
  if (!reason) {
    return (
      <span
        data-slot="dead-reason-chip"
        data-reason=""
        style={{
          ...ROW_BASE,
          color: "var(--text-dim)",
          fontFamily: "var(--font-mono)",
          fontSize: 10.5,
        }}
      >
        no reason
      </span>
    );
  }

  return (
    <span
      data-slot="dead-reason-chip"
      data-reason={reason}
      title={tooltip}
      style={ROW_BASE}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: 999,
          background: "var(--red-400, #f87171)",
        }}
      />
      {label}
    </span>
  );
}
