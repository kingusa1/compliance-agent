"use client";

/**
 * Wave-50 — Data-quality warning banner.
 *
 * Renders an amber alert at the top of /calls/[id] for each
 * `data_quality_warnings` entry the pipeline attached to the call. The
 * first (and currently only) producer is `customer_name_mismatch`: the
 * recording's detected business name strongly diverges from the deal it
 * was attached to — i.e. the reviewer likely uploaded the WRONG
 * customer's call.
 *
 * Deliberately a SEPARATE channel from compliance flags (which drive the
 * Vulnerability / PricingMismatch banners) so a data-quality signal never
 * mixes into the compliance findings or report. Stacks with the other
 * banners — page.tsx renders them in document order from the page top.
 */
import { AlertTriangle } from "lucide-react";

export type DataQualityWarning = { code: string; message: string };

export function DataQualityBanner({
  warnings,
}: {
  warnings?: DataQualityWarning[];
}) {
  const items = (warnings ?? []).filter(
    (w): w is DataQualityWarning =>
      !!w && typeof w.message === "string" && w.message.length > 0,
  );
  if (items.length === 0) return null;

  return (
    <div
      data-testid="data-quality-banner"
      role="alert"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "10px 16px",
        background: "var(--amber-bg)",
        border: "1px solid var(--amber-border)",
        borderLeft: "3px solid var(--amber)",
        color: "var(--amber-400)",
        fontSize: 12,
      }}
    >
      <AlertTriangle
        size={16}
        style={{ color: "var(--amber)", flexShrink: 0, marginTop: 2 }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, marginBottom: items.length > 1 ? 4 : 0 }}>
          Data quality — verify this recording
        </div>
        {items.length > 1 ? (
          <ul
            style={{
              margin: 0,
              paddingLeft: 16,
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            {items.map((w, i) => (
              <li key={`${w.code}-${i}`} style={{ color: "var(--amber-400)" }}>
                {w.message}
              </li>
            ))}
          </ul>
        ) : (
          <div>{items[0].message}</div>
        )}
      </div>
    </div>
  );
}
