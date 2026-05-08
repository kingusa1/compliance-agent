"use client";

/**
 * /deals/[id] — ported from
 * design/handoff-bundle/project/screens/lifecycle.jsx (DealAggregator).
 *
 * Custom 250px circular SVG gauge centered with worst-action chip,
 * calls-scored count, threshold. Banner at top describes lifecycle
 * status. Missing-calls chips when present. Per-call breakdown table.
 */
import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, AlertTriangle, CheckCircle2, Plus, ExternalLink } from "lucide-react";

import { ApiError } from "@/lib/api";
import {
  getDealDetailQuery,
  getDealVerdictQuery,
} from "@/lib/queries/aggregator";
import { useDealCompositeVerdictQuery } from "@/lib/queries/deals";
import { Pill, type PillTone } from "@/components/design/Pill";

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

function CircularGauge({
  value,
  tone,
  size = 250,
}: {
  value: number;
  tone: "emerald" | "amber" | "red";
  size?: number;
}) {
  const stroke = 16;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - value / 100);
  const color =
    tone === "emerald" ? "var(--emerald)" : tone === "red" ? "var(--red)" : "var(--amber)";

  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--bg-elev3)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeDasharray={c}
          strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: "stroke-dashoffset 600ms ease" }}
        />
      </svg>
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            fontSize: 56,
            fontWeight: 600,
            color: "var(--text-primary)",
            fontVariantNumeric: "tabular-nums",
            letterSpacing: "-0.02em",
            lineHeight: 1,
          }}
        >
          {value}
          <span
            style={{
              fontSize: 24,
              color: "var(--text-muted)",
              fontWeight: 500,
            }}
          >
            %
          </span>
        </div>
        <div
          style={{
            fontSize: 12,
            color: "var(--text-faint)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginTop: 8,
          }}
        >
          Composite
        </div>
      </div>
    </div>
  );
}

export default function DealDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const detail = useQuery(getDealDetailQuery(id));
  const verdict = useQuery(getDealVerdictQuery(id));
  // Sprint Task B — composite Deal verdict (weighted-avg of call scores).
  const compositeQuery = useDealCompositeVerdictQuery(id);

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

  // Backend returns composite_score as 0-100 (e.g. 83.33), not 0-1.
  const composite = Math.round(v?.composite_score ?? 0);
  const worst = v?.worst_action ?? "—";
  const tone = actionTone(worst) as "emerald" | "amber" | "red" | "blue" | "violet" | "neutral";
  const gaugeTone: "emerald" | "amber" | "red" =
    tone === "emerald" ? "emerald" : tone === "red" || tone === "violet" ? "red" : "amber";

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
      </div>

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
            green at/above. See backend/app/deals_composite.py. */}
        {compositeQuery.data && (
          <section
            data-slot="composite-verdict"
            className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-6"
          >
            <div className="flex items-center gap-8">
              <div className="relative h-32 w-32">
                <svg viewBox="0 0 100 100" className="-rotate-90">
                  <circle
                    cx="50"
                    cy="50"
                    r="44"
                    stroke="rgb(228 228 231)"
                    strokeWidth="8"
                    fill="none"
                  />
                  <circle
                    cx="50"
                    cy="50"
                    r="44"
                    stroke={
                      compositeQuery.data.threshold_met
                        ? "rgb(16 185 129)"
                        : "rgb(239 68 68)"
                    }
                    strokeWidth="8"
                    fill="none"
                    strokeDasharray={`${(compositeQuery.data.composite_pct ?? 0) * 2.76} 1000`}
                  />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-3xl font-semibold">
                    {compositeQuery.data.composite_pct ?? "—"}%
                  </span>
                  <span className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                    composite
                  </span>
                </div>
              </div>
              <div className="space-y-2 text-sm">
                <div>
                  <strong>Worst action:</strong> {compositeQuery.data.worst_action}
                </div>
                <div>
                  <strong>Calls scored:</strong>{" "}
                  {compositeQuery.data.calls_scored} / {compositeQuery.data.calls_total}
                </div>
                <div>
                  <strong>Threshold:</strong> ≥ {compositeQuery.data.threshold_pct}%
                  {" · "}
                  {compositeQuery.data.threshold_met ? "met" : "not met"}
                </div>
              </div>
            </div>
            <h3 className="mt-6 text-sm font-medium">Per-call breakdown</h3>
            <table className="mt-2 w-full text-sm">
              <thead>
                <tr className="text-left text-[var(--text-muted)]">
                  <th>Call type</th>
                  <th>Status</th>
                  <th>Agent</th>
                  <th>Score</th>
                  <th>Weight</th>
                </tr>
              </thead>
              <tbody>
                {compositeQuery.data.per_call.map((c) => (
                  <tr key={c.id}>
                    <td>{c.call_type}</td>
                    <td>{c.status}</td>
                    <td>{c.agent ?? "—"}</td>
                    <td>{c.score == null ? "—" : `${c.score.toFixed(0)}%`}</td>
                    <td>{c.weight}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* Big gauge card */}
        <div
          style={{
            padding: 32,
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 10,
            display: "flex",
            alignItems: "center",
            gap: 40,
          }}
        >
          {isLoading ? (
            <div style={{ width: 250, height: 250 }} />
          ) : (
            <CircularGauge value={composite} tone={gaugeTone} />
          )}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 4,
                }}
              >
                Worst action
              </div>
              <Pill tone={actionTone(worst)} style={{ fontSize: 14, padding: "4px 12px" }}>
                {worst}
              </Pill>
            </div>
            <div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 6,
                }}
              >
                Calls scored
              </div>
              <div
                style={{
                  fontSize: 22,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {breakdown.length}{" "}
                <span style={{ color: "var(--text-muted)", fontSize: 16, fontWeight: 500 }}>
                  / {totalExpected || breakdown.length}
                </span>
              </div>
            </div>
            <div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 6,
                }}
              >
                Threshold
              </div>
              <div style={{ fontSize: 13, color: "var(--text-primary)" }}>
                {locked ? (
                  <>
                    ≥ 80% · <span style={{ color: "var(--emerald)" }}>met</span>
                  </>
                ) : (
                  <span style={{ color: "var(--amber)" }}>≥ 80% · pending all calls</span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Missing calls — only when not locked */}
        {missing.length > 0 && (
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
                Missing required calls
              </h3>
              <Pill tone="amber">{missing.length}</Pill>
              <span style={{ fontSize: 12, color: "var(--text-faint)" }}>· click to upload</span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {missing.map((name) => (
                <button
                  key={name}
                  type="button"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "10px 14px",
                    background: "var(--amber-bg)",
                    border: "1px solid var(--amber-border)",
                    borderRadius: 8,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  <div
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: 12,
                      background: "var(--amber-bg)",
                      display: "grid",
                      placeItems: "center",
                      color: "var(--amber)",
                    }}
                  >
                    <Plus size={14} />
                  </div>
                  <div style={{ textAlign: "left" }}>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 500,
                        color: "var(--amber-400)",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {name}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>missing</div>
                  </div>
                  <span style={{ color: "var(--amber)", marginLeft: 4 }}>→</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Per-call breakdown */}
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
                gridTemplateColumns: "130px 110px 1fr 100px 100px 110px",
                gap: 12,
                padding: "10px 20px",
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-elev3)",
              }}
            >
              {["Call type", "Status", "Phase", "Score", "Action", "When"].map((h) => (
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
            {breakdown.length === 0 ? (
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
              breakdown.map((row, i) => {
                const scorePct =
                  row.score_fraction != null
                    ? `${Math.round(row.score_fraction * 100)}%`
                    : row.score_raw ?? "—";
                return (
                  <div
                    key={row.call_id || i}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "130px 110px 1fr 100px 100px 110px",
                      gap: 12,
                      alignItems: "center",
                      padding: "12px 20px",
                      borderBottom:
                        i < breakdown.length - 1 ? "1px solid var(--border-subtle)" : "none",
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
                    <div
                      style={{
                        color: "var(--text-muted)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {row.action ?? "—"}
                    </div>
                    <div style={{ color: "var(--text-muted)" }}>
                      {row.completed_at
                        ? new Date(row.completed_at).toLocaleDateString()
                        : "—"}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
