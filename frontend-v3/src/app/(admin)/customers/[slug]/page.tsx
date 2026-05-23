"use client";

/**
 * /customers/[slug] — ported from
 * design/handoff-bundle/project/screens/customer-detail.jsx.
 *
 * Hero: back arrow + customer name + inline KPIs + +Upload primary button.
 * 6-stat strip · Deal cards (workflow progress bar) · Call timeline table.
 */
import { use, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ExternalLink,
  AlertTriangle,
  CheckCircle2,
  Clock,
  ListChecks,
  Radio,
  Sparkles,
} from "lucide-react";

import {
  useCustomerDetailQuery,
  useCustomerRollupQuery,
  useCustomerTimelineQuery,
} from "@/lib/queries/admin";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";
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
  const [riskTagsExpanded, setRiskTagsExpanded] = useState(false);

  // 2026-05-23 — full realtime wiring. The customer detail view is
  // assembled from three queries (detail / rollup / timeline); every
  // CDC event from calls, customer_deals, or rejections invalidates
  // all three so the page stays live without polling. Matches the
  // policy in BRAIN/00_SYSTEM_PROMPT.md §1.
  const realtimeKeys = useMemo(
    () => [
      ["admin", "customer", slug],
      ["admin", "customer", slug, "rollup"],
      ["admin", "customer", slug, "timeline"],
    ],
    [slug],
  );
  useRealtimeInvalidate("calls", realtimeKeys);
  useRealtimeInvalidate("customer_deals", realtimeKeys);
  useRealtimeInvalidate("rejections", realtimeKeys);

  const customer = detail.data?.customer;
  const deals = detail.data?.deals ?? [];
  const rollupData = rollup.data ?? {};
  const timelineRows = timeline.data?.rows ?? [];

  const heroLabel = customer?.display_name ?? slug;
  const dealCount = customer?.deal_count ?? deals.length;
  const callCount = customer?.call_count ?? timelineRows.length;
  const supplier = (customer?.suppliers ?? [])[0] ?? "—";
  const worst = customer?.worst_action ?? null;
  // 2026-05-14 audit fix: align with what `customers_routes.py:378+` actually
  // emits (`total_open_directives`, `total_deal_value_gbp_annual_sum`,
  // `dead_rejections_count`). The old keys (`open_directives`, `total_value_gbp`,
  // `open_rejections`) were undefined on the response so all 3 KPI cards
  // showed 0/—. Keep the legacy keys as `??` fallbacks so re-deploy ordering
  // never breaks the page.
  const openDirs =
    (rollupData.total_open_directives as number | undefined) ??
    (rollupData.open_directives as number | undefined) ??
    customer?.open_directives ??
    0;
  const valueGbp =
    (rollupData.total_deal_value_gbp_annual_sum as number | undefined) ??
    (rollupData.total_value_gbp as number | undefined) ??
    null;
  // W1.1 (v3-watt-coverage): Watt portal deep-link chip (top-right of hero).
  const wattSiteId =
    (customer as { external_watt_site_id?: number | null } | undefined)?.external_watt_site_id ??
    null;
  const wattUrl = wattPortalUrl(wattSiteId);
  // W1.5 (v3-watt-coverage): aggregate risk-tag count for the hero readout.
  // Backend rollup returns ``risk_tag_aggregate`` keyed by canonical tag.
  const riskAgg = (rollupData.risk_tag_aggregate as Record<string, number> | undefined) ?? {};
  const riskTagTotal = Object.values(riskAgg).reduce((acc, n) => acc + (Number(n) || 0), 0);

  // 2026-05-23 redesign — derived metrics for the new top sections.
  // Everything below is read-only off existing data: no extra API
  // calls, no schema changes. The Action Banner + Compliance Hero
  // surface what the reviewer needs to act on in one glance.
  const compliantRows = timelineRows.filter(
    (r) => (r as { compliant?: unknown }).compliant === true,
  );
  const nonCompliantRows = timelineRows.filter(
    (r) => (r as { compliant?: unknown }).compliant === false,
  );
  const scoredTotal = compliantRows.length + nonCompliantRows.length;
  const compliancePct =
    scoredTotal > 0
      ? Math.round((compliantRows.length / scoredTotal) * 100)
      : null;
  // What's left for the reviewer to action.
  const callsToReview = nonCompliantRows.length;
  // Per-deal "missing phase" hints — what the supplier workflow expects
  // vs what calls have been uploaded. Drives the "what's next" line on
  // each deal card.
  const dealsWithMissing = deals.map((deal) => {
    const steps = workflowStepsFor(deal.supplier);
    const seen = new Set(
      deal.calls.map((c) => (c.call_type ?? "").toLowerCase()).filter(Boolean),
    );
    const missing = steps.filter((s) => !seen.has(s));
    return { deal, steps, missing };
  });
  const totalMissingPhases = dealsWithMissing.reduce(
    (acc, d) => acc + d.missing.length,
    0,
  );
  // Top 6 risk tags by frequency for the expandable section. Sorted
  // descending so the worst sit at the top.
  const topRiskTags = Object.entries(riskAgg)
    .filter(([, n]) => Number(n) > 0)
    .sort(([, a], [, b]) => Number(b) - Number(a))
    .slice(0, 6);
  // The "Live" indicator only renders once at least one of the queries
  // has returned — otherwise the user sees "Live" before any data has
  // arrived which is misleading.
  const dataLoaded = detail.isSuccess || rollup.isSuccess || timeline.isSuccess;

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
          gap: 20,
          minHeight: 0,
        }}
        className="ca-scroll"
      >
        {/* ─────────────────────────────────────────────────────────────
            ACTION BANNER — what the reviewer should do next on this
            customer. Only rendered when something is actionable so it
            doesn't add visual noise on cleanly-passing accounts.
            ───────────────────────────────────────────────────────────── */}
        {(callsToReview > 0 || openDirs > 0 || totalMissingPhases > 0) && (
          <div
            data-slot="action-banner"
            style={{
              padding: "14px 18px",
              borderRadius: 10,
              background:
                "linear-gradient(180deg, color-mix(in oklab, var(--amber) 14%, var(--bg-elev2)) 0%, var(--bg-elev2) 100%)",
              border: "1px solid var(--amber-border)",
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: 14,
            }}
          >
            <div
              style={{
                width: 32,
                height: 32,
                display: "grid",
                placeItems: "center",
                borderRadius: 8,
                background: "var(--amber-bg)",
                color: "var(--amber-400)",
                flexShrink: 0,
              }}
            >
              <AlertTriangle size={16} />
            </div>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  marginBottom: 2,
                }}
              >
                Action queue — {heroLabel}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
                {[
                  callsToReview > 0 &&
                    `${callsToReview} non-compliant call${callsToReview === 1 ? "" : "s"} need reviewer sign-off`,
                  openDirs > 0 &&
                    `${openDirs} open directive${openDirs === 1 ? "" : "s"} pending fix`,
                  totalMissingPhases > 0 &&
                    `${totalMissingPhases} workflow step${totalMissingPhases === 1 ? "" : "s"} missing across ${deals.length} deal${deals.length === 1 ? "" : "s"}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
              {callsToReview > 0 && (
                <Link
                  href={`/queue?filter=unclaimed&q=${encodeURIComponent(heroLabel)}`}
                  style={{
                    height: 30,
                    padding: "0 12px",
                    fontSize: 12.5,
                    fontWeight: 500,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    background: "var(--amber-400)",
                    color: "#1f1300",
                    borderRadius: 6,
                    textDecoration: "none",
                  }}
                >
                  <ListChecks size={13} /> Review now
                </Link>
              )}
              {openDirs > 0 && (
                <Link
                  href={`/rejections?source=reviewer&search=${encodeURIComponent(heroLabel)}`}
                  style={{
                    height: 30,
                    padding: "0 12px",
                    fontSize: 12.5,
                    fontWeight: 500,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    background: "var(--bg-elev3)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-subtle)",
                    borderRadius: 6,
                    textDecoration: "none",
                  }}
                >
                  Open rejections →
                </Link>
              )}
            </div>
          </div>
        )}

        {/* ─────────────────────────────────────────────────────────────
            COMPLIANCE HERO — single big number reviewers care about,
            plus a breakdown chip and a live status indicator. Renders
            even when no calls are scored yet so the slot is never empty.
            ───────────────────────────────────────────────────────────── */}
        <div
          data-slot="compliance-hero"
          style={{
            padding: 18,
            borderRadius: 10,
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            display: "flex",
            alignItems: "center",
            gap: 24,
            flexWrap: "wrap",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 16,
              flex: 1,
              minWidth: 280,
            }}
          >
            <div
              style={{
                width: 84,
                height: 84,
                borderRadius: 12,
                background:
                  compliancePct == null
                    ? "var(--bg-elev3)"
                    : compliancePct >= 80
                      ? "var(--emerald-bg)"
                      : compliancePct >= 60
                        ? "var(--amber-bg)"
                        : "var(--red-bg)",
                border: `1px solid ${
                  compliancePct == null
                    ? "var(--border-subtle)"
                    : compliancePct >= 80
                      ? "var(--emerald-border)"
                      : compliancePct >= 60
                        ? "var(--amber-border)"
                        : "var(--red-border)"
                }`,
                display: "grid",
                placeItems: "center",
                flexShrink: 0,
              }}
            >
              <div
                style={{
                  fontSize: 24,
                  fontWeight: 700,
                  color:
                    compliancePct == null
                      ? "var(--text-faint)"
                      : compliancePct >= 80
                        ? "var(--emerald-400)"
                        : compliancePct >= 60
                          ? "var(--amber-400)"
                          : "var(--red)",
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.02em",
                }}
              >
                {compliancePct == null ? "—" : `${compliancePct}%`}
              </div>
            </div>
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: 11,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  color: "var(--text-faint)",
                  marginBottom: 4,
                }}
              >
                Compliance rate
              </div>
              <div
                style={{
                  fontSize: 15,
                  fontWeight: 500,
                  color: "var(--text-primary)",
                  marginBottom: 6,
                }}
              >
                {compliancePct == null
                  ? "No calls scored yet"
                  : compliancePct >= 80
                    ? "Healthy — keep an eye on outliers"
                    : compliancePct >= 60
                      ? "Mixed — review the failing calls first"
                      : "Critical — most calls need reviewer action"}
              </div>
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  flexWrap: "wrap",
                  fontSize: 12,
                }}
              >
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    color: "var(--emerald-400)",
                  }}
                >
                  <CheckCircle2 size={12} /> {compliantRows.length} compliant
                </span>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    color: "var(--red)",
                  }}
                >
                  <AlertTriangle size={12} /> {nonCompliantRows.length} non-compliant
                </span>
                {customer?.last_seen && (
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 5,
                      color: "var(--text-muted)",
                    }}
                  >
                    <Clock size={12} /> Last call{" "}
                    {new Date(customer.last_seen).toLocaleDateString()}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            {dataLoaded && (
              <span
                title="Updates push from Supabase Realtime — no refresh needed"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  fontSize: 11,
                  color: "var(--emerald-400)",
                  background: "var(--emerald-bg)",
                  border: "1px solid var(--emerald-border)",
                  padding: "3px 8px",
                  borderRadius: 999,
                }}
              >
                <Radio size={11} /> Live
              </span>
            )}
            {(customer?.agents ?? []).length > 0 && (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  fontSize: 11,
                  color: "var(--text-muted)",
                  background: "var(--bg-elev3)",
                  border: "1px solid var(--border-subtle)",
                  padding: "3px 8px",
                  borderRadius: 999,
                }}
                title={(customer?.agents ?? []).join(", ")}
              >
                <Sparkles size={11} /> {(customer?.agents ?? []).length} agent
                {(customer?.agents ?? []).length === 1 ? "" : "s"}
              </span>
            )}
          </div>
        </div>

        {/* Stats strip — every tile carries explanatory subtext when the
            value is 0/—, so reviewers know whether the slot is "no data
            yet" or "intentionally empty". */}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <StatCard
            label="Deals"
            value={dealCount}
            sub={dealCount === 0 ? "none yet" : "tracked"}
          />
          <StatCard
            label="Calls"
            value={callCount}
            sub={callCount === 0 ? "none yet" : "uploaded"}
          />
          <StatCard
            label="£ Value"
            value={valueGbp != null ? `£${(valueGbp / 1000).toFixed(1)}k` : "—"}
            sub={valueGbp != null ? "contracted" : "not extracted yet"}
          />
          <StatCard
            label="Open Directives"
            value={openDirs}
            sub={openDirs === 0 ? "none assigned" : "pending fix"}
            tone={openDirs > 0 ? "var(--amber)" : undefined}
          />
          <StatCard
            label="Worst Action"
            value={worst || "—"}
            sub={worst ? "AI verdict" : "no scored calls"}
            tone={worst ? `var(--${complianceTone(worst)})` : undefined}
          />
          <StatCard
            label="Open Rejections"
            value={
              (rollupData.dead_rejections_count as number | undefined) ??
              (rollupData.open_rejections as number | undefined) ??
              0
            }
            sub={
              ((rollupData.dead_rejections_count as number | undefined) ??
                (rollupData.open_rejections as number | undefined) ??
                0) === 0
                ? "none filed"
                : "in review"
            }
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
              const dealMissing = dealsWithMissing.find(
                (d) => d.deal.id === deal.id,
              )?.missing ?? [];
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
                  {/* What's next / submitted vs missing — surfaces the
                      reviewer's "what do I do on this deal?" without
                      forcing a drill-in. */}
                  {dealMissing.length > 0 ? (
                    <div
                      style={{
                        marginTop: 10,
                        fontSize: 12,
                        color: "var(--text-muted)",
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                      }}
                    >
                      <Clock size={11} style={{ color: "var(--amber-400)" }} />
                      <span>
                        <strong style={{ color: "var(--amber-400)" }}>
                          Not yet submitted:
                        </strong>{" "}
                        {dealMissing
                          .map((p) =>
                            (PHASE_LABEL as Record<string, string>)[p] ?? p,
                          )
                          .join(", ")}
                      </span>
                    </div>
                  ) : (
                    <div
                      style={{
                        marginTop: 10,
                        fontSize: 12,
                        color: "var(--emerald-400)",
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                      }}
                    >
                      <CheckCircle2 size={11} />
                      <span>All required calls submitted for this supplier workflow</span>
                    </div>
                  )}
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
                gridTemplateColumns: "100px 130px 110px 130px 1fr 110px 130px 90px",
                gap: 12,
                padding: "10px 20px",
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-elev3)",
              }}
            >
              {["When", "Deal", "Call Type", "Agent", "Score", "Compliant", "Rejection", ""].map(
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
              timelineRows.map((row, i) => {
                // 2026-05-14 audit fix: backend emits `call_id` / `completed_at`
                // / `rejection_category` (see customers_routes.py:512+) but
                // the row was being read as `id` / `created_at` / `rejection`.
                // Map both names so the page renders correctly without
                // breaking on a re-deploy cycle.
                const rowAny = row as Record<string, unknown>;
                const rowId =
                  (rowAny.call_id as string | undefined) ??
                  (rowAny.id as string | undefined) ??
                  null;
                const rowWhen =
                  (rowAny.completed_at as string | undefined) ??
                  (rowAny.created_at as string | undefined) ??
                  null;
                const rowRejection =
                  (rowAny.rejection_category as string | undefined) ??
                  (rowAny.rejection as string | undefined) ??
                  null;
                // Parse "20/88" → 22.7% for the inline ScoreBar.
                const scoreStr = (row.score ?? "") as string;
                const sm = scoreStr.match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)/);
                let scorePct: number | null = null;
                if (sm) {
                  const num = parseFloat(sm[1]);
                  const den = parseFloat(sm[2]);
                  if (den > 0) scorePct = Math.round((num / den) * 100);
                } else if (/^\d+%/.test(scoreStr)) {
                  scorePct = parseInt(scoreStr, 10);
                }
                const tone =
                  row.compliant === true
                    ? "emerald"
                    : row.compliant === false
                      ? "red"
                      : "neutral";
                return (
                <div
                  key={rowId ?? i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "100px 130px 110px 130px 1fr 110px 130px 90px",
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
                    {rowWhen
                      ? new Date(rowWhen).toLocaleDateString()
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
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      minWidth: 0,
                    }}
                  >
                    <div
                      style={{
                        position: "relative",
                        height: 6,
                        flex: 1,
                        background: "var(--bg-elev3)",
                        borderRadius: 999,
                        overflow: "hidden",
                        minWidth: 60,
                      }}
                    >
                      {scorePct != null && (
                        <div
                          style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            bottom: 0,
                            width: `${Math.min(100, Math.max(0, scorePct))}%`,
                            background:
                              tone === "emerald"
                                ? "var(--emerald)"
                                : tone === "red"
                                  ? "var(--red)"
                                  : "var(--amber)",
                            transition: "width 200ms ease",
                          }}
                        />
                      )}
                    </div>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-primary)",
                        fontVariantNumeric: "tabular-nums",
                        fontSize: 12,
                        minWidth: 64,
                        textAlign: "right",
                      }}
                      title={`Raw score: ${row.score ?? "n/a"}`}
                    >
                      {scorePct != null ? `${scorePct}%` : row.score ?? "—"}
                    </span>
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
                    {rowRejection ?? "—"}
                  </div>
                  <div>
                    {rowId && (
                      <Link
                        href={`/calls/${encodeURIComponent(rowId)}`}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 4,
                          height: 24,
                          padding: "0 9px",
                          fontSize: 11.5,
                          fontWeight: 500,
                          background: "var(--bg-elev3)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border-subtle)",
                          borderRadius: 5,
                          textDecoration: "none",
                        }}
                      >
                        Review →
                      </Link>
                    )}
                  </div>
                </div>
                );
              })
            )}
          </div>
        </div>

        {/* ─────────────────────────────────────────────────────────────
            RISK TAGS — when the customer has critical / vulnerability /
            mis-selling flags from the AI, surface them grouped + ranked
            so the reviewer doesn't have to drill into each call. The
            count chip in the hero links here on click.
            ───────────────────────────────────────────────────────────── */}
        {topRiskTags.length > 0 && (
          <div data-slot="risk-tags-section">
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
                Risk tags
              </h3>
              <Pill tone="amber">{riskTagTotal}</Pill>
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  marginLeft: 4,
                }}
              >
                aggregated across all calls
              </span>
              <div style={{ flex: 1 }} />
              {Object.keys(riskAgg).length > topRiskTags.length && (
                <button
                  type="button"
                  onClick={() => setRiskTagsExpanded((v) => !v)}
                  style={{
                    fontSize: 12,
                    color: "var(--text-muted)",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    textDecoration: "underline",
                    textDecorationStyle: "dotted",
                  }}
                >
                  {riskTagsExpanded
                    ? "Show top 6"
                    : `Show all ${Object.keys(riskAgg).length}`}
                </button>
              )}
            </div>
            <div
              style={{
                background: "var(--bg-elev2)",
                border: "1px solid var(--border-subtle)",
                borderRadius: 8,
                padding: 12,
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              {(riskTagsExpanded
                ? Object.entries(riskAgg)
                    .filter(([, n]) => Number(n) > 0)
                    .sort(([, a], [, b]) => Number(b) - Number(a))
                : topRiskTags
              ).map(([tag, count]) => {
                const pct =
                  riskTagTotal > 0
                    ? Math.round((Number(count) / riskTagTotal) * 100)
                    : 0;
                return (
                  <div
                    key={tag}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 100px 50px",
                      alignItems: "center",
                      gap: 12,
                      padding: "6px 4px",
                      fontSize: 12.5,
                    }}
                  >
                    <span style={{ color: "var(--text-primary)" }}>{tag}</span>
                    <div
                      style={{
                        height: 5,
                        background: "var(--bg-elev3)",
                        borderRadius: 999,
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          width: `${pct}%`,
                          height: "100%",
                          background: "var(--amber)",
                        }}
                      />
                    </div>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontVariantNumeric: "tabular-nums",
                        color: "var(--text-muted)",
                        fontSize: 11.5,
                        textAlign: "right",
                      }}
                    >
                      {Number(count)} × ({pct}%)
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
