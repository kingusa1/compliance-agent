"use client";

/**
 * /dashboard — focused home page (redesigned 2026-05-10).
 *
 * Three sections, top to bottom:
 *
 *   1) KPI strip  — total / compliant / non-compliant / compliance rate.
 *   2) Three primary action cards — Queue, Tracker, All calls.
 *      Secondary destinations (Customers, Deals, Scripts, Rejections,
 *      Observability, Compliant, Non-compliant) are reachable from the
 *      left sidebar; we don't double-show them here.
 *   3) Recent activity — last 5 uploaded calls, click-through.
 *
 * Goal: reduce cognitive load. The previous 10-tile grid put everything
 * in front of every user every visit — for daily use, "what was just
 * uploaded" matters more than "where is the scripts page".
 */
import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Upload,
  Inbox,
  Table as TableIcon,
  ListVideo,
  ArrowRight,
  CheckCircle2,
  Sparkles,
  Clock,
} from "lucide-react";

import { apiFetch } from "@/lib/api";
import { UploadModal } from "@/app/(admin)/calls/UploadModal";
import { fetchQueue } from "@/lib/queries/reviewer";
import { IntelligencePanel } from "./IntelligencePanel";

interface StatsResponse {
  total_calls: number;
  compliant_count: number;
  non_compliant_count: number;
  compliance_rate: number;
  processing_count?: number;
  needs_review_count?: number;
  reviewed_count?: number;
  automated_rate?: number;
}

interface CallRow {
  id: string;
  filename: string;
  customer_name: string | null;
  agent_name: string | null;
  detected_supplier: string | null;
  score: string | null;
  compliant: boolean | null;
  created_at: string;
  reason: string | null;
}

interface CallsListResponse {
  calls: CallRow[];
  total: number;
}

interface KPI {
  label: string;
  value: string | number;
  hint: string;
  href?: string;
  tone: "neutral" | "good" | "bad" | "warn";
}

const PRIMARY_ACTIONS = [
  {
    href: "/queue",
    label: "Human Review Queue",
    icon: Inbox,
    description:
      "Calls flagged by the AI as needing reviewer attention. Open, listen, accept or override.",
  },
  {
    href: "/tracker",
    label: "Tracker",
    icon: TableIcon,
    description:
      "The full operational tracker — every call and every rejection with all 16 columns.",
  },
  {
    href: "/calls",
    label: "All Calls",
    icon: ListVideo,
    description:
      "Every uploaded call, newest first. Filter, search, delete. The master list of recordings.",
  },
];

const QUICK_START_STEPS = [
  {
    title: "Upload your first call",
    description:
      "Click the green button. Drop an MP3/WAV. The pipeline (Deepgram → Opus 4.7) runs automatically.",
  },
  {
    title: "Watch it land in the Tracker",
    description:
      "The Tracker mirrors the Watt XLSX. Click a row to see verdict, evidence, and rejections.",
  },
  {
    title: "Sign off or override on the Human Review Queue",
    description:
      "If the AI flagged it, open the call and either accept or override. Your decision is the audit-of-record.",
  },
];

function useStats() {
  return useQuery({
    queryKey: ["dashboard:stats"] as const,
    queryFn: () => apiFetch<StatsResponse>("/api/stats"),
    staleTime: 60_000,
  });
}

function useRecentCalls() {
  return useQuery({
    queryKey: ["dashboard:recent-calls"] as const,
    queryFn: () =>
      apiFetch<CallsListResponse>("/api/calls?limit=5&skip=0"),
    staleTime: 30_000,
  });
}

function useQueueBacklog() {
  return useQuery({
    queryKey: ["dashboard:queue-backlog"] as const,
    queryFn: () => fetchQueue("unclaimed"),
    staleTime: 30_000,
  });
}

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Math.floor((Date.now() - t) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function DashboardPage() {
  const stats = useStats();
  const recent = useRecentCalls();
  const queueBacklog = useQueueBacklog();
  const [uploadOpen, setUploadOpen] = useState(false);
  const qc = useQueryClient();
  const isFreshInstall = (stats.data?.total_calls ?? 0) === 0;
  const backlog = queueBacklog.data?.metrics?.backlog ?? 0;

  const kpis: KPI[] = [
    {
      label: "Total calls",
      value: stats.data?.total_calls ?? "—",
      hint: "All calls ever uploaded",
      href: "/calls",
      tone: "neutral",
    },
    {
      label: "Compliant",
      value: stats.data?.compliant_count ?? "—",
      hint: "Signed off clean",
      href: "/compliant",
      tone: "good",
    },
    {
      label: "Non-compliant",
      value: stats.data?.non_compliant_count ?? "—",
      hint: "Flagged for fix or escalation",
      href: "/non-compliant",
      tone: "bad",
    },
    {
      label: "Compliance rate",
      value:
        stats.data?.compliance_rate != null
          // /api/stats already returns the rate as a percent (e.g. 25.9 for
          // 7/27 calls compliant) — just round, do NOT multiply by 100 again.
          ? `${Math.round(stats.data.compliance_rate)}%`
          : "—",
      hint: "Across all reviewed calls",
      tone:
        stats.data?.compliance_rate != null && stats.data.compliance_rate >= 80
          ? "good"
          : "warn",
    },
  ];

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-5">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">
            Compliance Agent · Watt Utilities · Opus 4.7 · Deepgram en-GB
          </p>
        </div>
        <button
          type="button"
          onClick={() => setUploadOpen(true)}
          className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-emerald-700"
        >
          <Upload className="size-4" /> Upload Call
        </button>
      </header>

      <div className="space-y-6 px-6 py-6">
        {/* KPI strip */}
        <section
          className="grid gap-4"
          style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}
        >
          {kpis.map((k) => {
            const tone =
              k.tone === "good"
                ? "text-emerald-300"
                : k.tone === "bad"
                  ? "text-red-400"
                  : k.tone === "warn"
                    ? "text-amber-300"
                    : "text-[var(--text-primary)]";
            const card = (
              <div
                className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4 transition-colors hover:bg-[var(--bg-elev2)]"
                style={{ minHeight: 96 }}
              >
                <div className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                  {k.label}
                </div>
                <div className={`mt-1 text-[28px] font-semibold tabular-nums ${tone}`}>
                  {stats.isLoading ? "…" : k.value}
                </div>
                <div className="mt-1 text-[12px] text-[var(--text-muted)]">{k.hint}</div>
              </div>
            );
            return k.href ? (
              <Link key={k.label} href={k.href} className="block focus:outline-none">
                {card}
              </Link>
            ) : (
              <div key={k.label}>{card}</div>
            );
          })}
        </section>

        {/* Quick-start guide — only on fresh install */}
        {isFreshInstall && !stats.isLoading ? (
          <section className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-5">
            <div className="flex items-start gap-3">
              <Sparkles className="mt-1 size-4 text-emerald-300" />
              <div className="flex-1">
                <h2 className="text-[15px] font-semibold text-emerald-200">
                  Welcome — three steps to get going
                </h2>
                <p className="mt-1 text-[12.5px] text-emerald-200/80">
                  No calls have been processed yet. Once you upload one, this guide
                  auto-hides and the KPIs above light up.
                </p>
                <ol className="mt-4 space-y-3">
                  {QUICK_START_STEPS.map((s, i) => (
                    <li key={s.title} className="flex items-start gap-3">
                      <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-emerald-500/15 text-[12px] font-medium text-emerald-200 ring-1 ring-emerald-500/30">
                        {i + 1}
                      </span>
                      <div>
                        <div className="text-[13.5px] font-medium text-emerald-100">
                          {s.title}
                        </div>
                        <div className="text-[12.5px] text-emerald-200/80">
                          {s.description}
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
                <div className="mt-5 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setUploadOpen(true)}
                    className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3.5 py-2 text-[12.5px] font-medium text-white hover:bg-emerald-700"
                  >
                    <Upload className="size-3.5" /> Upload your first call
                  </button>
                  <Link
                    href="/guide"
                    className="inline-flex items-center gap-2 text-[12.5px] text-emerald-200/90 hover:text-emerald-100"
                  >
                    Read the full user guide <ArrowRight className="size-3.5" />
                  </Link>
                </div>
              </div>
            </div>
          </section>
        ) : null}

        {/* Three primary action cards */}
        <section>
          <div className="mb-3 flex items-center gap-2">
            <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
              What do you want to do?
            </h2>
          </div>
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}
          >
            {PRIMARY_ACTIONS.map((a) => {
              const Icon = a.icon;
              const isQueue = a.href === "/queue";
              const showBadge = isQueue && backlog > 0;
              return (
                <Link
                  key={a.href}
                  href={a.href}
                  className="group rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5 transition-colors hover:border-emerald-500/50 hover:bg-[var(--bg-elev2)]"
                >
                  <div className="flex items-start justify-between">
                    <div className="grid size-10 place-items-center rounded-lg bg-emerald-500/10">
                      <Icon className="size-5 text-emerald-300" />
                    </div>
                    {showBadge ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-300 ring-1 ring-amber-500/30">
                        {backlog} pending
                      </span>
                    ) : (
                      <ArrowRight className="size-4 text-[var(--text-dim)] transition-transform group-hover:translate-x-0.5 group-hover:text-emerald-300" />
                    )}
                  </div>
                  <div className="mt-4 text-[16px] font-semibold text-[var(--text-primary)]">
                    {a.label}
                  </div>
                  <div className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-muted)]">
                    {a.description}
                  </div>
                </Link>
              );
            })}
          </div>
          <div className="mt-3 text-[12px] text-[var(--text-muted)]">
            More pages (Customers, Deals, Scripts, Rejections, Compliant,
            Non-compliant) are in the left sidebar.
          </div>
        </section>

        {/* Intelligence panel — only render once we have data to chart. */}
        {!isFreshInstall ? <IntelligencePanel /> : null}

        {/* Recent activity */}
        {!isFreshInstall ? (
          <section>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
                Recent calls
              </h2>
              <Link
                href="/calls"
                className="inline-flex items-center gap-1 text-[12.5px] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              >
                View all <ArrowRight className="size-3.5" />
              </Link>
            </div>
            <div className="overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
              {recent.isLoading ? (
                <div className="p-6 text-center text-[12.5px] text-[var(--text-muted)]">
                  Loading…
                </div>
              ) : (recent.data?.calls.length ?? 0) === 0 ? (
                <div className="p-6 text-center text-[12.5px] text-[var(--text-muted)]">
                  No calls yet.
                </div>
              ) : (
                recent.data!.calls.map((c, i) => (
                  <Link
                    key={c.id}
                    href={`/calls/${c.id}`}
                    className={`flex items-center gap-4 px-4 py-3 text-[12.5px] transition-colors hover:bg-[var(--bg-elev2)] ${
                      i === 0 ? "" : "border-t border-[var(--border-subtle)]"
                    }`}
                  >
                    <Clock className="size-3.5 shrink-0 text-[var(--text-dim)]" />
                    <div
                      className="w-20 shrink-0 text-[var(--text-muted)] tabular-nums"
                      title={c.created_at ? new Date(c.created_at).toLocaleString() : undefined}
                    >
                      {relativeTime(c.created_at)}
                    </div>
                    <div className="min-w-0 flex-1 truncate text-[var(--text-primary)]">
                      {c.customer_name ?? c.filename}
                    </div>
                    <div className="hidden w-32 shrink-0 truncate text-[var(--text-muted)] md:block">
                      {c.detected_supplier ?? "—"}
                    </div>
                    <div className="hidden w-24 shrink-0 truncate text-[var(--text-muted)] md:block">
                      {c.agent_name ?? "—"}
                    </div>
                    <div className="w-14 shrink-0 text-right tabular-nums text-[var(--text-muted)]">
                      {c.score ?? "—"}
                    </div>
                    <div className="w-24 shrink-0 text-right">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                          c.compliant === true
                            ? "bg-emerald-500/10 text-emerald-300"
                            : c.compliant === false
                              ? "bg-red-500/10 text-red-300"
                              : "bg-[var(--bg-elev3)] text-[var(--text-muted)]"
                        }`}
                      >
                        {c.compliant === true
                          ? "compliant"
                          : c.compliant === false
                            ? "non-compliant"
                            : "pending"}
                      </span>
                    </div>
                  </Link>
                ))
              )}
            </div>
          </section>
        ) : null}

        {/* System state footer */}
        <section className="flex items-center gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 text-[11.5px] text-[var(--text-muted)]">
          <CheckCircle2 className="size-3.5 text-emerald-400" />
          <div>
            System operational · Vercel · Railway · Supabase · Opus 4.7 ·
            Deepgram Nova-3 (en-GB).
          </div>
        </section>
      </div>

      <UploadModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        // 2026-05-14 audit fix: without explicit invalidation the dashboard
        // would lag the new call by 60s (stats staleTime) / 30s (recent +
        // queue staleTime). Invalidate all three query keys on success so
        // the KPI counters + Recent Calls + Queue Backlog refresh instantly.
        onSuccess={() => {
          qc.invalidateQueries({ queryKey: ["dashboard:stats"] });
          qc.invalidateQueries({ queryKey: ["dashboard:recent-calls"] });
          qc.invalidateQueries({ queryKey: ["dashboard:queue-backlog"] });
        }}
      />
    </div>
  );
}
