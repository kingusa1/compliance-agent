"use client";

/**
 * /observability — ported from
 * design/handoff-bundle/project/screens/ops.jsx (Observability).
 *
 * Top bar: H1 + stuck pill + status filter chips + 1h/24h/7d/all
 * segmented + search + Pause/Refresh. StuckBanner with red border
 * + redispatch CTA. 60/40 split: runs list (5-col) + drawer with
 * step waterfall + expanded step JSON.
 */
import { useEffect, useRef, useState } from "react";
import { Search, RefreshCw, Pause as PauseIcon } from "lucide-react";

import {
  useRunsQuery,
  useStuckQuery,
  useStepsQuery,
  useFeedQuery,
  type FeedEvent,
  type PipelineStep,
} from "@/lib/queries/observability";
import { useRedispatchOrphan } from "@/lib/mutations/observability";
import { useObservabilityStream } from "@/lib/hooks/useObservabilityStream";
import { Pill, type PillTone } from "@/components/design/Pill";

type StatusFilter = "all" | "running" | "succeeded" | "failed" | "cancelled";

function statusToneRaw(s: string | null | undefined): {
  tone: PillTone;
  color: string;
} {
  switch ((s || "").toLowerCase()) {
    case "running":
      return { tone: "amber", color: "var(--amber)" };
    case "stuck":
    case "failed":
      return { tone: "red", color: "var(--red)" };
    case "succeeded":
      return { tone: "emerald", color: "var(--emerald)" };
    case "cancelled":
      return { tone: "neutral", color: "var(--text-faint)" };
    default:
      return { tone: "neutral", color: "var(--text-muted)" };
  }
}

export default function ObservabilityPage() {
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [range, setRange] = useState<"1h" | "24h" | "7d" | "all">("24h");
  const [search, setSearch] = useState("");
  const [streamPaused, setStreamPaused] = useState(false);
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null);
  // Live SSE feed — invalidates the runs/stuck caches on each event.
  useObservabilityStream(!streamPaused);
  const runs = useRunsQuery({
    status: filter !== "all" ? filter : undefined,
    range,
    q: search || undefined,
  });
  const stuck = useStuckQuery();
  const stuckRows = stuck.data?.stuck ?? [];
  const redispatch = useRedispatchOrphan();
  // Auto-select most-recent run so the drawer + terminal feed light up
  // immediately on first render — saves a click during demo / smoke.
  const firstRunCallId = (runs.data?.runs ?? [])[0]?.call_id ?? null;
  const activeCallId = selectedCallId ?? firstRunCallId;
  // Live polling: 2s while page open. Fetches per-call step waterfall + the
  // merged ComfyUI-style terminal feed (steps + LLM traces interleaved by
  // timestamp). Both auto-disable when no call selected.
  // Only fast-poll while the selected run is still in flight. Completed
  // runs degrade to 30s (just freshness check) so we don't hammer the
  // Supabase pooler with constant queries on historical data.
  const activeRun = (runs.data?.runs ?? []).find((r) => r.call_id === activeCallId);
  const isActiveRun = !activeRun || (activeRun.status ?? "").toLowerCase() === "running";
  const stepsQ = useStepsQuery(activeCallId, isActiveRun);
  const feedQ = useFeedQuery(activeCallId, isActiveRun);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
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
          Observability
        </h1>
        {stuckRows.length > 0 && (
          <Pill tone="amber" dot>
            {stuckRows.length} stuck
          </Pill>
        )}
        <div style={{ width: 1, height: 18, background: "var(--border-subtle)", margin: "0 4px" }} />
        {(["all", "running", "succeeded", "failed", "cancelled"] as StatusFilter[]).map(
          (s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                fontWeight: 500,
                background: filter === s ? "var(--bg-elev2)" : "transparent",
                color: filter === s ? "var(--text-primary)" : "var(--text-muted)",
                border: `1px solid ${filter === s ? "var(--border-strong)" : "transparent"}`,
                borderRadius: 6,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {s}
            </button>
          ),
        )}
        <div style={{ width: 1, height: 18, background: "var(--border-subtle)", margin: "0 4px" }} />
        <div
          style={{
            display: "inline-flex",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            overflow: "hidden",
          }}
        >
          {(["1h", "24h", "7d", "all"] as const).map((r, i) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                fontWeight: 500,
                background: range === r ? "var(--bg-elev2)" : "transparent",
                color: range === r ? "var(--text-primary)" : "var(--text-muted)",
                borderRight: i < 3 ? "1px solid var(--border-subtle)" : "none",
                cursor: "pointer",
                border: "none",
                fontFamily: "inherit",
              }}
            >
              {r}
            </button>
          ))}
        </div>
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
            width: 220,
          }}
        >
          <Search size={14} style={{ color: "var(--text-dim)" }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search runs / call_id…"
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
        <button
          onClick={() => setStreamPaused((p) => !p)}
          style={{
            height: 28,
            padding: "0 10px",
            background: streamPaused ? "var(--amber-bg)" : "var(--bg-elev2)",
            border: `1px solid ${streamPaused ? "var(--amber-border)" : "var(--border-subtle)"}`,
            color: streamPaused ? "var(--amber-400)" : "var(--text-primary)",
            borderRadius: 6,
            fontSize: 12,
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
          aria-pressed={streamPaused}
        >
          <PauseIcon size={12} />
          {streamPaused ? "Resume" : "Pause"}
        </button>
        <button
          onClick={() => runs.refetch()}
          style={{
            height: 28,
            padding: "0 10px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            color: "var(--text-primary)",
            borderRadius: 6,
            fontSize: 12,
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>

      {/* StuckBanner */}
      {stuckRows.length > 0 && (
        <div
          style={{
            margin: "16px 24px 0",
            padding: "12px 16px",
            background: "var(--red-bg)",
            border: "1px solid rgba(239,68,68,0.40)",
            borderRadius: 8,
            display: "flex",
            alignItems: "center",
            gap: 12,
            flexShrink: 0,
          }}
        >
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 14,
              background: "rgba(239,68,68,0.15)",
              display: "grid",
              placeItems: "center",
              color: "var(--red)",
              fontWeight: 600,
            }}
          >
            !
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, color: "var(--red)", fontWeight: 500 }}>
              {stuckRows.length} call{stuckRows.length === 1 ? "" : "s"} stuck — auto-redispatch in 60s
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
              {stuckRows.slice(0, 3).map((s) => s.call_id).join(", ")}
              {stuckRows.length > 3 ? ` (+${stuckRows.length - 3})` : ""}
            </div>
          </div>
          {/* 2026-05-24 wiring audit HIGH — removed the "Cancel auto"
              button. It had no onClick (auto-cancel is not implemented
              backend-side) so it was a dead CTA. If we add a cancel
              mutation later, reintroduce with a proper onClick. */}
          <button
            disabled={redispatch.isPending}
            data-testid="redispatch-now"
            onClick={() => {
              // Loop through every stuck call and fire the mutation; the
              // hook handles toast + invalidation per row.
              for (const s of stuckRows) {
                redispatch.mutate(s.call_id);
              }
            }}
            style={{
              height: 28,
              padding: "0 10px",
              background: "var(--emerald)",
              color: "#04201a",
              border: "1px solid var(--emerald)",
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer",
              fontFamily: "inherit",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            Redispatch now
          </button>
        </div>
      )}

      {/* Runs + drawer */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "60% 40%",
          overflow: "hidden",
          minHeight: 0,
          marginTop: 16,
        }}
      >
        <div
          style={{
            borderRight: "1px solid var(--border-subtle)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1.6fr 1.2fr 100px 110px 90px",
              gap: 10,
              padding: "10px 24px",
              borderBottom: "1px solid var(--border-subtle)",
              background: "var(--bg-elev1)",
            }}
          >
            {["Workflow / Run ID", "Call", "Started", "Status", "Duration"].map((h) => (
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
          <div style={{ flex: 1, overflowY: "auto" }} className="ca-scroll">
            {(runs.data?.runs ?? []).map((r, i) => {
              const t = statusToneRaw(r.status);
              const isSelected = activeCallId && r.call_id === activeCallId;
              return (
                <div
                  key={(r.run_id ?? "") + i}
                  onClick={() => r.call_id && setSelectedCallId(r.call_id)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1.6fr 1.2fr 100px 110px 90px",
                    gap: 10,
                    alignItems: "center",
                    padding: "10px 24px",
                    borderBottom: "1px solid var(--border-subtle)",
                    background: isSelected ? "var(--bg-elev2)" : "transparent",
                    borderLeft: `2px solid ${isSelected ? "var(--emerald)" : "transparent"}`,
                    fontSize: 12,
                    cursor: "pointer",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ color: "var(--text-primary)", fontWeight: 500, fontSize: 13 }}>
                      {r.workflow ?? "—"}
                    </div>
                    <div
                      style={{
                        color: "var(--text-faint)",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        marginTop: 2,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {r.run_id ?? "—"}
                    </div>
                  </div>
                  <div>
                    {r.call_id ? (
                      <Pill tone="neutral" mono>
                        {r.call_id.slice(0, 12)}
                      </Pill>
                    ) : (
                      "—"
                    )}
                  </div>
                  <div style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
                    {r.started_at ? new Date(r.started_at).toLocaleTimeString() : "—"}
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      color: t.color,
                    }}
                  >
                    <span
                      style={{
                        display: "inline-block",
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: t.color,
                      }}
                    />
                    {r.status ?? "—"}
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--font-mono)",
                      color: "var(--text-muted)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {r.duration_ms ? `${r.duration_ms}ms` : "—"}
                  </div>
                </div>
              );
            })}
            {!runs.isLoading && (runs.data?.runs ?? []).length === 0 && (
              <div
                style={{
                  padding: 32,
                  fontSize: 13,
                  color: "var(--text-muted)",
                  textAlign: "center",
                }}
              >
                No runs in this window.
              </div>
            )}
          </div>
        </div>

        {/* Drawer */}
        <div
          style={{
            background: "var(--bg-elev2)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-subtle)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <Pill tone="amber" dot>
                running
              </Pill>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  color: "var(--text-muted)",
                }}
              >
                {(runs.data?.runs ?? [])[0]?.run_id ?? "—"}
              </span>
            </div>
            <div
              style={{
                fontSize: 16,
                fontWeight: 600,
                letterSpacing: "-0.014em",
                marginTop: 4,
                color: "var(--text-primary)",
              }}
            >
              {(runs.data?.runs ?? [])[0]?.workflow ?? "—"}
            </div>
            <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
              {activeCallId ?? "—"} · {(stepsQ.data?.steps ?? []).length} steps
            </div>
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }} className="ca-scroll">
            <StepWaterfall steps={stepsQ.data?.steps ?? []} loading={stepsQ.isLoading} />
          </div>
        </div>
      </div>

      {/* Live terminal feed — ComfyUI-style: every step start/ok/err line +
          every LLM prompt/response interleaved by timestamp. Polls 2s. */}
      {activeCallId && (
        <TerminalFeed events={feedQ.data?.events ?? []} loading={feedQ.isLoading} />
      )}
    </div>
  );
}


// ── Step waterfall (replaces hardcoded STEPS array) ──────────────────────
function StepWaterfall({ steps, loading }: { steps: PipelineStep[]; loading: boolean }) {
  if (loading && steps.length === 0) {
    return <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading steps…</div>;
  }
  if (steps.length === 0) {
    return (
      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
        No pipeline steps recorded yet for this call. Steps will appear here as
        the workflow executes.
      </div>
    );
  }
  // Compute waterfall offsets relative to the run's first started_at.
  const t0 = steps.reduce<number | null>((acc, s) => {
    const ms = s.started_at ? new Date(s.started_at).getTime() : null;
    return ms != null && (acc == null || ms < acc) ? ms : acc;
  }, null);
  const totalMs = steps.reduce((acc, s) => {
    const start = s.started_at ? new Date(s.started_at).getTime() : null;
    const end = s.ended_at ? new Date(s.ended_at).getTime() : start ?? null;
    if (start == null || end == null || t0 == null) return acc;
    return Math.max(acc, end - t0);
  }, 0);

  return (
    <>
      <div style={{
        fontSize: 11, fontWeight: 500, color: "var(--text-faint)",
        textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12,
      }}>Step waterfall</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {steps.map((s) => {
          const tone =
            s.status === "ok" ? "var(--emerald)" :
            s.status === "running" ? "var(--amber)" :
            s.status === "err" ? "var(--red)" :
            "var(--border-strong)";
          const start = s.started_at ? new Date(s.started_at).getTime() : null;
          const end = s.ended_at ? new Date(s.ended_at).getTime() : start;
          const startPct = (t0 != null && start != null && totalMs > 0) ? ((start - t0) / totalMs) * 100 : 0;
          const widthPct = (start != null && end != null && totalMs > 0) ? Math.max(2, ((end - start) / totalMs) * 100) : 4;
          return (
            <details key={s.id} style={{ background: "var(--bg-elev1)", borderRadius: 4, padding: "6px 8px" }}>
              <summary style={{ cursor: "pointer", listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)", flex: 1 }}>{s.step_name}</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: tone, fontSize: 11 }}>{s.status}</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
                    {s.duration_ms != null ? `${s.duration_ms}ms` : s.status === "running" ? "running…" : "—"}
                  </span>
                </div>
                <div style={{ height: 8, background: "var(--bg-canvas)", borderRadius: 2, position: "relative" }}>
                  <div style={{
                    position: "absolute", left: `${startPct}%`, width: `${widthPct}%`,
                    height: "100%", background: tone, borderRadius: 2,
                    opacity: s.status === "running" ? 0.6 : 1,
                  }} />
                </div>
              </summary>
              <div style={{ marginTop: 8, fontSize: 11, fontFamily: "var(--font-mono)" }}>
                {s.error_message && (
                  <div style={{ background: "var(--red-bg)", color: "var(--red)", padding: 6, borderRadius: 4, marginBottom: 6 }}>
                    error: {s.error_message}
                  </div>
                )}
                {s.payload_in != null && (
                  <details style={{ marginBottom: 4 }}>
                    <summary style={{ color: "var(--text-muted)", cursor: "pointer" }}>input</summary>
                    <pre style={{ margin: "4px 0", padding: 6, background: "var(--bg-canvas)", borderRadius: 4, overflowX: "auto", fontSize: 10, color: "var(--text-primary)" }}>
                      {JSON.stringify(s.payload_in, null, 2)}
                    </pre>
                  </details>
                )}
                {s.payload_out != null && (
                  <details>
                    <summary style={{ color: "var(--text-muted)", cursor: "pointer" }}>output</summary>
                    <pre style={{ margin: "4px 0", padding: 6, background: "var(--bg-canvas)", borderRadius: 4, overflowX: "auto", fontSize: 10, color: "var(--text-primary)" }}>
                      {JSON.stringify(s.payload_out, null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            </details>
          );
        })}
      </div>
    </>
  );
}


// ── Live terminal feed (ComfyUI-style live tail, draggable height) ──────
function TerminalFeed({ events, loading }: { events: FeedEvent[]; loading: boolean }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const eventCount = events.length;
  // Draggable height: persist across reloads via localStorage.
  const [height, setHeight] = useState<number>(() => {
    if (typeof window === "undefined") return 320;
    const v = parseInt(window.localStorage.getItem("ca:terminal:h") ?? "", 10);
    return Number.isFinite(v) && v >= 80 ? v : 320;
  });
  const dragStateRef = useRef<{ startY: number; startH: number } | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [eventCount]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("ca:terminal:h", String(height));
    }
  }, [height]);

  const onDragStart = (e: React.MouseEvent) => {
    e.preventDefault();
    dragStateRef.current = { startY: e.clientY, startH: height };
    const onMove = (ev: MouseEvent) => {
      if (!dragStateRef.current) return;
      const delta = dragStateRef.current.startY - ev.clientY;
      const next = Math.max(80, Math.min(window.innerHeight - 100, dragStateRef.current.startH + delta));
      setHeight(next);
    };
    const onUp = () => {
      dragStateRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "ns-resize";
    document.body.style.userSelect = "none";
  };

  return (
    <div style={{
      background: "#08120c",
      flexShrink: 0,
      height,
      display: "flex", flexDirection: "column",
      borderTop: "1px solid var(--border-subtle)",
    }}>
      {/* Drag handle — sits flush on top edge, ns-resize cursor. Double-click = reset to 320. */}
      <div
        onMouseDown={onDragStart}
        onDoubleClick={() => setHeight(320)}
        title="Drag to resize · double-click to reset"
        style={{
          height: 6,
          cursor: "ns-resize",
          background: "rgba(255,255,255,0.06)",
          borderBottom: "1px solid rgba(255,255,255,0.04)",
          display: "flex", alignItems: "center", justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <div style={{ width: 36, height: 2, background: "rgba(255,255,255,0.25)", borderRadius: 2 }} />
      </div>
      <div style={{
        padding: "6px 16px", borderBottom: "1px solid rgba(255,255,255,0.06)",
        display: "flex", alignItems: "center", gap: 8, fontSize: 11,
        fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.6)",
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#34d399", boxShadow: "0 0 6px #34d399" }} />
        live feed · {eventCount} event{eventCount === 1 ? "" : "s"} · polls 2s · {height}px
        {loading && <span style={{ marginLeft: "auto", opacity: 0.6 }}>refreshing…</span>}
      </div>
      <div ref={scrollRef} style={{
        flex: 1, overflowY: "auto", padding: "8px 16px",
        fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5,
      }}>
        {eventCount === 0 ? (
          <div style={{ color: "rgba(255,255,255,0.4)" }}>Waiting for first event…</div>
        ) : (
          events.map((e, i) => <FeedLine key={i} ev={e} />)
        )}
      </div>
    </div>
  );
}


function FeedLine({ ev }: { ev: FeedEvent }) {
  const ts = ev.ts ? new Date(ev.ts).toLocaleTimeString("en-GB", { hour12: false }) : "—";
  if (ev.kind === "step") {
    const c =
      ev.status === "ok" ? "#34d399" :
      ev.status === "running" ? "#fbbf24" :
      ev.status === "err" ? "#ef4444" :
      "rgba(255,255,255,0.6)";
    return (
      <div style={{ color: "rgba(255,255,255,0.85)" }}>
        <span style={{ color: "rgba(255,255,255,0.4)" }}>{ts}</span>{" "}
        <span style={{ color: c }}>[step:{ev.status}]</span>{" "}
        <span style={{ color: "#a5f3fc" }}>{ev.step_name}</span>
        {ev.duration_ms != null && (
          <span style={{ color: "rgba(255,255,255,0.5)" }}> ({ev.duration_ms}ms)</span>
        )}
        {ev.error_message && (
          <span style={{ color: "#ef4444" }}> · {ev.error_message}</span>
        )}
      </div>
    );
  }
  // trace
  const roleColor = ev.role === "user" ? "#a5b4fc" : ev.role === "assistant" ? "#34d399" : "#fbbf24";
  return (
    <details style={{ color: "rgba(255,255,255,0.75)" }}>
      <summary style={{ cursor: "pointer", listStyle: "none" }}>
        <span style={{ color: "rgba(255,255,255,0.4)" }}>{ts}</span>{" "}
        <span style={{ color: roleColor }}>[llm:{ev.role}]</span>{" "}
        <span style={{ color: "#fde68a" }}>{ev.tool_name ?? "—"}</span>
        {ev.latency_ms != null && (
          <span style={{ color: "rgba(255,255,255,0.5)" }}> ({ev.latency_ms}ms)</span>
        )}
        <span style={{ color: "rgba(255,255,255,0.5)" }}>
          {" "}· {(ev.content || "").slice(0, 80).replace(/\s+/g, " ")}
          {(ev.content || "").length > 80 ? "…" : ""}
        </span>
      </summary>
      <pre style={{
        margin: "4px 0 4px 24px", padding: 6, background: "rgba(255,255,255,0.04)",
        borderRadius: 4, overflowX: "auto", fontSize: 10, color: "rgba(255,255,255,0.9)",
        whiteSpace: "pre-wrap",
      }}>
        {ev.content}
      </pre>
    </details>
  );
}
