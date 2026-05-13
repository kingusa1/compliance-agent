"use client";

/**
 * RejectionsTable — left master pane of the /rejections page.
 *
 * Faithfully ported from design/handoff-bundle/project/hifi/rejections-hifi.jsx
 * (RejTableHeader + RejRow). 9-col grid:
 *   1. Customer (clickable hyperlink → wattutilities.co.uk:4433/sites/{id})
 *   2. MPAN/MPRN (mono)
 *   3. Supplier
 *   4. Sales agent (avatar + name)
 *   5. Category (5-color chip)
 *   6. Rejection reason (truncated, hover for full)
 *   7. Status (Pill)
 *   8. Deadline (DeadlineBadge) — replaced by "resolved" / dead-reason on
 *      fixed/dead tabs (per prototype)
 *   9. 3-dot menu (placeholder — kebab visual only for now)
 */
import { MoreHorizontal } from "lucide-react";

import { Avatar } from "@/components/design/Avatar";
import { Pill, type PillTone } from "@/components/design/Pill";
import type { Rejection, RejectionStatus } from "@/lib/schemas/rejections";
import {
  REJECTION_STATUS_LABELS,
} from "@/lib/schemas/rejections";

import { CategoryChip } from "./CategoryChip";
import { DeadReasonChip } from "./DeadReasonChip";
import { DeadlineBadge } from "./DeadlineBadge";

const GRID_COLS = "1.4fr 130px 1fr 110px 150px 1.4fr 100px 110px 32px";

const HEADER_CELL: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  color: "var(--text-dim)",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
};

const STATUS_TONES: Record<RejectionStatus, PillTone> = {
  NOT_STARTED: "neutral",
  IN_PROGRESS: "amber",
  FIXED: "emerald",
  BATCHED_TO_PORTAL: "blue",
  SUBMITTED_TO_PORTAL: "violet",
  FIXED_AND_APPROVED: "emerald",
  DEAD: "red",
};

function StatusPill({ status }: { status: RejectionStatus | string }) {
  const tone =
    (STATUS_TONES[status as RejectionStatus] as PillTone | undefined) ??
    "neutral";
  const label =
    REJECTION_STATUS_LABELS[status as RejectionStatus] ?? String(status);
  return (
    <Pill tone={tone} dot>
      {label}
    </Pill>
  );
}

function PortalLink({
  customer,
  siteId,
}: {
  customer: string;
  siteId: number | null;
}) {
  // Watt portal anchor pattern from XLSX deep-dive §1.3, X1. We render the
  // <a> only when we know the site_id; otherwise the customer cell stays a
  // plain label so we don't ship a broken link.
  if (!siteId) {
    return <span>{customer}</span>;
  }
  return (
    <a
      href={`https://api.wattutilities.co.uk:4433/sites/${siteId}`}
      target="_blank"
      rel="noreferrer noopener"
      onClick={(e) => e.stopPropagation()}
      style={{
        color: "inherit",
        textDecoration: "underline",
        textDecorationColor: "var(--border-strong)",
        textUnderlineOffset: 3,
      }}
    >
      {customer}
    </a>
  );
}

export type RejectionsTableProps = {
  rejections: Rejection[];
  selectedId: string | null;
  tab: "active" | "fixed" | "dead" | "archive";
  onSelect: (id: string) => void;
  isLoading?: boolean;
};

export function RejectionsTable({
  rejections,
  selectedId,
  tab,
  onSelect,
  isLoading = false,
}: RejectionsTableProps) {
  return (
    <div
      data-slot="rejections-table"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: GRID_COLS,
          gap: 10,
          padding: "10px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          background: "var(--bg-elev1)",
          position: "sticky",
          top: 0,
          zIndex: 1,
          flexShrink: 0,
        }}
      >
        <div style={HEADER_CELL}>Customer</div>
        <div style={HEADER_CELL}>MPAN/MPRN</div>
        <div style={HEADER_CELL}>Supplier</div>
        <div style={HEADER_CELL}>Sales agent</div>
        <div style={HEADER_CELL}>Category</div>
        <div style={HEADER_CELL}>Rejection reason</div>
        <div style={HEADER_CELL}>Status</div>
        <div style={HEADER_CELL}>{tab === "dead" ? "Died" : "Deadline"}</div>
        <div />
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {isLoading && rejections.length === 0 ? (
          <div
            style={{
              padding: 40,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
            }}
          >
            Loading rejections…
          </div>
        ) : rejections.length === 0 ? (
          <div
            style={{
              padding: 60,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
            }}
          >
            No rejections in this tab.
          </div>
        ) : (
          rejections.map((row) => {
            const selected = row.id === selectedId;
            // Prefer the human customer_name; fall back to slug, then dash.
            // The slug was originally rendered as the user-visible label,
            // which surfaced URL-style strings ("acme-energy-ltd") instead of
            // a clean trading name. Plan §5d: surface customer_name here.
            const customer = row.customer_name || row.customer_slug || "—";
            return (
              <div
                key={row.id}
                onClick={() => onSelect(row.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(row.id);
                  }
                }}
                data-slot="rejection-row"
                data-selected={selected ? "1" : "0"}
                style={{
                  display: "grid",
                  gridTemplateColumns: GRID_COLS,
                  gap: 10,
                  alignItems: "center",
                  padding: "11px 24px",
                  borderBottom: "1px solid var(--border-subtle)",
                  background: selected ? "var(--bg-elev2)" : "transparent",
                  borderLeft: `2px solid ${selected ? "var(--emerald)" : "transparent"}`,
                  cursor: "pointer",
                  fontSize: 13,
                }}
              >
                {/* Customer */}
                <div
                  style={{
                    minWidth: 0,
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  <div
                    style={{
                      color: "var(--text-primary)",
                      fontWeight: 500,
                      letterSpacing: "-0.005em",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    <PortalLink
                      customer={customer}
                      siteId={row.external_watt_site_id}
                    />
                    {row.external_watt_site_id != null && (
                      <span
                        style={{ color: "var(--text-dim)", fontSize: 11 }}
                        aria-hidden
                      >
                        ↗
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--font-mono)",
                      color: "var(--text-dim)",
                      fontSize: 10.5,
                      letterSpacing: 0,
                    }}
                  >
                    {row.id.slice(0, 8)}
                    {row.external_watt_site_id != null
                      ? ` · site_${row.external_watt_site_id}`
                      : ""}
                  </div>
                </div>

                {/* MPAN/MPRN — backend hasn't populated this yet (W1
                    multi-meter is on Deal not Rejection); show em-dash. */}
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-muted)",
                    fontSize: 11.5,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  —
                </div>

                {/* Supplier */}
                <div
                  style={{
                    color: "var(--text-muted)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {row.supplier ?? "—"}
                </div>

                {/* Sales agent */}
                <div
                  style={{
                    color: "var(--text-muted)",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    overflow: "hidden",
                  }}
                >
                  {row.sales_agent ? (
                    <>
                      <Avatar
                        name={row.sales_agent}
                        size={20}
                        tone={selected ? "emerald" : "neutral"}
                      />
                      <span
                        style={{
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {row.sales_agent}
                      </span>
                    </>
                  ) : (
                    <span style={{ color: "var(--text-dim)" }}>—</span>
                  )}
                </div>

                {/* Category */}
                <div>
                  <CategoryChip category={row.category} />
                </div>

                {/* Reason */}
                <div
                  title={row.rejection_reason}
                  style={{
                    color: "var(--text-muted)",
                    fontSize: 12.5,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    letterSpacing: "-0.003em",
                  }}
                >
                  {row.rejection_reason}
                </div>

                {/* Status */}
                <div>
                  <StatusPill status={row.status} />
                </div>

                {/* Deadline / resolved / dead-reason */}
                <div>
                  {tab === "fixed" ? (
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "var(--emerald-400)",
                      }}
                    >
                      resolved
                    </span>
                  ) : tab === "dead" ? (
                    /* W4.6 — show the dead-reason chip (or "no reason"
                       placeholder when null). */
                    <DeadReasonChip reason={row.dead_reason} />
                  ) : (
                    <DeadlineBadge
                      deadline={row.deadline}
                      status={row.status}
                    />
                  )}
                </div>

                {/* Kebab */}
                <div
                  style={{
                    color: "var(--text-dim)",
                    display: "flex",
                    justifyContent: "center",
                    padding: 4,
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreHorizontal size={14} strokeWidth={1.75} />
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
