"use client";

/**
 * /guide — comprehensive in-app user manual.
 *
 * Sections covered (one click each via the side ToC):
 *   1.  What this system does
 *   2.  Quick start (3 steps)
 *   3.  Pages explained (Dashboard, Queue, Tracker, …)
 *   4.  How an upload becomes a verdict (the 13-step pipeline)
 *   5.  Compliance taxonomy (8 standards / 27 codes / 4 categories /
 *       3 severities / 4 risk tags / 8 call types)
 *   6.  Deal lifecycle (E.ON 2-stage vs 3-stage suppliers)
 *   7.  Reviewer playbook (claim → review → sign-off / override)
 *   8.  Glossary
 *   9.  Troubleshooting + where to get help
 *
 * Designed to stand alone. A new reviewer can read this page once and
 * be operational without prior training.
 */
import { useState } from "react";
import Link from "next/link";
import {
  Sparkles,
  Upload,
  ListChecks,
  Inbox,
  Table as TableIcon,
  Users,
  Briefcase,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Activity,
  BarChart3,
  Settings,
  BookOpen,
  CheckCircle2,
  CircleHelp,
  Zap,
  GitBranch,
  Shield,
  ScrollText,
  ChevronRight,
  Mail,
} from "lucide-react";

// ─── Section metadata for ToC + scrollspy ────────────────────────────────

interface Section {
  id: string;
  label: string;
  icon: typeof BookOpen;
}

const SECTIONS: Section[] = [
  { id: "what", label: "What this system does", icon: Sparkles },
  { id: "quick-start", label: "Quick start", icon: Zap },
  { id: "pages", label: "Pages explained", icon: BookOpen },
  { id: "pipeline", label: "Upload → verdict pipeline", icon: GitBranch },
  { id: "taxonomy", label: "Compliance taxonomy", icon: Shield },
  { id: "lifecycle", label: "Deal lifecycle", icon: ListChecks },
  { id: "reviewer", label: "Reviewer playbook", icon: Inbox },
  { id: "glossary", label: "Glossary", icon: ScrollText },
  { id: "help", label: "Troubleshooting + help", icon: CircleHelp },
];

// ─── Page content blocks ─────────────────────────────────────────────────

const PAGE_DESCRIPTIONS: { href: string; label: string; icon: typeof Inbox; tagline: string; usedFor: string }[] = [
  {
    href: "/dashboard",
    label: "Dashboard",
    icon: Sparkles,
    tagline: "At-a-glance home.",
    usedFor: "First page after sign-in. Shows total calls / compliance rate / quick-start guide. Click any KPI tile to drill in.",
  },
  {
    href: "/queue",
    label: "Review Queue",
    icon: Inbox,
    tagline: "Calls waiting for human sign-off.",
    usedFor: "If the AI flagged a call for human review you'll find it here. Click claim → review → accept or override the verdict.",
  },
  {
    href: "/tracker",
    label: "Tracker",
    icon: TableIcon,
    tagline: "Operational ledger — mirrors Watt's XLSX.",
    usedFor: "Every call + every rejection in one table. 5 tabs (Active / Awaiting Review / Fixed / Dead / Compliant). Click a row to see verdict + evidence.",
  },
  {
    href: "/rejections",
    label: "Rejections",
    icon: AlertTriangle,
    tagline: "All open rejections, by category and supplier.",
    usedFor: "Triage and fix non-compliant calls. Status moves Active → Fixed (or Dead) and tracks each rejection's audit log.",
  },
  {
    href: "/customers",
    label: "Customers",
    icon: Users,
    tagline: "One row per customer, with deal + call rollup.",
    usedFor: "See all calls + deals per customer + deal value rolled up. Click a customer for the full timeline view.",
  },
  {
    href: "/deals",
    label: "Deals",
    icon: Briefcase,
    tagline: "Multi-call deals with lifecycle phase.",
    usedFor: "A deal = 1 customer + 1 supplier + multiple calls (lead-gen → closer → LOA → confirmation). Use this to chase missing calls.",
  },
  {
    href: "/agents",
    label: "Agents",
    icon: BarChart3,
    tagline: "Sales agents performance leaderboard.",
    usedFor: "Compliance rate per agent over rolling 30 days. Drill in to see their calls + recent flags.",
  },
  {
    href: "/scripts",
    label: "Scripts",
    icon: ListChecks,
    tagline: "15 supplier scripts (BGL × 2, BG × 2, EDF × 2, EON × 5, Pozitive × 1, SP × 3).",
    usedFor: "Browse the canonical compliance scripts the LLM uses to grade each call. Reference for what the agent should be saying.",
  },
  {
    href: "/compliant",
    label: "Compliant",
    icon: ShieldCheck,
    tagline: "Calls signed off as compliant — clean audit trail.",
    usedFor: "Read-only list of calls that passed both AI + human review. Use this when an auditor asks for the clean ledger.",
  },
  {
    href: "/non-compliant",
    label: "Non-compliant",
    icon: ShieldAlert,
    tagline: "Calls flagged non-compliant — triage and escalate.",
    usedFor: "Open issues that need a fix or an amendment call. Click into each call's workbench to add a directive.",
  },
  {
    href: "/observability",
    label: "Observability",
    icon: Activity,
    tagline: "Pipeline runs, stuck calls, audit log.",
    usedFor: "Visit when something looks off (no verdict / call stuck in 'processing'). Redispatch a call from here.",
  },
  {
    href: "/settings",
    label: "Settings",
    icon: Settings,
    tagline: "Account, density, model, transcription tabs.",
    usedFor: "Switch the LLM provider, change UI density, see your account info.",
  },
];

const PIPELINE_STEPS: { num: number; title: string; description: string }[] = [
  { num: 1, title: "Upload", description: "Reviewer drops an MP3/WAV through the Upload Call modal. File goes to Supabase Storage; a Call row is created with status=processing." },
  { num: 2, title: "Storage", description: "Object stored in the call-audio bucket (max 25 MB). Backend gets a signed URL to read it." },
  { num: 3, title: "Inngest event (optional)", description: "If durable workflow is enabled, a call/uploaded event fires so each step can retry independently." },
  { num: 4, title: "Transcribe", description: "Deepgram Nova-3 (en-GB, EU region) runs first. Optional consensus from AssemblyAI / Speechmatics / Groq / Cohere if their keys are set." },
  { num: 5, title: "Detect metadata", description: "script_detect.detect() reads the transcript and returns supplier / script_type / call_class — backed by 25+ regex patterns." },
  { num: 6, title: "Phrase pre-pass", description: "10–15 cheap regex rules scan for Critical phrases (Watt-Utilities-identity, savings claim, guarantee, vulnerability signal, …)." },
  { num: 7, title: "Watt LLM", description: "Claude Opus 4.7 via OpenRouter. Prompt includes the 8 Watt Standards, 27 rejection codes, and call-type-specific focus block (LOA vs closer vs lead-gen)." },
  { num: 8, title: "Auto-escalate", description: "If any CRITICAL regex fired, verdict is forced to BLOCK regardless of LLM output. The LLM verdict is preserved as llm_verdict for audit." },
  { num: 9, title: "Risk tag normalise", description: "LLM-emitted tags get coerced to the canonical 4 (ombudsman_risk / mis_selling_risk / complaint_risk / cancellation_risk). Unknown tags drop." },
  { num: 10, title: "Persist", description: "Call.compliance_status / score / reason / risk_tags get updated. One Rejection row per item, idempotent on call_id." },
  { num: 11, title: "Tracker export", description: "JOINs Call × Rejection × CustomerDeal × Customer to produce the XLSX in the exact shape of the Watt operations template." },
  { num: 12, title: "Email feedback (optional)", description: "If FEEDBACK_EMAIL_API keys are set, a per-rejection digest email is dispatched to the agent." },
  { num: 13, title: "Weekly escalation (optional)", description: "Cron returns agents with ≥ 3 critical rejections in the trailing 7 days, into a digest sent to leads." },
];

const COMPLIANCE_TAXONOMY: { title: string; description: string; rows: string[] }[] = [
  {
    title: "8 Watt standards",
    description: "Single source of truth in backend/app/watt_compliance/taxonomy.py.",
    rows: [
      "Standard 1 — Caller identity (Watt brand stated, no impersonation)",
      "Standard 2 — Recording disclosure (before sales pitch)",
      "Standard 3 — Honest claims (no guarantees, no will-save, no urgency)",
      "Standard 4 — Whole-of-market (no 'we cover the whole market')",
      "Standard 5 — Objection handling (acknowledge, never steamroll)",
      "Standard 6 — Authorisation (decision-maker confirmed, LOA captured)",
      "Standard 7 — Script adherence (no script downplay on verbal calls)",
      "Standard 8 — Commission disclosure",
    ],
  },
  {
    title: "27 rejection codes (R01–R27)",
    description: "Each code maps to (category, severity, standard) — see /scripts page for the full table.",
    rows: [
      "R01 Caller identity not given · CRITICAL · Std 1",
      "R02 Recording disclosure missing · HIGH · Std 2",
      "R09 Guarantee phrase · CRITICAL · Std 3",
      "R20 Unauthorised supplier impersonation · CRITICAL · Std 1",
      "… and 23 more across categories ADMIN_ERROR / PROCESS_FAILURE / COMPLIANCE_ISSUE / VERBAL_SALES_ERROR.",
    ],
  },
  {
    title: "Severity → action",
    description: "Severity ranks the breach; the action is the verdict the system pushes.",
    rows: [
      "CRITICAL → BLOCK (Ofgem-reportable risk, refuse the deal)",
      "HIGH → REVIEW (reviewer must decide)",
      "MEDIUM → COACH (coaching note only, deal still ships)",
    ],
  },
  {
    title: "4 risk tags",
    description: "What kind of regulatory exposure this call carries.",
    rows: [
      "ombudsman_risk · mis_selling_risk · complaint_risk · cancellation_risk",
    ],
  },
  {
    title: "8 call types",
    description: "What stage of the deal this recording covers.",
    rows: [
      "lead_gen · passover · closer · verbal · loa · c_call · amendment · full",
    ],
  },
];

const LIFECYCLE_TABLE: { supplier: string; phases: string[]; required: number; corrective: string[] }[] = [
  { supplier: "E.ON Next", phases: ["lead_gen", "closer"], required: 2, corrective: ["c_call", "amendment"] },
  { supplier: "British Gas", phases: ["lead_gen", "closer", "standalone_loa"], required: 3, corrective: ["c_call", "amendment"] },
  { supplier: "EDF Energy", phases: ["lead_gen", "closer", "standalone_loa"], required: 3, corrective: ["c_call", "amendment"] },
  { supplier: "Scottish Power", phases: ["lead_gen", "closer", "standalone_loa"], required: 3, corrective: ["c_call", "amendment"] },
  { supplier: "Pozitive", phases: ["lead_gen", "closer", "standalone_loa"], required: 3, corrective: ["c_call", "amendment"] },
];

const REVIEWER_STEPS: { num: number; title: string; description: string }[] = [
  { num: 1, title: "Open the Review Queue", description: "Sidebar → Review Queue. The list shows calls awaiting sign-off, sorted by oldest first." },
  { num: 2, title: "Claim a call", description: "Click a row → 'Claim'. The system locks it to you for 10 minutes (idle-release frees it back if you walk away)." },
  { num: 3, title: "Read the AI verdict", description: "Right pane shows the LLM verdict, score, evidence quotes, and matched rejection codes. Audio player + word-timed transcript at the top." },
  { num: 4, title: "Decide", description: "Accept the AI verdict, OR override with your own. Add a fix directive if a re-record / DocuSign / new LOA is needed." },
  { num: 5, title: "Save", description: "The decision is the audit-of-record. Hash-chain audit log captures who/when/what." },
  { num: 6, title: "Move on", description: "Lock auto-releases; next reviewer can pick up another call. Your KPIs land on /agents." },
];

const GLOSSARY: { term: string; definition: string }[] = [
  { term: "Call type", definition: "What stage of the deal a recording covers — lead_gen / passover / closer / verbal / loa / c_call / amendment / full." },
  { term: "Lifecycle status", definition: "Where a deal is in its workflow — open → lead_gen_done → closer_done → verified, with c_call_done / amendment_done as corrective branches." },
  { term: "Rejection origin", definition: "Where the breach came from — audio_script, docusign, bacs, portal_state, meter_eligibility, customer_state, companies_house, credit_check, cot, … (17 values)." },
  { term: "Fix action", definition: "The corrective action a rejection demands — NEW_LOA, NEW_DOCUSIGN, AMENDMENT_CALL, REPRICE, … (15 values)." },
  { term: "Workflow state", definition: "The rejection's lifecycle — NOT_STARTED → IN_PROGRESS → FIXED → BATCHED → SUBMITTED → FIXED_AND_APPROVED, with DEAD reachable from any state." },
  { term: "TPI", definition: "Third-Party Intermediary — Watt's regulatory category under Ofgem." },
  { term: "LOA", definition: "Letter of Authority — customer's authorisation for Watt to fetch meter / usage data." },
  { term: "MPAN / MPRN", definition: "Meter Point Administration Number (electricity) / Meter Point Reference Number (gas) — the unique meter ID." },
  { term: "C-call", definition: "Confirmation call — short post-sale call to fix a soft breach without re-running the full verbal." },
  { term: "Amendment call", definition: "Re-recording of specific lines (e.g. lines 11–14) to correct one rate or missing item." },
  { term: "Standalone LOA", definition: "A separate LOA call required by every supplier except E.ON (which bundles the LOA into the closer)." },
];

const TROUBLESHOOTING: { problem: string; cause: string; fix: string }[] = [
  {
    problem: "Upload button does nothing",
    cause: "You're on the wrong page or the modal hook didn't mount.",
    fix: "Hard-refresh, then try from /dashboard or /tracker. Both have a wired-up Upload Call button in the header.",
  },
  {
    problem: "Call stuck in 'processing'",
    cause: "Pipeline failed at one of the 13 steps (likely transcription if Deepgram throttled, or LLM if OpenRouter is rate-limited).",
    fix: "Open /observability → click the call → Redispatch. Or wait 30 minutes — the redispatch watchdog auto-fires for stuck calls.",
  },
  {
    problem: "Empty list page",
    cause: "No calls have been processed yet, or the filter excludes everything.",
    fix: "Try /dashboard → +Upload Call to push your first audio through the pipeline. Then come back.",
  },
  {
    problem: "/api/me 401",
    cause: "Your Supabase session expired.",
    fix: "Sign out (click your avatar in the sidebar) and sign in again.",
  },
  {
    problem: "Verdict looks wrong",
    cause: "The LLM mis-classified, or the script detection picked the wrong script.",
    fix: "Open the call workbench, override the verdict, and add a directive. The system tracks every override — your decision becomes the audit-of-record.",
  },
];

// ─── Page component ──────────────────────────────────────────────────────

export default function GuidePage() {
  const [activeId, setActiveId] = useState<string>(SECTIONS[0]!.id);

  const scrollTo = (id: string) => {
    setActiveId(id);
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="flex h-full overflow-hidden">
      {/* ToC sidebar */}
      <aside className="w-60 flex-shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-4 py-6">
        <div className="mb-4 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          <BookOpen className="size-3.5" /> User Guide
        </div>
        <nav className="space-y-1">
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            const isActive = activeId === s.id;
            return (
              <button
                key={s.id}
                onClick={() => scrollTo(s.id)}
                className={`group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] transition-colors ${
                  isActive
                    ? "bg-[var(--bg-elev3)] text-[var(--text-primary)]"
                    : "text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
                }`}
              >
                <Icon className={`size-3.5 ${isActive ? "text-[var(--emerald-400)]" : "text-[var(--text-dim)]"}`} />
                <span className="flex-1">{s.label}</span>
                <ChevronRight className={`size-3 transition-opacity ${isActive ? "opacity-100" : "opacity-0 group-hover:opacity-50"}`} />
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="flex-1 overflow-y-auto px-8 py-8">
        {/* Hero */}
        <section className="mb-10 rounded-2xl border border-emerald-500/25 bg-gradient-to-br from-emerald-500/10 via-emerald-500/5 to-transparent p-7">
          <div className="flex items-start gap-4">
            <div className="grid size-12 place-items-center rounded-xl bg-emerald-500/15 ring-1 ring-emerald-500/30">
              <BookOpen className="size-6 text-emerald-300" />
            </div>
            <div className="flex-1">
              <h1 className="text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
                Compliance Agent — User Guide
              </h1>
              <p className="mt-2 max-w-3xl text-[14px] leading-relaxed text-[var(--text-muted)]">
                Everything you need to operate the system. Built for Watt Utilities reviewers
                — start with the Quick Start, then dive into whichever section matches what
                you're trying to do.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  onClick={() => scrollTo("quick-start")}
                  className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3.5 py-2 text-[12.5px] font-medium text-white hover:bg-emerald-700"
                >
                  <Zap className="size-3.5" /> Quick start (3 steps)
                </button>
                <button
                  onClick={() => scrollTo("pipeline")}
                  className="inline-flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-2 text-[12.5px] text-emerald-200 hover:bg-emerald-500/20"
                >
                  <GitBranch className="size-3.5" /> See how a verdict is produced
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* 1 · What this system does */}
        <Section id="what" icon={Sparkles} title="What this system does">
          <p className="text-[13.5px] leading-relaxed text-[var(--text-primary)]">
            Compliance Agent is an internal call-compliance review tool for Watt Utilities,
            an Ofgem-regulated TPI (Third-Party Intermediary). It ingests call audio,
            transcribes it through a multi-engine STT consensus, runs a Watt-canonical
            audit against the 8 Ofgem TPI standards and 27 rejection reasons, and surfaces
            the verdicts here for human sign-off. Every decision lands in a hash-chain
            audit log so the operations team has a clean, regulator-ready ledger.
          </p>
          <ul className="mt-4 grid gap-2 text-[13px] text-[var(--text-muted)]">
            {[
              "Reduces manual review time by ~80% (LLM does the first pass).",
              "Catches Ofgem-reportable breaches in seconds, not days.",
              "Mirrors Watt's existing tracker XLSX so handover is friction-free.",
              "Audit-ready by design: every verdict + every override is logged.",
            ].map((b) => (
              <li key={b} className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 size-3.5 flex-shrink-0 text-emerald-400" /> {b}
              </li>
            ))}
          </ul>
        </Section>

        {/* 2 · Quick start */}
        <Section id="quick-start" icon={Zap} title="Quick start (3 steps)">
          <ol className="space-y-4">
            {[
              { title: "Upload your first call", body: "Click the green +Upload Call button on the Dashboard or Tracker. Drop an MP3/WAV. The pipeline runs automatically (~60 seconds end-to-end on a typical call)." },
              { title: "Watch it land in the Tracker", body: "Sidebar → Tracker. The new call appears with verdict, score, and any rejections. Click the row to see evidence quotes + supplier-script alignment." },
              { title: "Review on the Queue", body: "If the AI flagged the call, sidebar → Review Queue → Claim → accept the verdict or override with your own. Done." },
            ].map((s, i) => (
              <li key={s.title} className="flex items-start gap-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4">
                <span className="grid size-8 shrink-0 place-items-center rounded-full bg-emerald-500/15 text-[14px] font-semibold text-emerald-300 ring-1 ring-emerald-500/30">
                  {i + 1}
                </span>
                <div>
                  <div className="text-[14px] font-semibold text-[var(--text-primary)]">{s.title}</div>
                  <div className="mt-1 text-[13px] text-[var(--text-muted)]">{s.body}</div>
                </div>
              </li>
            ))}
          </ol>
        </Section>

        {/* 3 · Pages explained */}
        <Section id="pages" icon={BookOpen} title="Pages explained">
          <p className="text-[13px] text-[var(--text-muted)]">
            Click any row to jump there.
          </p>
          <div className="mt-4 grid gap-2">
            {PAGE_DESCRIPTIONS.map((p) => {
              const Icon = p.icon;
              return (
                <Link
                  key={p.href}
                  href={p.href}
                  className="group flex items-start gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3.5 transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--bg-elev2)]"
                >
                  <div className="grid size-8 place-items-center rounded-md bg-[var(--bg-elev3)]">
                    <Icon className="size-4 text-[var(--emerald-400)]" />
                  </div>
                  <div className="flex-1">
                    <div className="text-[13.5px] font-medium text-[var(--text-primary)]">{p.label}</div>
                    <div className="mt-0.5 text-[12px] text-[var(--text-muted)]">{p.tagline}</div>
                    <div className="mt-1 text-[12px] text-[var(--text-dim)]">{p.usedFor}</div>
                  </div>
                  <ChevronRight className="size-4 text-[var(--text-dim)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--text-muted)]" />
                </Link>
              );
            })}
          </div>
        </Section>

        {/* 4 · Pipeline */}
        <Section id="pipeline" icon={GitBranch} title="Upload → verdict pipeline (13 steps)">
          <p className="mb-4 text-[13px] text-[var(--text-muted)]">
            What happens between the moment you click <em>Upload Call</em> and the moment a
            verdict appears on /tracker.
          </p>
          <ol className="space-y-2">
            {PIPELINE_STEPS.map((s) => (
              <li key={s.num} className="flex gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3">
                <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[var(--bg-elev3)] text-[12px] font-mono font-semibold text-[var(--emerald-400)]">
                  {s.num}
                </span>
                <div>
                  <div className="text-[13px] font-medium text-[var(--text-primary)]">{s.title}</div>
                  <div className="mt-0.5 text-[12.5px] text-[var(--text-muted)]">{s.description}</div>
                </div>
              </li>
            ))}
          </ol>
        </Section>

        {/* 5 · Compliance taxonomy */}
        <Section id="taxonomy" icon={Shield} title="Compliance taxonomy">
          <div className="grid gap-4">
            {COMPLIANCE_TAXONOMY.map((b) => (
              <div key={b.title} className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4">
                <div className="text-[14px] font-semibold text-[var(--text-primary)]">{b.title}</div>
                <div className="mt-1 text-[12px] text-[var(--text-muted)]">{b.description}</div>
                <ul className="mt-3 space-y-1 text-[12.5px] text-[var(--text-primary)]">
                  {b.rows.map((r) => (
                    <li key={r} className="flex items-start gap-2">
                      <span className="mt-1.5 size-1 flex-shrink-0 rounded-full bg-[var(--emerald-400)]" />
                      <span>{r}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Section>

        {/* 6 · Lifecycle */}
        <Section id="lifecycle" icon={ListChecks} title="Deal lifecycle (E.ON 2-stage vs others 3-stage)">
          <p className="mb-3 text-[13px] text-[var(--text-muted)]">
            E.ON Next bundles the Letter of Authority into the closer call, so it only
            needs 2 required calls. Every other supplier requires a separate LOA call (3
            required). On top, any supplier can have corrective <code>c_call</code> /
            <code>amendment</code> calls — these don't block the deal from verifying.
          </p>
          <div className="overflow-hidden rounded-xl border border-[var(--border-subtle)]">
            <table className="w-full text-[12.5px]">
              <thead className="bg-[var(--bg-elev2)] text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                <tr>
                  <th className="px-3 py-2 text-left">Supplier</th>
                  <th className="px-3 py-2 text-left">Required phases</th>
                  <th className="px-3 py-2 text-center">#</th>
                  <th className="px-3 py-2 text-left">Corrective (any time)</th>
                </tr>
              </thead>
              <tbody>
                {LIFECYCLE_TABLE.map((r) => (
                  <tr key={r.supplier} className="border-t border-[var(--border-subtle)]">
                    <td className="px-3 py-2 font-medium text-[var(--text-primary)]">{r.supplier}</td>
                    <td className="px-3 py-2 text-[var(--text-muted)] font-mono">{r.phases.join(" → ")}</td>
                    <td className="px-3 py-2 text-center font-mono tabular-nums text-[var(--emerald-400)]">{r.required}</td>
                    <td className="px-3 py-2 text-[var(--text-dim)] font-mono">{r.corrective.join(" / ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {/* 7 · Reviewer playbook */}
        <Section id="reviewer" icon={Inbox} title="Reviewer playbook">
          <ol className="space-y-3">
            {REVIEWER_STEPS.map((s) => (
              <li key={s.num} className="flex gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3.5">
                <span className="grid size-7 shrink-0 place-items-center rounded-full bg-emerald-500/15 text-[12px] font-mono font-semibold text-emerald-300 ring-1 ring-emerald-500/30">
                  {s.num}
                </span>
                <div>
                  <div className="text-[13.5px] font-medium text-[var(--text-primary)]">{s.title}</div>
                  <div className="mt-0.5 text-[12.5px] text-[var(--text-muted)]">{s.description}</div>
                </div>
              </li>
            ))}
          </ol>
        </Section>

        {/* 8 · Glossary */}
        <Section id="glossary" icon={ScrollText} title="Glossary">
          <div className="grid gap-2 sm:grid-cols-2">
            {GLOSSARY.map((g) => (
              <div key={g.term} className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3">
                <div className="text-[13px] font-semibold text-[var(--text-primary)]">{g.term}</div>
                <div className="mt-1 text-[12px] text-[var(--text-muted)]">{g.definition}</div>
              </div>
            ))}
          </div>
        </Section>

        {/* 9 · Troubleshooting */}
        <Section id="help" icon={CircleHelp} title="Troubleshooting + where to get help">
          <div className="space-y-3">
            {TROUBLESHOOTING.map((t) => (
              <div key={t.problem} className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3.5">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 size-4 flex-shrink-0 text-amber-400" />
                  <div>
                    <div className="text-[13px] font-medium text-[var(--text-primary)]">{t.problem}</div>
                    <div className="mt-1 text-[12px] text-[var(--text-muted)]">
                      <span className="text-[var(--text-dim)]">Cause:</span> {t.cause}
                    </div>
                    <div className="mt-1 text-[12px] text-[var(--text-muted)]">
                      <span className="text-emerald-400">Fix:</span> {t.fix}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-6 rounded-xl border border-emerald-500/25 bg-emerald-500/5 p-4">
            <div className="flex items-start gap-3">
              <Mail className="mt-0.5 size-4 text-emerald-300" />
              <div className="text-[12.5px] text-emerald-200/90">
                Still stuck?{" "}
                <Link href="/observability" className="underline hover:text-emerald-100">
                  Open Observability
                </Link>{" "}
                to inspect the pipeline run, or contact the engineering team. Every call
                has a redispatch button — most issues resolve themselves on a re-run.
              </div>
            </div>
          </div>
        </Section>

        {/* Footer */}
        <div className="mt-10 border-t border-[var(--border-subtle)] pt-6 text-center text-[11px] text-[var(--text-dim)]">
          Compliance Agent · Watt Utilities · Opus 4.7 · Deepgram en-GB ·
          <Link href="/dashboard" className="ml-1 hover:text-[var(--text-muted)]">
            Back to Dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}

function Section({
  id,
  icon: Icon,
  title,
  children,
}: {
  id: string;
  icon: typeof BookOpen;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="mb-10 scroll-mt-6">
      <div className="mb-4 flex items-center gap-2.5 border-b border-[var(--border-subtle)] pb-2">
        <Icon className="size-4 text-[var(--emerald-400)]" />
        <h2 className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">{title}</h2>
      </div>
      {children}
    </section>
  );
}
