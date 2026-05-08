"use client";

/**
 * W3.A — Red banner shown at the top of /calls/[id] whenever the L2
 * extraction writer emitted at least one PRICING_MISMATCH flag for the
 * call. Stacks above the tab grid; rows are click-to-seek so the
 * reviewer can jump to the moment the agent quoted the wrong rate.
 *
 * Pure presentational — the page owns the seek logic.
 */
import { AlertTriangle } from "lucide-react";

import type { Flag } from "@/lib/queries/reviewer";

export type PricingMismatchBannerProps = {
  flags: Flag[];
  onSeek: (flag: Flag) => void;
};

export function PricingMismatchBanner({ flags, onSeek }: PricingMismatchBannerProps) {
  const pricingFlags = flags.filter((f) => f.rule_id === "PRICING_MISMATCH");
  if (pricingFlags.length === 0) return null;

  return (
    <div
      role="alert"
      aria-label="Pricing mismatch detected"
      className="bg-red-50 border-b border-red-300 text-red-900"
      style={{
        padding: "10px 20px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        flexShrink: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, fontSize: 13 }}>
        <AlertTriangle size={16} aria-hidden="true" />
        <span>
          Pricing mismatch — {pricingFlags.length === 1 ? "1 issue" : `${pricingFlags.length} issues`}
        </span>
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
        {pricingFlags.map((f) => (
          <li key={f.id}>
            <button
              type="button"
              onClick={() => onSeek(f)}
              data-testid={`pricing-mismatch-row-${f.id}`}
              style={{
                background: "transparent",
                border: "none",
                padding: "4px 0 4px 24px",
                color: "inherit",
                fontFamily: "inherit",
                fontSize: 12.5,
                textAlign: "left",
                cursor: "pointer",
                width: "100%",
                textDecoration: "underline",
                textDecorationStyle: "dotted",
                textUnderlineOffset: 3,
              }}
            >
              ⚠ {f.reason}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default PricingMismatchBanner;
