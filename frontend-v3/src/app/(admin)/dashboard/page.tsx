"use client";

/**
 * /dashboard — at-a-glance home for admin / lead / reviewer users.
 *
 * Three sections:
 *   1) KPI strip — total calls / compliant / non-compliant / compliance rate
 *   2) Quick-start guide that auto-collapses once 1+ call has been processed
 *   3) Quick-action tiles linking into the heavy pages (Tracker, Customers,
 *      Deals, Scripts, Rejections, Observability)
 *
 * Every tile has a one-sentence description so a fresh user can navigate
 * the system without prior training. The only blocking action on a fresh
 * install is "Upload your first call" — everything else is reachable from
 * here in one click.
 */
import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Upload,
  Inbox,
  Users,
  Briefcase,
  ListChecks,
  ShieldCheck,
  ShieldAlert,
  Table as TableIcon,
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Sparkles,
} from "lucide-react";

import { apiFetch } from "@/lib/api";
import { UploadModal } from "@/app/(admin)/calls/UploadModal";

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

interface KPI {
  label: string;
  value: string | number;
  hint: string;
  href?: string;
  tone: "neutral" | "good" | "bad" | "warn";
}

interface QuickAction {
  href: string;
  label: string;
  icon: typeof Inbox;
  description: string;
  tone: "primary" | "neutral";
}

const QUICK_ACTIONS: QuickAction[] = [
  {
    href: "/queue",
    label: "Review Queue",
    icon: Inbox,
    description: "Calls waiting for human sign-off. Claim a call, accept or override the AI verdict.",
    tone: "primary",
  },
  {
    href: "/tracker",
    label: "Tracker",
    icon: TableIcon,
    description: "Full operational tracker — every call + every rejection in the Watt XLSX shape.",
    tone: "primary",
  },
  {
    href: "/customers",
    label: "Customers",
    icon: Users,
    description: "Customer rollup: deals + calls + lifecycle status per customer.",
    tone: "neutral",
  },
  {
    href: "/deals",
    label: "Deals",
    icon: Briefcase,
    description: "Multi-call deals: Lead Gen → Closer → LOA → Confirmation.",
    tone: "neutral",
  },
  {
    href: "/scripts",
    label: "Scripts",
    icon: ListChecks,
    description: "15 supplier scripts (BGL, BG, EDF, EON, Pozitive, SP) and their checkpoint sets.",
    tone: "neutral",
  },
  {
    href: "/rejections",
    label: "Rejections",
    icon: AlertTriangle,
    description: "Open rejections by category, owner, supplier — track to fixed/dead.",
    tone: "neutral",
  },
  {
    href: "/observability",
    label: "Observability",
    icon: Activity,
    description: "Live pipeline runs, stuck calls, audit log. Visit when something looks off.",
    tone: "neutral",
  },
  {
    href: "/compliant",
    label: "Compliant",
    icon: ShieldCheck,
    description: "Clean audit trail of calls signed off as compliant.",
    tone: "neutral",
  },
  {
    href: "/non-compliant",
    label: "Non-compliant",
    icon: ShieldAlert,
    description: "Calls flagged non-compliant — triage and escalate.",
    tone: "neutral",
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
    title: "Sign off or override on the Review Queue",
    description:
      "If the AI flagged it, claim the call and either accept or override. Your decision is the audit-of-record.",
  },
];

function useStats() {
  return useQuery({
    queryKey: ["dashboard:stats"] as const,
    queryFn: () => apiFetch<StatsResponse>("/api/stats"),
    staleTime: 60_000,
  });
}

export default function DashboardPage() {
  const stats = useStats();
  const [uploadOpen, setUploadOpen] = useState(false);
  const isFreshInstall = (stats.data?.total_calls ?? 0) === 0;

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
          ? `${Math.round(stats.data.compliance_rate * 100)}%`
          : "—",
      hint: "Across all reviewed calls",
      tone: stats.data?.compliance_rate != null && stats.data.compliance_rate >= 0.8 ? "good" : "warn",
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

      <div className="px-6 py-6 space-y-6">
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
                <h2 className="text-[15px] font-semibold text-emerald-200">Welcome — three steps to get going</h2>
                <p className="mt-1 text-[12.5px] text-emerald-200/80">
                  No calls have been processed yet. Once you upload one, this guide auto-hides
                  and the KPIs above light up.
                </p>
                <ol className="mt-4 space-y-3">
                  {QUICK_START_STEPS.map((s, i) => (
                    <li key={s.title} className="flex items-start gap-3">
                      <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-emerald-500/15 text-[12px] font-medium text-emerald-200 ring-1 ring-emerald-500/30">
                        {i + 1}
                      </span>
                      <div>
                        <div className="text-[13.5px] font-medium text-emerald-100">{s.title}</div>
                        <div className="text-[12.5px] text-emerald-200/80">{s.description}</div>
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
                    href="/scripts"
                    className="inline-flex items-center gap-2 text-[12.5px] text-emerald-200/90 hover:text-emerald-100"
                  >
                    Browse the 15 supplier scripts <ArrowRight className="size-3.5" />
                  </Link>
                </div>
              </div>
            </div>
          </section>
        ) : null}

        {/* "What's where" grid */}
        <section>
          <div className="mb-3 flex items-center gap-2">
            <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">Where to go next</h2>
            <span className="text-[12px] text-[var(--text-muted)]">— click any card</span>
          </div>
          <div
            className="grid gap-3"
            style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}
          >
            {QUICK_ACTIONS.map((a) => {
              const Icon = a.icon;
              return (
                <Link
                  key={a.href}
                  href={a.href}
                  className="group rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4 transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--bg-elev2)]"
                >
                  <div className="flex items-start justify-between">
                    <div className="grid size-9 place-items-center rounded-lg bg-[var(--bg-elev3)]">
                      <Icon className="size-4 text-[var(--emerald-400)]" />
                    </div>
                    <ArrowRight className="size-4 text-[var(--text-dim)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--text-muted)]" />
                  </div>
                  <div className="mt-3 text-[14px] font-semibold text-[var(--text-primary)]">
                    {a.label}
                  </div>
                  <div className="mt-1 text-[12.5px] leading-snug text-[var(--text-muted)]">
                    {a.description}
                  </div>
                </Link>
              );
            })}
          </div>
        </section>

        {/* System state footer */}
        <section className="flex items-center gap-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4 text-[12.5px] text-[var(--text-muted)]">
          <CheckCircle2 className="size-4 text-emerald-400" />
          <div>
            System operational · Frontend on Vercel · Backend on Railway · Postgres on Supabase ·
            Opus 4.7 via OpenRouter · Deepgram Nova-3 (en-GB, EU region).
          </div>
        </section>
      </div>

      <UploadModal open={uploadOpen} onOpenChange={setUploadOpen} />
    </div>
  );
}
