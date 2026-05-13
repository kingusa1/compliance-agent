"use client";

/**
 * /dashboard — Intelligence panel (Plan §5f).
 *
 * Four cards pulled from /api/intelligence/*:
 *
 *   1) Compliance % by supplier   — bar chart
 *   2) Top-10 agents by % compliant — table
 *   3) Calls by call_type        — donut
 *   4) 30-day compliance trend   — line chart
 *
 * Charts are hand-rolled SVG so we don't add a charting library for one
 * panel. The look matches the rest of the dashboard (--bg-elev1 cards,
 * emerald accents).
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

type BySupplierItem = {
  supplier: string;
  total: number;
  compliant: number;
  compliance_pct: number;
};

type ByAgentItem = {
  agent: string;
  total: number;
  compliant: number;
  compliance_pct: number;
};

type ByCallTypeItem = {
  call_type: string;
  total: number;
};

type TrendItem = {
  label: string;
  total: number;
  compliant: number;
  compliance_pct: number;
};

type Wrap<T> = { items: T[] };

const CARD_CLASS =
  "rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4";

function Card({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={CARD_CLASS}>
      <div className="flex items-baseline justify-between">
        <div className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
          {title}
        </div>
        {hint ? (
          <div className="text-[10.5px] text-[var(--text-faint)]">{hint}</div>
        ) : null}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div
      style={{
        padding: "32px 8px",
        textAlign: "center",
        color: "var(--text-faint)",
        fontSize: 12,
      }}
    >
      {message}
    </div>
  );
}

// ── Card 1: Compliance % by supplier ─────────────────────────────────

function SupplierBars() {
  const q = useQuery({
    queryKey: ["intelligence:by-supplier"] as const,
    queryFn: () => apiFetch<Wrap<BySupplierItem>>("/api/intelligence/by-supplier"),
    staleTime: 60_000,
  });
  const items = q.data?.items ?? [];
  if (q.isLoading) return <EmptyState message="Loading…" />;
  if (!items.length) return <EmptyState message="No completed calls yet." />;
  const maxTotal = Math.max(...items.map((i) => i.total));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {items.slice(0, 8).map((it) => {
        const widthPct = Math.max(2, Math.round((it.total / maxTotal) * 100));
        const tone =
          it.compliance_pct >= 85
            ? "var(--emerald)"
            : it.compliance_pct >= 60
              ? "var(--amber)"
              : "var(--red)";
        return (
          <div
            key={it.supplier}
            style={{ display: "grid", gridTemplateColumns: "120px 1fr 60px", gap: 8, alignItems: "center" }}
          >
            <div
              style={{
                fontSize: 12,
                color: "var(--text-primary)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={it.supplier}
            >
              {it.supplier}
            </div>
            <div
              style={{
                height: 8,
                borderRadius: 4,
                background: "var(--bg-elev3)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${widthPct}%`,
                  background: tone,
                }}
              />
            </div>
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--text-primary)",
                textAlign: "right",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {it.compliance_pct}%
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Card 2: Top-10 agents ────────────────────────────────────────────

function AgentLeaderboard() {
  const q = useQuery({
    queryKey: ["intelligence:by-agent"] as const,
    queryFn: () => apiFetch<Wrap<ByAgentItem>>("/api/intelligence/by-agent?limit=10"),
    staleTime: 60_000,
  });
  const items = q.data?.items ?? [];
  if (q.isLoading) return <EmptyState message="Loading…" />;
  if (!items.length)
    return (
      <EmptyState message="No agents with ≥3 completed calls yet." />
    );

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {items.map((it, i) => (
        <div
          key={it.agent}
          style={{
            display: "grid",
            gridTemplateColumns: "20px 1fr 60px 60px",
            alignItems: "center",
            gap: 8,
            padding: "6px 0",
            borderBottom:
              i === items.length - 1 ? "none" : "1px solid var(--border-subtle)",
            fontSize: 12,
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--text-faint)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {i + 1}
          </div>
          <div
            style={{
              color: "var(--text-primary)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={it.agent}
          >
            {it.agent}
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--text-faint)",
              textAlign: "right",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {it.total}
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--emerald)",
              textAlign: "right",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {it.compliance_pct}%
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Card 3: Call-type donut ──────────────────────────────────────────

const CALL_TYPE_TONE: Record<string, string> = {
  lead_gen: "var(--emerald)",
  pre_sales: "var(--blue)",
  verbal: "var(--amber)",
  loa: "var(--violet)",
  Unclassified: "var(--text-faint)",
};

const CALL_TYPE_LABEL: Record<string, string> = {
  lead_gen: "Lead Gen",
  pre_sales: "Pre-Sales",
  verbal: "Verbal",
  loa: "LOA",
};

function CallTypeDonut() {
  const q = useQuery({
    queryKey: ["intelligence:by-call-type"] as const,
    queryFn: () =>
      apiFetch<Wrap<ByCallTypeItem>>("/api/intelligence/by-call-type"),
    staleTime: 60_000,
  });
  const items = q.data?.items ?? [];
  const total = items.reduce((acc, x) => acc + x.total, 0);
  if (q.isLoading) return <EmptyState message="Loading…" />;
  if (!total) return <EmptyState message="No completed calls yet." />;

  // SVG donut math
  const size = 140;
  const stroke = 18;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  let cumulative = 0;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Call type breakdown">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--bg-elev3)"
          strokeWidth={stroke}
        />
        {items.map((it) => {
          const frac = it.total / total;
          const dash = frac * circumference;
          const offset = -cumulative * circumference;
          cumulative += frac;
          const color = CALL_TYPE_TONE[it.call_type] ?? "var(--text-muted)";
          const arc = (
            <circle
              key={it.call_type}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={color}
              strokeWidth={stroke}
              strokeDasharray={`${dash} ${circumference - dash}`}
              strokeDashoffset={offset}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
            />
          );
          return arc;
        })}
        <text
          x={size / 2}
          y={size / 2}
          textAnchor="middle"
          dominantBaseline="middle"
          fontSize="20"
          fontFamily="var(--font-mono)"
          fill="var(--text-primary)"
          fontWeight="600"
        >
          {total}
        </text>
      </svg>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
        {items.map((it) => {
          const color = CALL_TYPE_TONE[it.call_type] ?? "var(--text-muted)";
          const label = CALL_TYPE_LABEL[it.call_type] ?? it.call_type;
          const pct = Math.round((it.total / total) * 100);
          return (
            <div
              key={it.call_type}
              style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}
            >
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: color,
                  flexShrink: 0,
                }}
              />
              <span style={{ color: "var(--text-primary)", flex: 1 }}>{label}</span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-faint)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {it.total} ({pct}%)
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Card 4: 30-day trend ─────────────────────────────────────────────

function TrendLine() {
  const q = useQuery({
    queryKey: ["intelligence:trend"] as const,
    queryFn: () => apiFetch<Wrap<TrendItem>>("/api/intelligence/trend?days=30"),
    staleTime: 60_000,
  });
  const items = q.data?.items ?? [];
  if (q.isLoading) return <EmptyState message="Loading…" />;
  if (items.length < 2)
    return (
      <EmptyState message="Need at least 2 weeks of data to draw a trend." />
    );

  const width = 320;
  const height = 100;
  const padX = 8;
  const padY = 12;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const maxIdx = items.length - 1;
  const points = items.map((it, i) => {
    const x = padX + (maxIdx ? (i / maxIdx) * innerW : innerW / 2);
    const y = padY + (1 - it.compliance_pct / 100) * innerH;
    return { x, y, label: it.label, pct: it.compliance_pct, total: it.total };
  });
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");

  return (
    <div>
      <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="30-day compliance trend">
        {/* y-axis gridlines at 0/50/100 % */}
        {[0, 50, 100].map((v) => {
          const y = padY + (1 - v / 100) * innerH;
          return (
            <line
              key={v}
              x1={padX}
              x2={width - padX}
              y1={y}
              y2={y}
              stroke="var(--border-subtle)"
              strokeDasharray="2 4"
            />
          );
        })}
        <path d={path} fill="none" stroke="var(--emerald)" strokeWidth="2" />
        {points.map((p) => (
          <circle
            key={`${p.x}-${p.y}`}
            cx={p.x}
            cy={p.y}
            r={3}
            fill="var(--emerald)"
            stroke="var(--bg-elev1)"
            strokeWidth="1"
          >
            <title>
              {p.label}: {p.pct}% ({p.total} calls)
            </title>
          </circle>
        ))}
      </svg>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 10,
          color: "var(--text-faint)",
          fontFamily: "var(--font-mono)",
          marginTop: 4,
        }}
      >
        <span>{items[0]?.label}</span>
        <span>{items[items.length - 1]?.label}</span>
      </div>
    </div>
  );
}

// ── Top-level export ────────────────────────────────────────────────

export function IntelligencePanel() {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-[16px] font-semibold tracking-tight text-[var(--text-primary)]">
          Intelligence
        </h2>
        <p className="text-[12px] text-[var(--text-muted)]">
          Compliance shape — by supplier, agent, call_type, and trend.
        </p>
      </div>
      <div
        className="grid gap-4"
        style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}
      >
        <Card title="Compliance % by supplier" hint="Bar = call volume; tone = % compliant">
          <SupplierBars />
        </Card>
        <Card title="Top agents by compliance" hint="Min 3 calls">
          <AgentLeaderboard />
        </Card>
        <Card title="Calls by type">
          <CallTypeDonut />
        </Card>
        <Card title="30-day compliance trend" hint="Weekly buckets">
          <TrendLine />
        </Card>
      </div>
    </section>
  );
}
