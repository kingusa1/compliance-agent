"use client";

/**
 * /agents/[name] — ported from
 * design/handoff-bundle/project/screens/ops.jsx (AgentDrilldown).
 *
 * Hero: back arrow + name + ESCALATE/OK pill + Retraining toggle.
 * 4 hero stat cards. 4 tabs (Recent flags · Open directives · Dead
 * rejections · Similar failures). Recent flags table by default.
 */
import { use, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";

import {
  getAgentDrilldownQuery,
  patchAgentRetraining,
} from "@/lib/queries/aggregator";
import { Pill } from "@/components/design/Pill";

type Tab = "flags" | "directives" | "rejections" | "similar";

export default function AgentDrilldownPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const decoded = decodeURIComponent(name);
  const qc = useQueryClient();
  const drill = useQuery(getAgentDrilldownQuery(decoded));
  const [tab, setTab] = useState<Tab>("flags");

  const data = drill.data;
  const flags = data?.dead_rejections ?? [];
  const recentCalls = data?.recent_calls ?? [];

  const retrainingMutation = useMutation({
    mutationFn: (next: boolean) =>
      patchAgentRetraining(decoded, { retraining_assigned: next }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", decoded, "drilldown"] });
      qc.invalidateQueries({ queryKey: ["agents", "list"] });
      toast.success("Retraining flag updated");
    },
    onError: (e) =>
      toast.error("Couldn't update retraining", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const escalate = (data?.critical_count_7d ?? 0) > 0 || (data?.open_directives ?? 0) > 2;
  const passRatePct =
    data?.pass_rate_30d != null ? Math.round(data.pass_rate_30d * 100) : null;
  const failedAtRiskGbp = data?.open_rejections_value_gbp;

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
          href="/agents"
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
          Agents
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
          {decoded}
        </h1>
        <Pill tone={escalate ? "red" : "emerald"} dot>
          {escalate ? "ESCALATE" : "OK"}
        </Pill>
        <div style={{ flex: 1 }} />
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
          <div
            onClick={(e) => {
              e.preventDefault();
              const next = !(data?.retraining_assigned ?? false);
              retrainingMutation.mutate(next);
            }}
            style={{
              width: 28,
              height: 16,
              borderRadius: 8,
              background: data?.retraining_assigned
                ? "var(--emerald)"
                : "var(--border-strong)",
              position: "relative",
              transition: "background 100ms",
            }}
          >
            <div
              style={{
                position: "absolute",
                top: 2,
                left: data?.retraining_assigned ? 14 : 2,
                width: 12,
                height: 12,
                borderRadius: 6,
                background: "#fff",
                transition: "left 120ms",
              }}
            />
          </div>
          <span style={{ fontSize: 13, color: "var(--text-primary)" }}>
            Retraining assigned
          </span>
        </label>
      </div>

      {/* Hero stats — 6 KPI cards (2026-05-27 quality-reviewer redesign) */}
      <div
        style={{
          padding: 24,
          borderBottom: "1px solid var(--border-subtle)",
          display: "grid",
          gridTemplateColumns: "repeat(6, 1fr)",
          gap: 12,
        }}
      >
        {[
          {
            label: "Total calls",
            value: data?.total_calls_lifetime ?? 0,
            sub: "lifetime",
            tone: "var(--text-primary)",
          },
          {
            label: "Pass rate",
            value: passRatePct != null ? `${passRatePct}%` : "—",
            sub: "30d window",
            tone:
              passRatePct == null
                ? "var(--text-muted)"
                : passRatePct >= 80
                  ? "var(--emerald)"
                  : passRatePct >= 50
                    ? "var(--amber)"
                    : "var(--red)",
          },
          {
            label: "Avg score",
            value:
              data?.avg_score_30d != null
                ? `${Math.round(data.avg_score_30d * 100)}%`
                : "—",
            sub: "30d avg",
            tone:
              data?.avg_score_30d == null
                ? "var(--text-muted)"
                : data.avg_score_30d >= 0.8
                  ? "var(--emerald)"
                  : data.avg_score_30d >= 0.5
                    ? "var(--amber)"
                    : "var(--red)",
          },
          {
            label: "Critical flags",
            value: data?.critical_count_7d ?? 0,
            sub: "last 7 days",
            tone: "var(--red)",
          },
          {
            label: "Open directives",
            value: data?.open_directives ?? 0,
            sub: "outstanding",
            tone: "var(--amber)",
          },
          {
            label: "QC blocks",
            value: data?.qc_block_count_30d ?? 0,
            sub: "auditor 30d",
            tone:
              (data?.qc_block_count_30d ?? 0) > 0 ? "var(--red)" : "var(--text-primary)",
          },
        ].map((s) => (
          <div
            key={s.label}
            style={{
              flex: 1,
              padding: 16,
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 8,
            }}
          >
            <div
              style={{
                fontSize: 11,
                color: "var(--text-faint)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 8,
              }}
            >
              {s.label}
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <div
                style={{
                  fontSize: 26,
                  fontWeight: 600,
                  color: s.tone,
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.01em",
                }}
              >
                {s.value}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{s.sub}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Quality-reviewer breakdown panels (2026-05-27).
          One row of 4 dense cards: weekly trend, severity, top-failed, mix.
          Hidden when data is loading; degrades gracefully on missing fields. */}
      {data && (
        <div
          style={{
            padding: "16px 24px",
            borderBottom: "1px solid var(--border-subtle)",
            display: "grid",
            gridTemplateColumns: "1.2fr 1fr 1.4fr 1fr",
            gap: 12,
          }}
        >
          <BreakdownCard title="Pass rate trend · 8w">
            <WeeklySparkline trend={data.weekly_trend ?? []} />
          </BreakdownCard>
          <BreakdownCard title="Breach severity · 30d">
            <SeverityBars sev={data.severity_breakdown_30d ?? null} />
          </BreakdownCard>
          <BreakdownCard title="Top failed checkpoints · 30d">
            <TopFailedList items={data.top_failed_checkpoints_30d ?? []} />
          </BreakdownCard>
          <BreakdownCard title="Mix · 30d">
            <MixBars
              supplierMix={data.supplier_mix_30d ?? {}}
              callTypeMix={data.call_type_mix_30d ?? {}}
            />
          </BreakdownCard>
        </div>
      )}

      {/* Best / worst call quick-jumps */}
      {data && (data.best_call_id || data.worst_call_id) && (
        <div
          style={{
            padding: "10px 24px",
            borderBottom: "1px solid var(--border-subtle)",
            display: "flex",
            gap: 12,
            fontSize: 12,
            color: "var(--text-muted)",
          }}
        >
          {data.best_call_id && (
            <Link
              href={`/calls/${data.best_call_id}`}
              style={{
                color: "var(--emerald)",
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              ★ Best recent call → {data.best_call_id.slice(0, 8)}
            </Link>
          )}
          {data.worst_call_id && (
            <Link
              href={`/calls/${data.worst_call_id}`}
              style={{
                color: "var(--red)",
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              ⚠ Worst recent call → {data.worst_call_id.slice(0, 8)}
            </Link>
          )}
          {data.retraining_assigned && data.retraining_reason && (
            <div style={{ marginLeft: "auto", color: "var(--amber)" }}>
              Coaching: {data.retraining_reason.slice(0, 80)}
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--border-subtle)",
          paddingLeft: 16,
          flexShrink: 0,
        }}
      >
        {(
          [
            { key: "flags", label: "Recent calls", count: recentCalls.length },
            { key: "directives", label: "Open directives", count: data?.open_directives ?? 0 },
            { key: "rejections", label: "Dead rejections", count: flags.length },
            { key: "similar", label: "Similar failures", count: 0 },
          ] as { key: Tab; label: string; count: number }[]
        ).map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                padding: "12px 14px",
                fontSize: 13,
                fontWeight: 500,
                color: active ? "var(--text-primary)" : "var(--text-muted)",
                borderBottom: `2px solid ${active ? "var(--emerald)" : "transparent"}`,
                marginBottom: -1,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 6,
                background: "transparent",
                border: "none",
                fontFamily: "inherit",
              }}
            >
              {t.label}
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {t.count}
              </span>
            </button>
          );
        })}
      </div>

      <div style={{ flex: 1, overflowY: "auto" }} className="ca-scroll">
        {tab === "flags" ? (
          <RecentCallsTable calls={recentCalls} />
        ) : tab === "rejections" ? (
          <DeadRejectionsTable flags={flags} />
        ) : (
          <div style={{ padding: 32, fontSize: 13, color: "var(--text-muted)", textAlign: "center" }}>
            {tab === "directives" ? "Directives view — coming soon." : "Similar-failures view — coming soon."}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Tab content components ─────────────────────────────────────────

import type { AgentRecentCall, AgentDeadRejection } from "@/lib/queries/aggregator";
import { formatCustomerName } from "@/lib/customer";

const COLS_RECENT = "120px 1.4fr 1.2fr 90px 110px 110px";
const HEADERS_RECENT = ["When", "Customer", "Supplier", "Score", "Status", "Call ID"];

function RecentCallsTable({ calls }: { calls: AgentRecentCall[] }) {
  return (
    <>
      <TableHeaderRow cols={COLS_RECENT} headers={HEADERS_RECENT} />
      {calls.length === 0 ? (
        <EmptyRow text="No calls for this agent yet." />
      ) : (
        calls.map((c) => (
          <Link
            key={c.id}
            href={`/calls/${c.id}`}
            style={{
              display: "grid",
              gridTemplateColumns: COLS_RECENT,
              gap: 12,
              alignItems: "center",
              padding: "12px 24px",
              borderBottom: "1px solid var(--border-subtle)",
              fontSize: 13,
              color: "var(--text-primary)",
              textDecoration: "none",
              cursor: "pointer",
            }}
            className="ca-row-hover"
          >
            <div style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
              {c.created_at ? new Date(c.created_at).toLocaleDateString() : "—"}
            </div>
            <div>{formatCustomerName(c.customer_name)}</div>
            <div style={{ color: "var(--text-muted)" }}>{c.detected_supplier ?? "—"}</div>
            <div style={{ fontVariantNumeric: "tabular-nums" }}>{c.score ?? "—"}</div>
            <div>
              {/* 2026-05-24 — was a binary `c.compliant ? emerald : red`,
                  which rendered every still-processing / unscored call
                  (compliant === null) as a red "non-compliant" pill.
                  Tri-state respects the pending state. */}
              {c.compliant === true ? (
                <Pill tone="emerald" dot>compliant</Pill>
              ) : c.compliant === false ? (
                <Pill tone="red" dot>non-compliant</Pill>
              ) : (
                <Pill tone="neutral" dot>pending</Pill>
              )}
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-faint)" }}>
              {c.id.slice(0, 8)}
            </div>
          </Link>
        ))
      )}
    </>
  );
}

const COLS_DEAD = "100px 90px 1.4fr 1.6fr 110px 110px";
const HEADERS_DEAD = ["When", "Severity", "Rule", "Customer", "Fix Status", "Deal"];

function DeadRejectionsTable({ flags }: { flags: AgentDeadRejection[] }) {
  return (
    <>
      <TableHeaderRow cols={COLS_DEAD} headers={HEADERS_DEAD} />
      {flags.length === 0 ? (
        <EmptyRow text="No dead rejections in this window." />
      ) : (
        flags.map((r, i) => (
          <div
            key={`${r.deal_id}-${i}`}
            style={{
              display: "grid",
              gridTemplateColumns: COLS_DEAD,
              gap: 12,
              alignItems: "center",
              padding: "12px 24px",
              borderBottom: "1px solid var(--border-subtle)",
              fontSize: 13,
            }}
          >
            <div style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
              {r.rejected_at ? new Date(r.rejected_at).toLocaleDateString() : "—"}
            </div>
            <div>
              <Pill tone="red" dot>HIGH</Pill>
            </div>
            <div style={{ color: "var(--text-primary)" }}>{r.dead_reason ?? "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>{formatCustomerName(r.customer_name)}</div>
            <div>
              <Pill tone="amber" dot>open</Pill>
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--red)" }}>
              {r.deal_id.slice(0, 8)}
            </div>
          </div>
        ))
      )}
    </>
  );
}

function TableHeaderRow({ cols, headers }: { cols: string; headers: string[] }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: cols,
        gap: 12,
        padding: "10px 24px",
        borderBottom: "1px solid var(--border-subtle)",
        background: "var(--bg-elev1)",
      }}
    >
      {headers.map((h) => (
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
  );
}

function EmptyRow({ text }: { text: string }) {
  return (
    <div style={{ padding: 32, fontSize: 13, color: "var(--text-muted)", textAlign: "center" }}>
      {text}
    </div>
  );
}

// ── Breakdown panels (2026-05-27 quality-reviewer redesign) ──────────

function BreakdownCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        background: "var(--bg-elev2)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 8,
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minHeight: 120,
      }}
    >
      <div
        style={{
          fontSize: 11,
          color: "var(--text-faint)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}
      >
        {title}
      </div>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

import type { AgentWeeklyTrendPoint, AgentTopFailedCheckpoint } from "@/lib/queries/aggregator";

function WeeklySparkline({ trend }: { trend: AgentWeeklyTrendPoint[] }) {
  // Inline SVG line+dots — no chart lib. Empty trend → muted hint.
  const pts = trend.filter((p) => p.total > 0);
  if (pts.length === 0) {
    return (
      <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
        No calls in the last 8 weeks.
      </div>
    );
  }
  const W = 220;
  const H = 70;
  const padX = 6;
  const padY = 8;
  // X coords map to the original 8-week buckets (even when some are empty)
  const N = Math.max(trend.length, 8);
  const stepX = N > 1 ? (W - 2 * padX) / (N - 1) : 0;
  const polyPoints = trend
    .map((p, i) => {
      const r = p.pass_rate;
      if (r == null) return null;
      const x = padX + i * stepX;
      const y = padY + (1 - r) * (H - 2 * padY);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  const latest = pts[pts.length - 1];
  const latestPct = latest && latest.pass_rate != null ? Math.round(latest.pass_rate * 100) : null;
  return (
    <div>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
        {/* baseline 50% guide */}
        <line
          x1={padX}
          y1={padY + 0.5 * (H - 2 * padY)}
          x2={W - padX}
          y2={padY + 0.5 * (H - 2 * padY)}
          stroke="var(--border-subtle)"
          strokeDasharray="2 3"
        />
        <polyline
          points={polyPoints}
          fill="none"
          stroke="var(--emerald)"
          strokeWidth="2"
        />
        {trend.map((p, i) => {
          if (p.pass_rate == null) return null;
          const x = padX + i * stepX;
          const y = padY + (1 - p.pass_rate) * (H - 2 * padY);
          return (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={2.5}
              fill={
                p.pass_rate >= 0.8
                  ? "var(--emerald)"
                  : p.pass_rate >= 0.5
                    ? "var(--amber)"
                    : "var(--red)"
              }
            />
          );
        })}
      </svg>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
        Latest week: {latestPct != null ? `${latestPct}% pass` : "no data"} · {pts.length} of {trend.length} weeks active
      </div>
    </div>
  );
}

function SeverityBars({ sev }: { sev: { critical: number; high: number; medium: number; low: number } | null }) {
  if (!sev) {
    return <div style={{ fontSize: 12, color: "var(--text-faint)" }}>No data.</div>;
  }
  const rows: { label: string; n: number; tone: string }[] = [
    { label: "Critical", n: sev.critical, tone: "var(--red)" },
    { label: "High", n: sev.high, tone: "#f97316" },
    { label: "Medium", n: sev.medium, tone: "var(--amber)" },
    { label: "Low", n: sev.low, tone: "var(--text-muted)" },
  ];
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map((r) => (
        <div
          key={r.label}
          style={{ display: "grid", gridTemplateColumns: "60px 1fr 28px", gap: 6, alignItems: "center" }}
        >
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{r.label}</div>
          <div style={{ height: 8, background: "var(--bg-elev1)", borderRadius: 4, overflow: "hidden" }}>
            <div
              style={{
                width: `${(r.n / max) * 100}%`,
                height: "100%",
                background: r.tone,
                transition: "width 200ms",
              }}
            />
          </div>
          <div
            style={{
              fontSize: 12,
              fontVariantNumeric: "tabular-nums",
              color: "var(--text-primary)",
              textAlign: "right",
            }}
          >
            {r.n}
          </div>
        </div>
      ))}
    </div>
  );
}

function TopFailedList({ items }: { items: AgentTopFailedCheckpoint[] }) {
  if (items.length === 0) {
    return <div style={{ fontSize: 12, color: "var(--text-faint)" }}>None — clean record.</div>;
  }
  const max = Math.max(1, ...items.map((i) => i.count));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {items.map((it) => (
        <div
          key={it.name}
          style={{ display: "grid", gridTemplateColumns: "1fr 60px 28px", gap: 6, alignItems: "center" }}
        >
          <div
            style={{
              fontSize: 12,
              color: "var(--text-primary)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={it.name}
          >
            {it.name}
          </div>
          <div style={{ height: 6, background: "var(--bg-elev1)", borderRadius: 3, overflow: "hidden" }}>
            <div
              style={{
                width: `${(it.count / max) * 100}%`,
                height: "100%",
                background: "var(--red)",
                transition: "width 200ms",
              }}
            />
          </div>
          <div
            style={{
              fontSize: 12,
              fontVariantNumeric: "tabular-nums",
              color: "var(--text-muted)",
              textAlign: "right",
            }}
          >
            {it.count}
          </div>
        </div>
      ))}
    </div>
  );
}

function MixBars({
  supplierMix,
  callTypeMix,
}: {
  supplierMix: Record<string, number>;
  callTypeMix: Record<string, number>;
}) {
  const supEntries = Object.entries(supplierMix);
  const ctEntries = Object.entries(callTypeMix);
  if (supEntries.length === 0 && ctEntries.length === 0) {
    return <div style={{ fontSize: 12, color: "var(--text-faint)" }}>No 30-day calls.</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <MixStackedBar label="Supplier" entries={supEntries} />
      <MixStackedBar label="Call type" entries={ctEntries} />
    </div>
  );
}

function MixStackedBar({ label, entries }: { label: string; entries: [string, number][] }) {
  const total = entries.reduce((s, [, n]) => s + n, 0);
  const palette = ["#22c55e", "#f59e0b", "#ef4444", "#3b82f6", "#a855f7", "#64748b"];
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-faint)", marginBottom: 4 }}>{label}</div>
      <div style={{ display: "flex", height: 10, borderRadius: 3, overflow: "hidden", background: "var(--bg-elev1)" }}>
        {total > 0
          ? entries.map(([k, n], i) => (
              <div
                key={k}
                title={`${k}: ${n}`}
                style={{
                  width: `${(n / total) * 100}%`,
                  background: palette[i % palette.length],
                }}
              />
            ))
          : null}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
        {entries.slice(0, 4).map(([k, n], i) => (
          <div
            key={k}
            style={{
              fontSize: 10,
              color: "var(--text-muted)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: 2,
                background: palette[i % palette.length],
              }}
            />
            {k} · {n}
          </div>
        ))}
      </div>
    </div>
  );
}
