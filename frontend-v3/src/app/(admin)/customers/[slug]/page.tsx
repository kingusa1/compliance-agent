"use client";

/**
 * /customers/[slug] — ported from
 * design/handoff-bundle/project/screens/customer-detail.jsx.
 *
 * Hero: back arrow + customer name + inline KPIs + +Upload primary button.
 * 6-stat strip · Deal cards (workflow progress bar) · Call timeline table.
 */
import { use, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ExternalLink } from "lucide-react";

import {
  useCustomerDetailQuery,
  useCustomerRollupQuery,
  useCustomerTimelineQuery,
} from "@/lib/queries/admin";
import { Pill, type PillTone } from "@/components/design/Pill";
import { WorkflowTypePill } from "@/components/design/WorkflowTypePill";
import { UploadModal } from "@/app/(admin)/calls/UploadModal";
import {
  PHASE_LABEL,
  SEGMENT_PARENT,
  TOPLEVEL_LABEL,
  isEonSupplier,
  workflowStepsFor as _workflowStepsForShared,
  workflowSummary,
  type SegmentStage,
  type TopLevelStage,
} from "@/lib/workflow";

/** W1.1 (v3-watt-coverage): build the Watt portal deep-link URL. */
function wattPortalUrl(siteId: number | null | undefined): string | null {
  if (siteId == null || !Number.isFinite(siteId)) return null;
  return `https://api.wattutilities.co.uk:4433/sites/${siteId}`;
}

// Workflow phases are resolved by `lib/workflow.ts` — single source of truth
// that mirrors backend `deal_lifecycle.SUPPLIER_PHASE_MATRIX`:
//   - E.ON variants → 3 required stages (LOA bundled into Closer)
//   - everyone else → 4 required stages (+ Standalone LOA)
// Corrective steps (Amendment, C-Call) are appended for any supplier and
// don't count toward the headline 3/4.
function workflowStepsFor(supplier: string | null | undefined): string[] {
  return _workflowStepsForShared(supplier);
}

function completedPhaseCount(deal: { calls: { call_type?: string | null }[] }, steps: string[]): number {
  // Count distinct workflow phases that this deal's calls have covered.
  // The 2026-05-12 taxonomy rebuild canonicalised call_type to
  // lead_gen / pre_sales / verbal / loa — no remapping needed any more.
  const seen = new Set<string>();
  for (const c of deal.calls) {
    const p = (c.call_type ?? "").toLowerCase();
    if (steps.includes(p)) seen.add(p);
  }
  // For the progress-bar position we treat every distinct workflow
  // phase covered as one step done. Order is preserved by `steps`.
  return seen.size;
}

function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  tone?: string;
}) {
  return (
    <div
      style={{
        flex: 1,
        padding: 14,
        background: "var(--bg-elev2)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 8,
        minWidth: 0,
      }}
    >
      <div
        style={{
          fontSize: 11,
          color: "var(--text-faint)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <div
          style={{
            fontSize: 22,
            fontWeight: 600,
            color: tone || "var(--text-primary)",
            fontVariantNumeric: "tabular-nums",
            letterSpacing: "-0.01em",
          }}
        >
          {value}
        </div>
        {sub && (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{sub}</div>
        )}
      </div>
    </div>
  );
}

function WorkflowBar({
  steps,
  current,
  supplier,
}: {
  steps: string[];
  current: number;
  supplier?: string | null;
}) {
  const eon = isEonSupplier(supplier);
  const summary = supplier ? workflowSummary(supplier) : undefined;
  // Group the inner segments under the 2 top-level deal stages
  // (Opener / Closer) — 2026-05-14 model.
  const groups: Record<TopLevelStage, string[]> = { opener: [], closer: [] };
  for (const s of steps) {
    const parent = SEGMENT_PARENT[s as SegmentStage];
    if (parent) groups[parent].push(s);
  }

  return (
    <div style={{ marginTop: 10 }}>
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          marginBottom: 6,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
        title={summary}
      >
        <WorkflowTypePill supplier={supplier ?? null} />
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--text-faint)" }}>
          2 stages · Opener + Closer
          {" · "}{eon ? "LOA bundled in Closer" : "LOA is a DocuSign document, not a recording"}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        {steps.map((s, i) => {
          const done = i < current;
          const active = i === current;
          const corrective = false;
          const isVerbalEon = eon && s === "verbal";
          const isLoaEon = eon && s === "loa";
          // Sublabel surfaces the supplier-specific twist (LOA bundled into
          // Verbal for E.ON; nothing for non-E.ON since LOA is paper).
          const subLabel = isVerbalEon
            ? "+ LOA bundled"
            : isLoaEon
              ? "inside Closer call"
              : null;
          return (
            <div key={s} style={{ display: "flex", alignItems: "center", flex: 1 }}>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  padding: "4px 8px",
                  borderRadius: 4,
                  background: done
                    ? "var(--emerald-bg)"
                    : active
                      ? "var(--amber-bg)"
                      : "var(--bg-elev3)",
                  border: `1px solid ${
                    done
                      ? "var(--emerald-border)"
                      : active
                        ? "var(--amber-border)"
                        : "var(--border-subtle)"
                  }`,
                  fontSize: 11,
                  color: done
                    ? "var(--emerald-400)"
                    : active
                      ? "var(--amber-400)"
                      : "var(--text-faint)",
                  opacity: corrective && !done && !active ? 0.55 : 1,
                  flex: 1,
                  minWidth: 0,
                }}
                title={
                  isVerbalEon
                    ? "E.ON reads the LOA wording inside the Verbal contract — no separate LOA recording needed."
                    : isLoaEon
                      ? "E.ON LOA segment — captured inside the Closer recording (NOT a separate document)."
                      : PHASE_LABEL[s as keyof typeof PHASE_LABEL]
                }
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 5,
                      height: 5,
                      borderRadius: "50%",
                      background: done
                        ? "var(--emerald)"
                        : active
                          ? "var(--amber)"
                          : "var(--border-strong)",
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ whiteSpace: "nowrap" }}>
                    {PHASE_LABEL[s as keyof typeof PHASE_LABEL] ?? s}
                  </span>
                </div>
                {subLabel && (
                  <span
                    style={{
                      fontSize: 9,
                      letterSpacing: "0.04em",
                      textTransform: "uppercase",
                      color: "var(--text-faint)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {subLabel}
                  </span>
                )}
              </div>
              {i < steps.length - 1 && (
                <div
                  style={{
                    flex: "0 0 12px",
                    height: 1,
                    background: "var(--border-subtle)",
                    minWidth: 8,
                  }}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function complianceTone(s: string | boolean | null | undefined): PillTone {
  // Backend timeline rows return `compliant` as a boolean. Earlier callers
  // pass strings (`worst_action`, `final_action`). Coerce both shapes
  // safely — `true || ""` evaluates to `true` and then `.toLowerCase()`
  // throws "(e || '').toLowerCase is not a function". audit-late 2026-05-11.
  if (typeof s === "boolean") {
    return s ? "emerald" : "red";
  }
  const v = typeof s === "string" ? s.toLowerCase() : "";
  switch (v) {
    case "compliant":
    case "pass":
      return "emerald";
    case "review":
      return "amber";
    case "fail":
    case "non_compliant":
      return "red";
    default:
      return "neutral";
  }
}

export default function CustomerDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug: rawSlug } = use(params);
  // Next.js 16 leaves dynamic-route params URI-encoded. Decode once here so
  // the slug we send to query helpers matches the DB key (LOWER(TRIM(name))),
  // and the helper's encodeURIComponent doesn't double-encode (e.g. `%20` → `%2520`).
  const slug = (() => {
    try {
      return decodeURIComponent(rawSlug);
    } catch {
      return rawSlug;
    }
  })();
  const detail = useCustomerDetailQuery(slug);
  const rollup = useCustomerRollupQuery(slug);
  const timeline = useCustomerTimelineQuery(slug);
  const [uploadOpen, setUploadOpen] = useState(false);

  const customer = detail.data?.customer;
  const deals = detail.data?.deals ?? [];
  const rollupData = rollup.data ?? {};
  const timelineRows = timeline.data?.rows ?? [];

  const heroLabel = customer?.display_name ?? slug;
  const dealCount = customer?.deal_count ?? deals.length;
  const callCount = customer?.call_count ?? timelineRows.length;
  const supplier = (customer?.suppliers ?? [])[0] ?? "—";
  const worst = customer?.worst_action ?? null;
  const openDirs = (rollupData.open_directives as number | undefined) ?? customer?.open_directives ?? 0;
  const valueGbp = (rollupData.total_value_gbp as number | undefined) ?? null;
  // W1.1 (v3-watt-coverage): Watt portal deep-link chip (top-right of hero).
  const wattSiteId =
    (customer as { external_watt_site_id?: number | null } | undefined)?.external_watt_site_id ??
    null;
  const wattUrl = wattPortalUrl(wattSiteId);
  // W1.5 (v3-watt-coverage): aggregate risk-tag count for the hero readout.
  // Backend rollup returns ``risk_tag_aggregate`` keyed by canonical tag.
  const riskAgg = (rollupData.risk_tag_aggregate as Record<string, number> | undefined) ?? {};
  const riskTagTotal = Object.values(riskAgg).reduce((acc, n) => acc + (Number(n) || 0), 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      {/* Hero */}
      <div
        style={{
          padding: "16px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Link
            href="/customers"
            style={{
              height: 28,
              padding: "0 10px",
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              fontSize: 12,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              textDecoration: "none",
            }}
          >
            <ArrowLeft size={14} />
            Customers
          </Link>
          <div style={{ width: 1, height: 18, background: "var(--border-subtle)" }} />
          <h1
            style={{
              fontSize: 19,
              fontWeight: 600,
              letterSpacing: "-0.018em",
              margin: 0,
              color: "var(--text-primary)",
            }}
          >
            {heroLabel}
          </h1>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              flex: 1,
              marginLeft: 8,
            }}
          >
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              {dealCount} deal{dealCount === 1 ? "" : "s"}
            </span>
            <span style={{ fontSize: 12, color: "var(--text-faint)" }}>·</span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              {callCount} call{callCount === 1 ? "" : "s"}
            </span>
            <span style={{ fontSize: 12, color: "var(--text-faint)" }}>·</span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{supplier}</span>
            {supplier !== "—" && (
              <WorkflowTypePill supplier={supplier} />
            )}
            {worst && (
              <>
                <span style={{ fontSize: 12, color: "var(--text-faint)" }}>·</span>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>worst:</span>
                <Pill tone={complianceTone(worst)}>{worst}</Pill>
              </>
            )}
          </div>
          {riskTagTotal > 0 && (
            <span
              data-slot="risk-tag-count"
              style={{
                fontSize: 11,
                fontWeight: 500,
                padding: "2px 8px",
                borderRadius: 999,
                background: "var(--amber-bg)",
                color: "var(--amber-400)",
                border: "1px solid var(--amber-border)",
              }}
              title="Aggregate risk-tag count from this customer's calls"
            >
              {riskTagTotal} risk tag{riskTagTotal === 1 ? "" : "s"}
            </span>
          )}
          {wattUrl && (
            <a
              href={wattUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-slot="watt-portal-chip"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                height: 24,
                padding: "0 8px",
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: "var(--text-muted)",
                background: "var(--bg-elev2)",
                border: "1px solid var(--border-subtle)",
                borderRadius: 4,
                textDecoration: "none",
              }}
              title={`Open site ${wattSiteId} in Watt portal`}
            >
              Watt portal <ExternalLink size={11} />
            </a>
          )}
          <button
            type="button"
            onClick={() => setUploadOpen(true)}
            data-testid="customer-upload-trigger"
            style={{
              height: 32,
              padding: "0 12px",
              fontSize: 13,
              fontWeight: 500,
              background: "var(--emerald)",
              color: "#04201a",
              border: "1px solid var(--emerald)",
              borderRadius: 6,
              cursor: "pointer",
              fontFamily: "inherit",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            + Upload call to this customer
          </button>
        </div>
      </div>

      <UploadModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        customerSlug={slug}
        prefill={{
          customer: {
            name: customer?.display_name ?? slug,
            slug,
          },
        }}
      />

      {/* Body */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: 24,
          display: "flex",
          flexDirection: "column",
          gap: 24,
          minHeight: 0,
        }}
        className="ca-scroll"
      >
        {/* 6-stat strip */}
        <div style={{ display: "flex", gap: 12 }}>
          <StatCard label="Deals" value={dealCount} />
          <StatCard label="Calls" value={callCount} />
          <StatCard
            label="£ Value"
            value={valueGbp != null ? `£${(valueGbp / 1000).toFixed(1)}k` : "—"}
            sub="contracted"
          />
          <StatCard
            label="Open Directives"
            value={openDirs}
            tone={openDirs > 0 ? "var(--amber)" : undefined}
          />
          <StatCard
            label="Worst Action"
            value={worst || "—"}
            tone={worst ? `var(--${complianceTone(worst)})` : undefined}
          />
          <StatCard
            label="Open Rejections"
            value={(rollupData.open_rejections as number | undefined) ?? 0}
          />
        </div>

        {/* Deals */}
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 10,
            }}
          >
            <h3
              style={{
                fontSize: 16,
                fontWeight: 600,
                letterSpacing: "-0.014em",
                margin: 0,
                color: "var(--text-primary)",
              }}
            >
              Deals
            </h3>
            <Pill tone="neutral">{deals.length}</Pill>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {deals.map((deal) => {
              const supplierAwareSteps = workflowStepsFor(deal.supplier);
              const stepIdx = completedPhaseCount(deal, supplierAwareSteps);
              return (
                <Link
                  key={deal.id}
                  href={`/deals/${encodeURIComponent(deal.id)}`}
                  style={{
                    display: "block",
                    padding: 16,
                    background: "var(--bg-elev2)",
                    border: "1px solid var(--border-subtle)",
                    borderRadius: 8,
                    textDecoration: "none",
                    color: "inherit",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      flexWrap: "wrap",
                    }}
                  >
                    <Pill tone="neutral" mono>
                      {deal.deal_ref}
                    </Pill>
                    <span
                      style={{
                        fontSize: 14,
                        color: "var(--text-primary)",
                        fontWeight: 500,
                      }}
                    >
                      {deal.supplier ?? "—"}
                    </span>
                    {deal.deal_value_gbp != null && (
                      <span
                        style={{
                          fontSize: 14,
                          color: "var(--text-primary)",
                          fontFamily: "var(--font-mono)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        £{deal.deal_value_gbp.toLocaleString()}
                      </span>
                    )}
                    <Pill tone={complianceTone(deal.final_action)} dot>
                      {deal.status ?? "—"}
                    </Pill>
                    <div style={{ flex: 1 }} />
                    <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      {deal.calls.length} of {supplierAwareSteps.length} steps · {stepIdx} done
                    </span>
                  </div>
                  <WorkflowBar steps={supplierAwareSteps} current={stepIdx} supplier={deal.supplier} />
                </Link>
              );
            })}
            {deals.length === 0 && (
              <div
                style={{
                  padding: 24,
                  background: "var(--bg-elev2)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 8,
                  fontSize: 13,
                  color: "var(--text-muted)",
                  textAlign: "center",
                }}
              >
                No deals yet.
              </div>
            )}
          </div>
        </div>

        {/* Call timeline */}
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 10,
            }}
          >
            <h3
              style={{
                fontSize: 16,
                fontWeight: 600,
                letterSpacing: "-0.014em",
                margin: 0,
                color: "var(--text-primary)",
              }}
            >
              Call timeline
            </h3>
            <Pill tone="neutral">{timelineRows.length}</Pill>
          </div>
          <div
            style={{
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "100px 130px 130px 130px 90px 110px 1fr",
                gap: 12,
                padding: "10px 20px",
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-elev3)",
              }}
            >
              {["When", "Deal", "Call Type", "Agent", "Score", "Compliant", "Rejection"].map(
                (h) => (
                  <div
                    key={h}
                    style={{
                      fontSize: 11,
                      fontWeight: 500,
                      color: "var(--text-faint)",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
                    {h}
                  </div>
                ),
              )}
            </div>
            {timelineRows.length === 0 ? (
              <div
                style={{
                  padding: 32,
                  fontSize: 13,
                  color: "var(--text-muted)",
                  textAlign: "center",
                }}
              >
                No calls yet.
              </div>
            ) : (
              timelineRows.map((row, i) => (
                <div
                  key={row.id ?? i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "100px 130px 130px 130px 90px 110px 1fr",
                    gap: 12,
                    alignItems: "center",
                    padding: "12px 20px",
                    borderBottom: "1px solid var(--border-subtle)",
                    fontSize: 13,
                  }}
                >
                  <div
                    style={{
                      color: "var(--text-muted)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {row.created_at
                      ? new Date(row.created_at).toLocaleDateString()
                      : "—"}
                  </div>
                  <div
                    style={{
                      color: "var(--text-primary)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                    }}
                  >
                    {row.deal_ref ?? "—"}
                  </div>
                  <div>
                    <Pill tone="neutral">{row.call_type ?? "call"}</Pill>
                  </div>
                  <div style={{ color: "var(--text-muted)" }}>{row.agent_name ?? "—"}</div>
                  <div
                    style={{
                      fontFamily: "var(--font-mono)",
                      color: "var(--text-primary)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {row.score ?? "—"}
                  </div>
                  <div>
                    <Pill tone={complianceTone(row.compliant)} dot>
                      {row.compliant === true
                        ? "compliant"
                        : row.compliant === false
                          ? "non_compliant"
                          : typeof row.compliant === "string" && row.compliant
                            ? row.compliant
                            : "—"}
                    </Pill>
                  </div>
                  <div style={{ color: "var(--text-faint)" }}>
                    {row.rejection ?? "—"}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
