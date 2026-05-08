"use client";

/**
 * /settings → Observability tab.
 *
 * Renders 5 cards linking out to the Wave 2/3 dashboards. Each card
 * pings its tool's health endpoint every 60s and shows a green/red dot.
 * Click "Open ↗" to launch the tool in a new tab (kiosk mode where supported).
 */
import { useEffect, useState } from "react";

type DashKey = "pipeline" | "llm" | "api" | "errors" | "glitchtip";

interface Dashboard {
  key: DashKey;
  title: string;
  description: string;
  href: string;          // open this in new tab
  pingLayer: "grafana" | "glitchtip"; // resolved server-side via /api/health-check?layer=...
  loginUser?: string;    // shown inline on the card so reviewers can copy-paste
  loginPass?: string;
}

const DASHBOARDS: Dashboard[] = [
  {
    key: "pipeline",
    title: "Pipeline",
    description: "Per-step duration p50/p95/p99 + throughput. Diagnose slow calls.",
    href: "http://localhost:3001/d/compliance-pipeline/pipeline?kiosk=tv&theme=dark&refresh=30s",
    pingLayer: "grafana",
    loginUser: "admin",
    loginPass: "admin-dev-pass",
  },
  {
    key: "llm",
    title: "LLM",
    description: "Call rate, escalation rate, latency by model. Cost watch.",
    href: "http://localhost:3001/d/compliance-llm/llm?kiosk=tv&theme=dark&refresh=30s",
    pingLayer: "grafana",
    loginUser: "admin",
    loginPass: "admin-dev-pass",
  },
  {
    key: "api",
    title: "API",
    description: "RPS, latency p50/p95/p99, error rate per route.",
    href: "http://localhost:3001/d/compliance-api/api?kiosk=tv&theme=dark&refresh=30s",
    pingLayer: "grafana",
    loginUser: "admin",
    loginPass: "admin-dev-pass",
  },
  {
    key: "errors",
    title: "Errors",
    description: "ERROR-level log rate + recent ERROR lines (Loki).",
    href: "http://localhost:3001/d/compliance-errors/errors?kiosk=tv&theme=dark&refresh=1m",
    pingLayer: "grafana",
    loginUser: "admin",
    loginPass: "admin-dev-pass",
  },
  {
    key: "glitchtip",
    title: "GlitchTip Issues",
    description: "Captured exceptions w/ stack traces + request payloads.",
    href: "http://localhost:8080/issues",
    pingLayer: "glitchtip",
  },
];

type Status = "checking" | "up" | "down";

async function ping(d: Dashboard): Promise<Status> {
  try {
    // Server-side proxy avoids CORS — see app/api/health-check/route.ts
    const r = await fetch(`/api/health-check?layer=${d.pingLayer}`, { cache: "no-store" });
    if (!r.ok) return "down";
    const body = (await r.json()) as { up: boolean };
    return body.up ? "up" : "down";
  } catch {
    return "down";
  }
}

export function ObservabilityTab() {
  const [statuses, setStatuses] = useState<Record<DashKey, Status>>({
    pipeline: "checking",
    llm: "checking",
    api: "checking",
    errors: "checking",
    glitchtip: "checking",
  });

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      const results = await Promise.all(DASHBOARDS.map(ping));
      if (cancelled) return;
      const next: Record<DashKey, Status> = { ...statuses };
      DASHBOARDS.forEach((d, i) => {
        next[d.key] = results[i];
      });
      setStatuses(next);
    }
    refresh();
    const id = setInterval(refresh, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">
          Observability
        </h2>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Operations dashboards. Open in a new tab for the full Grafana / GlitchTip
          UI. Status dot pings every 60 seconds.
        </p>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gap: 12,
        }}
      >
        {DASHBOARDS.map((d) => (
          <DashboardCard key={d.key} dashboard={d} status={statuses[d.key]} />
        ))}
      </div>

      <details className="mt-2 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3 text-[12px] text-[var(--text-muted)]" open>
        <summary className="cursor-pointer font-medium text-[var(--text-primary)]">
          Local dev login credentials
        </summary>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide">Grafana</div>
            <div className="mt-0.5">
              user: <code className="font-mono text-[11px]">admin</code>
              <br />
              pass: <code className="font-mono text-[11px]">admin-dev-pass</code>
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide">GlitchTip</div>
            <div className="mt-0.5">
              First-visitor signup creates the superuser account.
              Use any email / password and tick "I'm not a robot" — see
              <code className="ml-1 font-mono text-[11px]">infrastructure/observability/README.md</code>.
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide">Prometheus</div>
            <div className="mt-0.5">
              <a href="http://localhost:9090" target="_blank" rel="noopener noreferrer" className="text-emerald-700 underline">
                http://localhost:9090
              </a>{" "}
              · no auth (local-only port-bind)
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide">Loki</div>
            <div className="mt-0.5">
              <a href="http://localhost:3100/ready" target="_blank" rel="noopener noreferrer" className="text-emerald-700 underline">
                http://localhost:3100
              </a>{" "}
              · API only (queried via Grafana → Errors dash)
            </div>
          </div>
        </div>
      </details>

      <p className="text-[11px] text-[var(--text-muted)]">
        Configured for local dev (localhost ports). On production VPS, these route
        through Cloudflare Tunnel hostnames per <code>infrastructure/contabo/README.md</code>.
      </p>
    </div>
  );
}

function DashboardCard({ dashboard, status }: { dashboard: Dashboard; status: Status }) {
  const dotColor =
    status === "up" ? "var(--emerald)" :
    status === "down" ? "var(--red, #ef4444)" :
    "var(--text-muted)";
  const statusLabel =
    status === "up" ? "Reachable" :
    status === "down" ? "Unreachable" :
    "Checking…";

  return (
    <div
      style={{
        border: "1px solid var(--border-subtle)",
        borderRadius: 8,
        padding: 14,
        background: "var(--surface-1)",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
          {dashboard.title}
        </h3>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--text-muted)" }}>
          <span style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: dotColor,
            display: "inline-block",
          }} />
          {statusLabel}
        </span>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0, lineHeight: 1.4 }}>
        {dashboard.description}
      </p>
      {(dashboard.loginUser || dashboard.loginPass) && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", display: "flex", flexDirection: "column", gap: 4 }}>
          {dashboard.loginUser && (
            <CopyableCred label="user" value={dashboard.loginUser} />
          )}
          {dashboard.loginPass && (
            <CopyableCred label="pass" value={dashboard.loginPass} />
          )}
        </div>
      )}
      <a
        href={dashboard.href}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          display: "inline-block",
          width: "fit-content",
          fontSize: 12,
          color: "var(--text-primary)",
          textDecoration: "none",
          padding: "6px 10px",
          border: "1px solid var(--border-subtle)",
          borderRadius: 6,
          background: "var(--surface-2)",
        }}
      >
        Open ↗
      </a>
    </div>
  );
}


function CopyableCred({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // fallback: select-into-input trick
      const ta = document.createElement("textarea");
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); setCopied(true); setTimeout(() => setCopied(false), 1500); }
      finally { document.body.removeChild(ta); }
    }
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span style={{ width: 32, color: "var(--text-muted)" }}>{label}</span>
      <code style={{ fontFamily: "ui-monospace, monospace", fontSize: 11, padding: "2px 6px", background: "var(--bg-elev2)", borderRadius: 4, color: "var(--text-primary)" }}>
        {value}
      </code>
      <button
        type="button"
        onClick={onCopy}
        style={{
          fontSize: 10,
          padding: "2px 6px",
          border: "1px solid var(--border-subtle)",
          borderRadius: 4,
          background: copied ? "var(--emerald)" : "var(--surface-2)",
          color: copied ? "#04201a" : "var(--text-primary)",
          cursor: "pointer",
        }}
      >
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}
