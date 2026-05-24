"use client";

/**
 * /deals/[id] — ported from
 * design/handoff-bundle/project/screens/lifecycle.jsx (DealAggregator).
 *
 * Custom 250px circular SVG gauge centered with worst-action chip,
 * calls-scored count, threshold. Banner at top describes lifecycle
 * status. Missing-calls chips when present. Per-call breakdown table.
 */
import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, AlertTriangle, CheckCircle2, Plus, ExternalLink } from "lucide-react";

import { ApiError } from "@/lib/api";
import {
  getDealDetailQuery,
  getDealVerdictQuery,
} from "@/lib/queries/aggregator";
import { useDealCompositeVerdictQuery } from "@/lib/queries/deals";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";
import { Pill, type PillTone } from "@/components/design/Pill";
import { UploadModal } from "@/app/(admin)/calls/UploadModal";
import { PHASE_LABEL } from "@/lib/workflow";

/** W1.1 (v3-watt-coverage): build the Watt portal deep-link URL. */
function wattPortalUrl(siteId: number | null | undefined): string | null {
  if (siteId == null || !Number.isFinite(siteId)) return null;
  return `https://api.wattutilities.co.uk:4433/sites/${siteId}`;
}

function actionTone(a: string | null | undefined): PillTone {
  switch ((a || "").toUpperCase()) {
    case "PASS":
      return "emerald";
    case "REVIEW":
      return "amber";
    case "COACHING":
      return "blue";
    case "FAIL":
      return "red";
    case "BLOCK":
      return "violet";
    default:
      return "neutral";
  }
}

// 2026-05-24 redesign — the standalone CircularGauge component was
// powered by the legacy `/api/deals/{id}/verdict` numbers and rendered
// a second composite gauge below the new composite-verdict block. Both
// blocks claimed the page heading "Composite" with different numbers
// (e.g. 60.1% vs 0%), which is exactly the user-reported confusion.
// The new single composite-verdict section above owns the gauge now.

export default function DealDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const detail = useQuery(getDealDetailQuery(id));
  const verdict = useQuery(getDealVerdictQuery(id));
  // Sprint Task B — composite Deal verdict (weighted-avg of call scores).
  const compositeQuery = useDealCompositeVerdictQuery(id);
  // 2026-05-24 — same Smart Upload CTA as /customers/[slug]; prefill the
  // supplier + customer name so the L7Form lands on the right deal lane.
  const [uploadOpen, setUploadOpen] = useState(false);

  // 2026-05-24 — code-reviewer HIGH-1 retro on 1be5452: this page was
  // shipping the +Upload CTA without subscribing to realtime, so the
  // verdict + missing-call chips went stale until 30s stale-time
  // expired. LAW_OF_ENTERPRISE_GRADE §4 makes realtime non-negotiable
  // when a page has user-triggered mutations. Matches the pattern at
  // /customers/[slug] (page.tsx:411-414).
  const realtimeKeys = useMemo(
    () => [
      ["deal", id],
      ["deal", id, "verdict"],
      ["deal", id, "calls"],
      ["admin", "deal", id, "composite-verdict"],
    ],
    [id],
  );
  useRealtimeInvalidate("calls", realtimeKeys);
  useRealtimeInvalidate("customer_deals", realtimeKeys);

  if (detail.isError || verdict.isError) {
    const e = (detail.error ?? verdict.error) as unknown;
    const msg =
      e instanceof ApiError ? `${e.status} ${e.body || e.message}` : e instanceof Error ? e.message : "Unknown";
    return (
      <div style={{ padding: 32, color: "var(--red)", fontSize: 13 }}>
        Couldn&apos;t load deal — {msg}
      </div>
    );
  }

  const isLoading = detail.isLoading || verdict.isLoading;
  const v = verdict.data;
  const d = detail.data;

  // 2026-05-24 redesign — the legacy `composite`/`tone`/`gaugeTone`
  // variables fed the duplicate big gauge that's been removed. The new
  // composite block (compositeQuery.data) computes its own state.
  const missing = v?.missing_calls ?? [];
  const breakdown = v?.call_breakdown ?? [];
  const totalExpected = breakdown.length + missing.length;
  const lifecycle = (v?.lifecycle_status ?? "in_progress").toLowerCase();
  const locked = lifecycle === "closed_done";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      {/* Top bar */}
      <div
        style={{
          padding: "16px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <Link
          href="/deals"
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
          Deals
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
          {d?.customer_name ?? id}
        </h1>
        <Pill tone="neutral" mono>
          {id.slice(0, 12)}
        </Pill>
        <Pill tone={locked ? "emerald" : "amber"} dot>
          {(lifecycle || "—").toUpperCase()}
        </Pill>
        <div style={{ flex: 1 }} />
        {(() => {
          // W1.2 (v3-watt-coverage): meter-count badge.
          const meterCount = (d?.meters?.length ?? 0);
          if (meterCount === 0) return null;
          const label =
            meterCount === 2
              ? "2 meters (dual fuel)"
              : `${meterCount} meter${meterCount === 1 ? "" : "s"}`;
          return (
            <span
              data-slot="meter-count"
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
                fontFamily: "var(--font-mono)",
                padding: "2px 6px",
                borderRadius: 3,
                background: "var(--bg-elev2)",
                border: "1px solid var(--border-subtle)",
              }}
            >
              {label}
            </span>
          );
        })()}
        {(() => {
          // W1.1 (v3-watt-coverage): Watt portal deep-link chip.
          const url = wattPortalUrl(d?.external_watt_site_id);
          if (!url) return null;
          return (
            <a
              href={url}
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
              title={`Open site ${d?.external_watt_site_id} in Watt portal`}
            >
              Watt portal <ExternalLink size={11} />
            </a>
          );
        })()}
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {d?.supplier ?? "—"}
          {d?.deal_value_gbp != null && ` · £${Math.round(d.deal_value_gbp).toLocaleString()}`}
          {d?.created_at && ` · created ${new Date(d.created_at).toLocaleDateString()}`}
        </span>
        {/* 2026-05-24 — Smart "+ Upload <next stage>" CTA in the top bar.
            Mirrors the customer-detail page so users get the same one-
            click flow whether they navigated to a customer or a deal. */}
        {!locked && missing.length > 0 && (
          <button
            type="button"
            onClick={() => setUploadOpen(true)}
            title={`Upload the ${
              PHASE_LABEL[missing[0] as keyof typeof PHASE_LABEL] ?? missing[0]
            } call to advance this deal`}
            style={{
              height: 28,
              padding: "0 12px",
              fontSize: 12,
              fontWeight: 500,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              background: "var(--amber-400)",
              color: "#1f1300",
              borderRadius: 6,
              border: "none",
              cursor: "pointer",
              fontFamily: "inherit",
              boxShadow: "var(--shadow-sm)",
            }}
            data-slot="deal-upload-next-stage"
          >
            <Plus size={12} />
            Upload{" "}
            {PHASE_LABEL[missing[0] as keyof typeof PHASE_LABEL] ?? missing[0]}
          </button>
        )}
      </div>

      <UploadModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        prefill={
          d?.customer_name ? { customer: { name: d.customer_name } } : undefined
        }
      />

      {/* Status banner */}
      {!isLoading && v && (
        <div
          style={{
            padding: "10px 24px",
            background: locked ? "var(--emerald-bg)" : "var(--amber-bg)",
            borderBottom: `1px solid ${locked ? "var(--emerald-border)" : "var(--amber-border)"}`,
            display: "flex",
            alignItems: "center",
            gap: 10,
            color: locked ? "var(--emerald-400)" : "var(--amber-400)",
            fontSize: 13,
            flexShrink: 0,
          }}
        >
          {locked ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
          <span style={{ fontWeight: 500 }}>
            {missing.length === 0
              ? "All required calls scored. Composite verdict locked."
              : `${missing.length} of ${totalExpected} required call${
                  missing.length === 1 ? "" : "s"
                } missing — composite verdict pending`}
          </span>
        </div>
      )}

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
        {/* Sprint Task B — composite Deal verdict donut + per-call breakdown.
            Weighted-avg of all calls in the deal; red below 80% threshold,
            green at/above. See backend/app/deals_composite.py.
            The block ALWAYS renders a card — loading skeleton, error
            fallback, or the donut/table — so this section is never
            silently absent from the page. */}
        {compositeQuery.isLoading && (
          <section
            data-slot="composite-verdict-loading"
            className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-6"
          >
            <div className="flex items-center gap-8">
              <div className="h-32 w-32 animate-pulse rounded-full bg-[var(--bg-elev2)]" />
              <div className="space-y-3">
                <div className="h-4 w-48 animate-pulse rounded bg-[var(--bg-elev2)]" />
                <div className="h-4 w-40 animate-pulse rounded bg-[var(--bg-elev2)]" />
                <div className="h-4 w-56 animate-pulse rounded bg-[var(--bg-elev2)]" />
              </div>
            </div>
            <p className="mt-4 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
              Loading composite verdict…
            </p>
          </section>
        )}
        {compositeQuery.isError && (
          <section
            data-slot="composite-verdict-error"
            role="alert"
            className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-6"
          >
            <div className="text-sm font-medium">
              Composite verdict unavailable
            </div>
            <p className="mt-1 text-[12.5px] text-[var(--text-muted)]">
              The deal-level rollup couldn&apos;t be computed.
              Per-call scores are still visible in the breakdown table below.
            </p>
          </section>
        )}
        {/* 2026-05-24 redesign — single composite verdict block.
            Previously this page rendered TWO independent gauges (one from
            /api/deals/{id}/composite-verdict counting only scored calls;
            another from /api/deals/{id}/verdict counting expected calls)
            with the same labels and different numbers — reviewers could
            not tell which was "the" verdict. The unified block now shows:
              • ONE big gauge tinted by overall state (pending / pass /
                at-risk / fail)
              • Three explicit KPIs (worst action · scored progress ·
                threshold)
              • An inline next-action callout when the deal is incomplete
                (the same Upload-next-phase CTA already in the top bar)
              • The per-call breakdown table integrates MISSING rows so
                the gap is visible in-line rather than in a separate
                "Missing required calls" section below.
        */}
        {compositeQuery.data && (() => {
          const data = compositeQuery.data;
          const pct = data.composite_pct;
          const scored = data.calls_scored;
          const totalForGauge = totalExpected || data.calls_total || scored;
          const allScored = scored === totalForGauge && totalForGauge > 0;
          // Deal state machine: PENDING (missing calls) → READY when all
          // scored → PASS / FAIL based on threshold + worst_action.
          const dealState: "pending" | "pass" | "at_risk" | "fail" =
            !allScored
              ? "pending"
              : data.worst_action === "FAIL"
                ? "fail"
                : data.threshold_met
                  ? "pass"
                  : "at_risk";
          const stateBadge: Record<typeof dealState, { label: string; tone: "amber" | "emerald" | "red"; }> = {
            pending:  { label: "Pending more calls", tone: "amber"   },
            pass:     { label: "PASS",                tone: "emerald" },
            at_risk:  { label: "At risk",             tone: "amber"   },
            fail:     { label: "FAIL",                tone: "red"     },
          };
          const ringColor =
            dealState === "pass"   ? "rgb(16 185 129)"
          : dealState === "fail"   ? "rgb(239 68 68)"
          :                          "rgb(245 158 11)";
          return (
            <section
              data-slot="composite-verdict"
              className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-6"
            >
              <div className="flex flex-wrap items-center gap-8">
                {/* Gauge */}
                <div className="relative h-36 w-36 shrink-0" aria-label={`Composite score ${pct ?? "pending"} percent`}>
                  <svg viewBox="0 0 100 100" className="-rotate-90">
                    <circle cx="50" cy="50" r="44" stroke="rgb(228 228 231)" strokeWidth="8" fill="none" />
                    <circle
                      cx="50"
                      cy="50"
                      r="44"
                      stroke={ringColor}
                      strokeWidth="8"
                      fill="none"
                      strokeDasharray={`${(pct ?? 0) * 2.76} 1000`}
                      strokeLinecap="round"
                    />
                  </svg>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-3xl font-semibold tabular-nums">
                      {pct == null ? "—" : `${pct}%`}
                    </span>
                    <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                      {allScored ? "composite" : "scored so far"}
                    </span>
                  </div>
                </div>

                {/* KPIs */}
                <div className="flex min-w-0 flex-1 flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <Pill tone={stateBadge[dealState].tone} dot>
                      {stateBadge[dealState].label}
                    </Pill>
                    <span className="text-[12px] text-[var(--text-muted)]">
                      worst action <strong className="text-[var(--text-primary)]">{data.worst_action ?? "—"}</strong>
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-4">
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-[var(--text-faint)]">Calls scored</div>
                      <div className="text-[18px] font-semibold tabular-nums text-[var(--text-primary)]">
                        {scored}<span className="text-[var(--text-muted)] text-[14px] font-normal"> / {totalForGauge}</span>
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-[var(--text-faint)]">Threshold</div>
                      <div className="text-[14px] tabular-nums text-[var(--text-primary)]">
                        ≥ {data.threshold_pct}%
                        <span className={data.threshold_met ? "text-emerald-500 ml-1" : "text-amber-500 ml-1"}>
                          · {allScored ? (data.threshold_met ? "met" : "not met") : "pending"}
                        </span>
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-[var(--text-faint)]">Avg of scored</div>
                      <div className="text-[14px] tabular-nums text-[var(--text-primary)]">
                        {pct == null ? "—" : `${pct}%`}
                      </div>
                    </div>
                  </div>

                  {/* Inline next-action — visible only when something to do */}
                  {!locked && missing.length > 0 && (
                    <div className="mt-2 flex items-center gap-3 rounded-md border border-amber-300/40 bg-amber-500/10 px-3 py-2 text-[12.5px] text-[var(--text-primary)]">
                      <AlertTriangle size={14} className="text-amber-500 shrink-0" aria-hidden />
                      <div className="flex-1">
                        Next step: upload the{" "}
                        <strong>
                          {missing
                            .map((m) => PHASE_LABEL[m as keyof typeof PHASE_LABEL] ?? m)
                            .join(", ")}
                        </strong>{" "}
                        call{missing.length === 1 ? "" : "s"} to finalise this deal.
                      </div>
                      <button
                        type="button"
                        onClick={() => setUploadOpen(true)}
                        className="inline-flex items-center gap-1 rounded-md bg-amber-500 px-2.5 py-1 text-[12px] font-medium text-[#1f1300] hover:bg-amber-400"
                      >
                        <Plus size={12} />
                        Upload {PHASE_LABEL[missing[0] as keyof typeof PHASE_LABEL] ?? missing[0]}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </section>
          );
        })()}

        {/* Per-call breakdown — integrates MISSING rows so the gap is
            visible inline; each MISSING row carries an Upload button so
            reviewers don't have to scan the page for an action. */}
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
              Per-call breakdown
            </h3>
            <Pill tone="neutral">{breakdown.length}</Pill>
            {missing.length > 0 && (
              <Pill tone="amber">{missing.length} missing</Pill>
            )}
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
                gridTemplateColumns: "130px 110px 1fr 100px 110px 130px",
                gap: 12,
                padding: "10px 20px",
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-elev3)",
              }}
            >
              {["Call type", "Status", "Phase", "Score", "When", ""].map((h) => (
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
              ))}
            </div>
            {breakdown.length === 0 && missing.length === 0 ? (
              <div
                style={{
                  padding: 32,
                  fontSize: 13,
                  color: "var(--text-muted)",
                  textAlign: "center",
                }}
              >
                No calls scored yet.
              </div>
            ) : (
              <>
                {breakdown.map((row, i) => {
                  const scorePct =
                    row.score_fraction != null
                      ? `${Math.round(row.score_fraction * 100)}%`
                      : row.score_raw ?? "—";
                  const isLast = i === breakdown.length - 1 && missing.length === 0;
                  return (
                    <div
                      key={row.call_id || i}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "130px 110px 1fr 100px 110px 130px",
                        gap: 12,
                        alignItems: "center",
                        padding: "12px 20px",
                        borderBottom: isLast ? "none" : "1px solid var(--border-subtle)",
                        fontSize: 13,
                      }}
                    >
                      <div>
                        <Pill tone="neutral">{row.call_type ?? "—"}</Pill>
                      </div>
                      <div>
                        <Pill tone={actionTone(row.action)} dot>
                          {row.action ?? "—"}
                        </Pill>
                      </div>
                      <div style={{ color: "var(--text-muted)" }}>{row.phase ?? "—"}</div>
                      <div
                        style={{
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-primary)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {scorePct}
                      </div>
                      <div style={{ color: "var(--text-muted)" }}>
                        {row.completed_at
                          ? new Date(row.completed_at).toLocaleDateString()
                          : "—"}
                      </div>
                      <div>
                        {row.call_id && (
                          <Link
                            href={`/calls/${row.call_id}`}
                            className="text-[12px] text-emerald-500 hover:underline"
                          >
                            Open →
                          </Link>
                        )}
                      </div>
                    </div>
                  );
                })}
                {missing.map((phase, i) => {
                  const label = PHASE_LABEL[phase as keyof typeof PHASE_LABEL] ?? phase;
                  const isLast = i === missing.length - 1;
                  return (
                    <div
                      key={`missing-${phase}`}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "130px 110px 1fr 100px 110px 130px",
                        gap: 12,
                        alignItems: "center",
                        padding: "12px 20px",
                        borderBottom: isLast ? "none" : "1px solid var(--border-subtle)",
                        fontSize: 13,
                        background: "var(--amber-bg)",
                      }}
                      data-slot="deal-missing-call-row"
                    >
                      <div>
                        <Pill tone="neutral">{label}</Pill>
                      </div>
                      <div>
                        <Pill tone="amber" dot>
                          MISSING
                        </Pill>
                      </div>
                      <div style={{ color: "var(--text-muted)" }}>—</div>
                      <div style={{ color: "var(--text-muted)" }}>—</div>
                      <div style={{ color: "var(--text-muted)" }}>—</div>
                      <div>
                        {!locked && (
                          <button
                            type="button"
                            onClick={() => setUploadOpen(true)}
                            className="inline-flex items-center gap-1 rounded-md bg-amber-500 px-2 py-0.5 text-[11.5px] font-medium text-[#1f1300] hover:bg-amber-400"
                            title={`Upload the ${label} call`}
                          >
                            <Plus size={11} /> Upload
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
