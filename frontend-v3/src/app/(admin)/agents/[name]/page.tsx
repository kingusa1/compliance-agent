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

      {/* Hero stats */}
      <div
        style={{
          padding: 24,
          borderBottom: "1px solid var(--border-subtle)",
          display: "flex",
          gap: 12,
        }}
      >
        {[
          {
            label: "Total flagged",
            value: data?.critical_count_7d ?? 0,
            sub: "last 7 days",
            tone: "var(--red)",
          },
          {
            label: "Pass rate",
            value: passRatePct != null ? `${passRatePct}%` : "—",
            sub: "30d window",
            tone: "var(--amber)",
          },
          {
            label: "Open directives",
            value: data?.open_directives ?? 0,
            sub: "outstanding",
            tone: "var(--amber)",
          },
          {
            label: "Failed at risk",
            value:
              failedAtRiskGbp != null
                ? `£${(failedAtRiskGbp / 1000).toFixed(0)}k`
                : "—",
            sub: "across deals",
            tone: "var(--red)",
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
            <div>{c.customer_name ?? "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>{c.detected_supplier ?? "—"}</div>
            <div style={{ fontVariantNumeric: "tabular-nums" }}>{c.score ?? "—"}</div>
            <div>
              <Pill tone={c.compliant ? "emerald" : "red"} dot>
                {c.compliant ? "compliant" : "non-compliant"}
              </Pill>
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
            <div style={{ color: "var(--text-muted)" }}>{r.customer_name ?? "—"}</div>
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
