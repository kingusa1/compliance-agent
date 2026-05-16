"use client";

/**
 * /agents — ported from
 * design/handoff-bundle/project/screens/ops.jsx (AgentsScreen).
 *
 * Top bar: H1 + 9-agents pill + status filter dropdown + search.
 * 8-col density table: Agent | Total | Compliant | Non-compliant
 * | Flags | Directives | Last Call | Status pill (OK / ESCALATE).
 * Click row → /agents/[name].
 */
import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";

import { getAgentsListQuery } from "@/lib/queries/aggregator";
import { Pill } from "@/components/design/Pill";

const COL = "1.5fr 90px 110px 130px 90px 100px 100px 110px";

function pctOf(num: number, denom: number): string {
  if (!denom || denom <= 0) return "—";
  const p = Math.round((num / denom) * 100);
  return `${p}%`;
}

function HeaderCell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 500,
        color: "var(--text-faint)",
        textTransform: "uppercase",
        letterSpacing: "0.06em",
      }}
    >
      {children}
    </div>
  );
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diffSec = Math.floor((Date.now() - t) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function AgentsPage() {
  const [filter, setFilter] = useState<"all" | "ok" | "escalate">("all");
  const [search, setSearch] = useState("");
  const query = useQuery(getAgentsListQuery());
  const agents = query.data?.agents ?? [];

  const filtered = useMemo(() => {
    let rows = agents;
    if (filter === "ok") rows = rows.filter((a) => !a.needs_escalation);
    if (filter === "escalate") rows = rows.filter((a) => a.needs_escalation);
    if (search.trim()) {
      const q = search.toLowerCase();
      rows = rows.filter((a) => a.agent_name.toLowerCase().includes(q));
    }
    return rows;
  }, [agents, filter, search]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "14px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            fontSize: 19,
            fontWeight: 600,
            letterSpacing: "-0.018em",
            margin: 0,
            color: "var(--text-primary)",
          }}
        >
          Agents
        </h1>
        <Pill tone="neutral" mono>
          {agents.length} agent{agents.length === 1 ? "" : "s"}
        </Pill>
        <div
          style={{
            width: 1,
            height: 18,
            background: "var(--border-subtle)",
            margin: "0 4px",
          }}
        />
        <label
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            height: 30,
            padding: "0 4px 0 10px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--text-primary)",
          }}
        >
          <span style={{ color: "var(--text-faint)" }}>Status:</span>
          {/* 2026-05-14 audit fix: previously a fake <div> cycler — no
              keyboard access, no aria, can't jump direct to ESCALATE.
              Replaced with a real controlled <select> so screen readers
              + keyboard users work. Plan §5e label parity preserved. */}
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as typeof filter)}
            aria-label="Filter agents by status"
            style={{
              border: "none",
              background: "transparent",
              color: "inherit",
              fontSize: 12,
              fontFamily: "inherit",
              cursor: "pointer",
              padding: "0 6px",
            }}
          >
            <option value="all">All</option>
            <option value="ok">OK</option>
            <option value="escalate">ESCALATE</option>
          </select>
        </label>
        <div style={{ flex: 1 }} />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            height: 32,
            padding: "0 10px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            width: 240,
          }}
        >
          <Search size={14} style={{ color: "var(--text-dim)" }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search agents…"
            style={{
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--text-primary)",
              fontSize: 13,
              flex: 1,
              fontFamily: "inherit",
            }}
          />
        </div>
      </div>

      {/* Table */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: COL,
            gap: 12,
            padding: "10px 24px",
            borderBottom: "1px solid var(--border-subtle)",
            background: "var(--bg-elev1)",
          }}
        >
          <HeaderCell>Agent</HeaderCell>
          <HeaderCell>Total</HeaderCell>
          <HeaderCell>Compliant %</HeaderCell>
          <HeaderCell>Non-compliant %</HeaderCell>
          <HeaderCell>Flags</HeaderCell>
          <HeaderCell>Directives</HeaderCell>
          <HeaderCell>Last Call</HeaderCell>
          <HeaderCell>Status</HeaderCell>
        </div>
        <div style={{ flex: 1, overflowY: "auto" }} className="ca-scroll">
          {query.isError ? (
            <div
              role="alert"
              style={{
                padding: "40px 24px",
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: 13,
                display: "flex",
                flexDirection: "column",
                gap: 12,
                alignItems: "center",
              }}
            >
              <div>Couldn’t load agents.</div>
              <button
                type="button"
                onClick={() => query.refetch()}
                style={{
                  fontSize: 12,
                  padding: "6px 14px",
                  background: "transparent",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 6,
                  color: "var(--text-primary)",
                  cursor: "pointer",
                }}
              >
                Retry
              </button>
            </div>
          ) : query.isLoading
            ? Array.from({ length: 8 }).map((_, i) => (
                <div
                  key={i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: COL,
                    gap: 12,
                    padding: "14px 24px",
                    borderBottom: "1px solid var(--border-subtle)",
                  }}
                >
                  {[140, 50, 50, 50, 30, 30, 60, 70].map((w, j) => (
                    <div
                      key={j}
                      style={{
                        height: 10,
                        width: w,
                        background: "var(--bg-elev3)",
                        borderRadius: 3,
                        animation: "ca-pulse 1.5s ease-in-out infinite",
                      }}
                    />
                  ))}
                </div>
              ))
            : filtered.map((r) => {
                const flags = r.recent_non_compliant_30d ?? 0;
                return (
                  <Link
                    key={r.agent_name}
                    href={`/agents/${encodeURIComponent(r.agent_name)}`}
                    style={{
                      display: "grid",
                      gridTemplateColumns: COL,
                      gap: 12,
                      alignItems: "center",
                      padding: "12px 24px",
                      borderBottom: "1px solid var(--border-subtle)",
                      background: r.needs_escalation ? "var(--bg-elev2)" : "transparent",
                      borderLeft: `2px solid ${r.needs_escalation ? "var(--emerald)" : "transparent"}`,
                      fontSize: 13,
                      cursor: "pointer",
                      textDecoration: "none",
                      color: "inherit",
                    }}
                  >
                    <div style={{ color: "var(--text-primary)", fontWeight: 500 }}>
                      {r.agent_name}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-primary)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {r.total_calls}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: "var(--emerald)",
                        fontVariantNumeric: "tabular-nums",
                        display: "flex",
                        alignItems: "baseline",
                        gap: 6,
                      }}
                    >
                      <span>{pctOf(r.compliant, r.total_calls)}</span>
                      <span style={{ color: "var(--text-faint)", fontSize: 11 }}>
                        ({r.compliant}/{r.total_calls})
                      </span>
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: r.non_compliant > 10 ? "var(--red)" : "var(--text-muted)",
                        fontVariantNumeric: "tabular-nums",
                        display: "flex",
                        alignItems: "baseline",
                        gap: 6,
                      }}
                    >
                      <span>{pctOf(r.non_compliant, r.total_calls)}</span>
                      <span style={{ color: "var(--text-faint)", fontSize: 11 }}>
                        ({r.non_compliant}/{r.total_calls})
                      </span>
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: flags > 3 ? "var(--amber)" : "var(--text-muted)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {flags || "—"}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: r.open_directives > 0 ? "var(--amber)" : "var(--text-faint)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {r.open_directives || "—"}
                    </div>
                    <div
                      style={{
                        color: "var(--text-muted)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {formatRelative(r.last_call_at)}
                    </div>
                    <div>
                      <Pill tone={r.needs_escalation ? "red" : "emerald"} dot>
                        {r.needs_escalation ? "ESCALATE" : "OK"}
                      </Pill>
                    </div>
                  </Link>
                );
              })}
        </div>
      </div>
    </div>
  );
}
