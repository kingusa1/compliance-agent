"use client";

/**
 * BulkActionToolbar — floating bottom-anchored action bar for the
 * checkpoint multi-select flow.
 *
 *   - Visible only when `selectedCount > 0`.
 *   - Buttons: Bulk Pass · Bulk Override → Fail · Mark N/A · Clear.
 *   - All actions go through `useBulkVerdictMutation` from the caller —
 *     this component is pure presentation + click forwarders.
 *
 * Wave-25 (2026-05-27) — Charlotte's client feedback: "we're wondering if
 * there's a way to quickly pass something without going through each
 * checkpoint individually." Owner direction:
 *   - Bulk Pass works on ANY card (max flexibility — no AI-state guard).
 *   - Bulk Override → Fail is a SEPARATE button, slightly more friction.
 *   - Mark N/A added for conditional CPs that didn't fire.
 *
 * Anchoring: position: sticky with bottom: 0 in the existing
 * scroll container so the bar floats above the cards without
 * needing a portal. Mirror of Gmail's bulk-action chip per pre-wave
 * research (agent a10ae26df28b8663b).
 */
import { CheckCircle2, XCircle, MinusCircle, X } from "lucide-react";

export interface BulkActionToolbarProps {
  selectedCount: number;
  busy?: boolean;
  onBulkPass: () => void;
  onBulkFail: () => void;
  onBulkNA: () => void;
  onClear: () => void;
}

export function BulkActionToolbar({
  selectedCount,
  busy = false,
  onBulkPass,
  onBulkFail,
  onBulkNA,
  onClear,
}: BulkActionToolbarProps) {
  if (selectedCount === 0) return null;

  return (
    <div
      data-slot="bulk-action-toolbar"
      role="toolbar"
      aria-label={`${selectedCount} checkpoints selected — bulk actions`}
      style={{
        position: "sticky",
        bottom: 12,
        zIndex: 30,
        margin: "12px auto 4px",
        maxWidth: 720,
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "10px 14px",
        background: "var(--bg-elev3)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 10,
        boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
        flexWrap: "wrap",
      }}
    >
      <span
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: "var(--text-primary)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {selectedCount} selected
      </span>
      <span style={{ flex: 1 }} />
      <ToolbarButton
        label="Bulk Pass"
        color="#22c55e"
        Icon={CheckCircle2}
        onClick={onBulkPass}
        busy={busy}
      />
      <ToolbarButton
        label="Override → Fail"
        color="#ef4444"
        Icon={XCircle}
        onClick={onBulkFail}
        busy={busy}
      />
      <ToolbarButton
        label="Mark N/A"
        color="#94a3b8"
        Icon={MinusCircle}
        onClick={onBulkNA}
        busy={busy}
      />
      <button
        type="button"
        onClick={onClear}
        disabled={busy}
        aria-label="Clear selection"
        title="Clear selection (Esc)"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          height: 28,
          padding: "0 8px",
          fontSize: 11,
          background: "transparent",
          color: "var(--text-muted)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 6,
          cursor: busy ? "default" : "pointer",
          opacity: busy ? 0.6 : 1,
        }}
      >
        <X size={12} /> Clear
      </button>
    </div>
  );
}

interface ToolbarButtonProps {
  label: string;
  color: string;
  Icon: typeof CheckCircle2;
  onClick: () => void;
  busy: boolean;
}

function ToolbarButton({ label, color, Icon, onClick, busy }: ToolbarButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        height: 30,
        padding: "0 12px",
        fontSize: 12,
        fontWeight: 600,
        background: `${color}1A`,
        color,
        border: `1px solid ${color}55`,
        borderRadius: 6,
        cursor: busy ? "default" : "pointer",
        opacity: busy ? 0.6 : 1,
        whiteSpace: "nowrap",
      }}
    >
      <Icon size={14} />
      {label}
    </button>
  );
}
