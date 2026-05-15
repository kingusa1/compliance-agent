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
 *   6.  Deal lifecycle (E.ON 3-stage vs 4-stage suppliers + 6 stages + state machine)
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
    label: "Human Review Queue",
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
  { num: 1, title: "Upload", description: "Reviewer drops an MP3/WAV through the Upload Call modal. File posts to /api/calls/upload; a Call row is created with status=processing and a SHA-256 content hash is recorded for dedup." },
  { num: 2, title: "Storage", description: "Object stored in the call-audio Supabase bucket (max 200 MB). Backend obtains a signed URL for transcription." },
  { num: 3, title: "Inngest event (optional)", description: "Durable workflow path emits call/uploaded so each step can retry independently. Live deployments currently use the asyncio path (USE_INNGEST_PIPELINE=false) — Inngest stays warmed for failover." },
  { num: 4, title: "Transcribe", description: "Deepgram Nova-3 (en-GB, EU region) with diarisation + smart-format + sentiment + intents + topics + summary. Per-word speakers come back as numeric ids and are role-tagged AGENT/CUSTOMER server-side." },
  { num: 5, title: "Detect supplier (AI)", description: "Opus 4.7 receives the first ~3000 words and names the energy supplier whose tariff is being sold. Sibling-supplier inheritance fills in when an LOA-only call doesn't mention the supplier explicitly." },
  { num: 6, title: "Content classifier (AI, 2026-05-12 rebuild)", description: "Opus 4.7 reads the transcript and emits 1–4 segments — lead_gen / pre_sales / verbal / loa — with word-index boundaries. Each segment grades against its OWN rubric; worst-bucket-wins aggregates to one call-level verdict. Filename hints are NOT used; pure content classification. SegmentCards on the call detail show the AI's per-segment reasoning + time range." },
  { num: 7, title: "Detect names + meters", description: "detect_names() pulls agent + customer; name_normaliser snaps to existing roster entries above a 0.78 similarity threshold. meter_extractor regex pulls MPAN (13 digits) + MPRN (6-10 digits) into CustomerDeal." },
  { num: 8, title: "Phrase pre-pass", description: "15 cheap regex patterns scan for Critical signals (Watt identity false-employ, VAT-not-clear, guaranteed-rates, savings-misrep, vulnerability indicators). Hits become CRITICAL evidence injected into the LLM prompt." },
  { num: 9, title: "Watt analyzer (Opus 4.7)", description: "Per-checkpoint analyzer reads the matched supplier-script's checkpoint list and grades each one PASS / PARTIAL / FAIL with evidence quotes + line numbers." },
  { num: 10, title: "Auto-escalate", description: "If any CRITICAL regex fired in step 8, verdict is forced to BLOCK regardless of LLM output. The LLM verdict is preserved as llm_verdict for audit." },
  { num: 11, title: "Vulnerability detect", description: "Per-call scan for vulnerable-customer indicators (age, disability, financial hardship, language). Surfaces a VulnerabilityBanner on the reviewer page." },
  { num: 12, title: "Pricing mismatch flag", description: "Compares the closer-call's quoted unit-rate against the supplier-published rate sheet. Mismatch surfaces a PricingMismatchBanner." },
  { num: 13, title: "Quality agent (Opus 4.7)", description: "Cross-call identity resolution. Snaps fuzzy customer names + agent names to existing canonical rows; flips lifecycle status for any newly-bundled deals." },
  { num: 14, title: "Persist + Tracker", description: "Call.compliance_status / score / reason / risk_tags + per-checkpoint CallCheckpoint rows committed. Rejection rows are idempotent on (call_id, rejection_reason). /tracker shows the live view." },
  { num: 15, title: "Email + weekly escalation (optional)", description: "If FEEDBACK_EMAIL_API is set, per-rejection digest emails fire to agents. Weekly cron escalates agents with ≥ 3 CRITICAL rejections in trailing 7 days." },
];

const AI_CLASSIFIER_RULES: { stage: string; tell: string; signals: string }[] = [
  {
    stage: "lead_gen",
    tell: "FIRST contact — cold/warm intro. Captures decision-maker + contract end date. No verbal contract, no LOA wording.",
    signals: "\"is that [name]?\", \"I'm calling from [broker]\", \"are you the decision maker\", \"shall I send across some prices\", \"I'll pass you to my colleague\"",
  },
  {
    stage: "pre_sales",
    tell: "WARM HANDOVER + pre-contract recap at the start of the closer recording. Lead-gen agent transfers, closer reconfirms identity / postcode / authority before reading the contract.",
    signals: "\"I'll just pass you over to [name]\", \"my colleague [name] tells me…\", \"let me re-confirm your details\", two distinct agent voices in one recording.",
  },
  {
    stage: "verbal",
    tell: "LEGALLY BINDING verbal contract. Rate p/kWh + standing charge + contract length + Ombudsman + customer \"yes\". For E.ON, LOA wording is bundled INTO this segment.",
    signals: "\"this is a legally binding contract\", \"do you agree to be bound\", customer says \"yes\" to affirmation blocks, explicit rate + standing charge read.",
  },
  {
    stage: "loa",
    tell: "Letter of Authority. Standalone call for non-E.ON suppliers; bundled into the verbal segment for E.ON.",
    signals: "\"verbal letter of authorization\", \"do you authorise Watt to act on your behalf\", \"12 months\", \"authority to negotiate with [supplier]\".",
  },
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
    title: "Severity → bucket (server) → reviewer surface",
    description: "Severity ranks the breach; the server computes a bucket; the reviewer sees only three buttons (2026-05-13 rebuild).",
    rows: [
      "CRITICAL → bucket=blocked → reviewer surface: Non-Compliant",
      "HIGH → bucket=review → reviewer surface: Needs Review",
      "MEDIUM → bucket=coaching → reviewer surface: Pass (note logged)",
      "Reviewer's 3-pill choice: Pass / Needs Review / Non-Compliant — COACHING + BLOCK are server-only.",
    ],
  },
  {
    title: "5 risk tags (Plan §5b)",
    description: "What kind of regulatory exposure this call carries. Only rendered on Needs Review / Non-Compliant verdicts.",
    rows: [
      "Ombudsman · Mis-selling · Complaint · Cancellation · Vulnerable",
    ],
  },
  {
    title: "4 canonical segments (2026-05-12 lockdown)",
    description: "What stage of the deal this recording's content covers. The AI emits 1–4 segments per recording; each grades against its own rubric.",
    rows: [
      "lead_gen · pre_sales · verbal · loa",
    ],
  },
  {
    title: "Intelligence panel (Plan §5f, 2026-05-13)",
    description: "Dashboard surfaces 4 read-only aggregations over completed calls.",
    rows: [
      "Compliance % by supplier — bar chart, descending by call volume.",
      "Top-10 agents by compliance % — min 3 calls.",
      "Calls by call_type — donut (lead_gen / pre_sales / verbal / loa).",
      "30-day compliance trend — weekly buckets, polyline.",
    ],
  },
];

const LIFECYCLE_TABLE: { supplier: string; phases: string[]; required: number; corrective: string[]; note: string }[] = [
  {
    supplier: "E.ON Next",
    phases: ["lead_gen", "pre_sales", "verbal", "loa"],
    required: 2,
    corrective: [],
    note: "Opener = Lead Gen recording. Closer = Pre-Sales + Verbal + LOA (LOA wording is bundled INSIDE the Closer recording — no separate LOA file).",
  },
  {
    supplier: "British Gas",
    phases: ["lead_gen", "pre_sales", "verbal"],
    required: 2,
    corrective: [],
    note: "Opener = Lead Gen recording. Closer = Pre-Sales + Verbal. LOA is a DocuSign paper document — never a recording.",
  },
  {
    supplier: "British Gas Lite (BGL)",
    phases: ["lead_gen", "pre_sales", "verbal"],
    required: 2,
    corrective: [],
    note: "Opener = Lead Gen recording. Closer = Pre-Sales + Verbal. LOA is a DocuSign paper document.",
  },
  {
    supplier: "EDF Energy",
    phases: ["lead_gen", "pre_sales", "verbal"],
    required: 2,
    corrective: [],
    note: "Opener = Lead Gen recording. Closer = Pre-Sales + Verbal. LOA is a DocuSign paper document.",
  },
  {
    supplier: "Scottish Power",
    phases: ["lead_gen", "pre_sales", "verbal"],
    required: 2,
    corrective: [],
    note: "Opener = Lead Gen recording. Closer = Pre-Sales + Verbal. LOA is a DocuSign paper document.",
  },
  {
    supplier: "Pozitive",
    phases: ["lead_gen", "pre_sales", "verbal"],
    required: 2,
    corrective: [],
    note: "Opener = Lead Gen recording. Closer = Pre-Sales + Verbal. LOA is a DocuSign paper document.",
  },
];

const STAGE_DETAILS: {
  key: string;
  label: string;
  blurb: string;
  filenameHints: string[];
}[] = [
  {
    key: "lead_gen",
    label: "Lead Gen",
    blurb:
      "Cold/warm intro. Watt agent introduces themselves, qualifies interest, captures site + contract details. Identity disclosure happens here.",
    filenameHints: ["lead.mp3", "Lead Gen.mp3", "LG.mp3", "lg.mp3"],
  },
  {
    key: "pre_sales",
    label: "Pre-Sales",
    blurb:
      "Warm handover + pre-contract recap inside the closer recording. Lead-gen passes the customer to the closer; closer re-confirms identity, postcode, decision-maker authority before reading the verbal contract.",
    filenameHints: ["passover.mp3", "pre-sales.mp3"],
  },
  {
    key: "verbal",
    label: "Verbal",
    blurb:
      "Legally-binding verbal contract reading. Closing agent reads the supplier script, customer agrees, deal is captured. For E.ON, the LOA section is bundled INTO this segment.",
    filenameHints: ["verbal.mp3", "closer.mp3", "full call.mp3"],
  },
  {
    key: "loa",
    label: "LOA (E.ON only)",
    blurb:
      "Letter-of-Authority. For E.ON Next the LOA wording is bundled into the Closer recording, so the system grades it as an inner segment. For every OTHER supplier the LOA is a DocuSign paper document — it's NOT a recording and the AI never emits an LOA segment for them.",
    filenameHints: ["(E.ON closer .mp3 — LOA is inside)"],
  },
];

const LIFECYCLE_STATES: { state: string; meaning: string }[] = [
  { state: "open", meaning: "No qualifying call yet." },
  { state: "lead_gen_done", meaning: "Opener (Lead Gen) signed off — Closer still pending." },
  {
    state: "pre_sales_done",
    meaning:
      "Pre-Sales segment inside the Closer signed off — Verbal still pending in the same Closer recording.",
  },
  {
    state: "verbal_done",
    meaning:
      "Verbal contract signed off. For non-E.ON this is enough to verify the recording side; LOA arrives as a DocuSign document.",
  },
  {
    state: "loa_done",
    meaning:
      "LOA segment captured (E.ON only — it's INSIDE the Closer recording).",
  },
  { state: "verified", meaning: "Every required segment + the DocuSign LOA (non-E.ON only) finalised. Deal is contractually complete." },
  { state: "rejected", meaning: "Terminal. Manual reviewer override." },
];

const REVIEWER_STEPS: { num: number; title: string; description: string }[] = [
  { num: 1, title: "Open the Human Review Queue", description: "Sidebar → Human Review Queue. The list shows calls awaiting sign-off, sorted by oldest first." },
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
    cause: "Pipeline failed at one of the 15 steps (likely transcription if Deepgram throttled, or LLM if OpenRouter is rate-limited).",
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
              { title: "Review on the Queue", body: "If the AI flagged the call, sidebar → Human Review Queue → Claim → accept the verdict or override with your own. Done." },
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
        <Section id="pipeline" icon={GitBranch} title="Upload → verdict pipeline (15 steps)">
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
        <Section id="lifecycle" icon={ListChecks} title="Deal lifecycle — 2-stage Opener / Closer (2026-05-14)">
          {/* Headline rule */}
          <div className="mb-5 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4 text-[13px] leading-relaxed text-emerald-100/90">
            Every supplier follows the same{" "}
            <strong className="text-emerald-100">2 top-level deal stages</strong>:{" "}
            <strong className="text-emerald-100">Opener</strong> (the Lead Gen recording) and{" "}
            <strong className="text-emerald-100">Closer</strong> (the contract-binding recording).
            The supplier-specific twist is what segments live INSIDE the Closer:
            <ul className="mt-2 ml-5 list-disc space-y-1">
              <li>
                <strong>E.ON Next</strong> — Closer contains Pre-Sales + Verbal +{" "}
                <strong>LOA</strong> (LOA wording is bundled INTO the Closer recording).
              </li>
              <li>
                <strong>Every other supplier</strong> — Closer contains Pre-Sales + Verbal only.
                <strong> LOA is a DocuSign paper document, not a recording</strong> —
                the system never grades an LOA segment for non-E.ON.
              </li>
            </ul>
          </div>

          {/* The 4 canonical inner segments */}
          <h3 className="mb-2 text-[14px] font-semibold text-[var(--text-primary)]">The 4 inner segments the AI emits</h3>
          <p className="mb-3 text-[12.5px] text-[var(--text-muted)]">
            Every recording you upload is classified as one of these stages.
            Classification happens (a) from the filename if the basename matches a known
            hint, then (b) from the audio content via Opus 4.7. The stage is stored as{" "}
            <code className="font-mono text-[11.5px]">Call.call_type</code> and feeds the
            deal-lifecycle resolver.
          </p>
          <div className="mb-6 grid gap-2.5 md:grid-cols-2">
            {STAGE_DETAILS.map((s) => (
              <div
                key={s.key}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3"
              >
                <div className="text-[13px] font-semibold text-[var(--text-primary)]">{s.label}</div>
                <div className="mt-1 text-[12px] leading-relaxed text-[var(--text-muted)]">
                  {s.blurb}
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {s.filenameHints.map((h) => (
                    <span
                      key={h}
                      className="rounded bg-[var(--bg-elev3)] px-1.5 py-0.5 font-mono text-[10.5px] text-[var(--text-muted)]"
                    >
                      {h}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Per-supplier required stages */}
          <h3 className="mb-2 text-[14px] font-semibold text-[var(--text-primary)]">Per-supplier required stages</h3>
          <div className="mb-6 overflow-hidden rounded-xl border border-[var(--border-subtle)]">
            <table className="w-full text-[12.5px]">
              <thead className="bg-[var(--bg-elev2)] text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                <tr>
                  <th className="px-3 py-2 text-left">Supplier</th>
                  <th className="px-3 py-2 text-left">Required phases</th>
                  <th className="px-3 py-2 text-center">#</th>
                  <th className="px-3 py-2 text-left">Corrective (any time)</th>
                  <th className="px-3 py-2 text-left">Note</th>
                </tr>
              </thead>
              <tbody>
                {LIFECYCLE_TABLE.map((r) => (
                  <tr key={r.supplier} className="border-t border-[var(--border-subtle)]">
                    <td className="px-3 py-2 font-medium text-[var(--text-primary)]">{r.supplier}</td>
                    <td className="px-3 py-2 font-mono text-[var(--text-muted)]">{r.phases.join(" → ")}</td>
                    <td className="px-3 py-2 text-center font-mono tabular-nums text-[var(--emerald-400)]">{r.required}</td>
                    <td className="px-3 py-2 font-mono text-[var(--text-dim)]">{r.corrective.join(" / ")}</td>
                    <td className="px-3 py-2 text-[var(--text-muted)]">{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* How status is computed */}
          <h3 className="mb-2 text-[14px] font-semibold text-[var(--text-primary)]">
            How the deal status is computed
          </h3>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[var(--text-muted)]">
            Every time a call finalises, the backend runs{" "}
            <code className="font-mono text-[11.5px]">derive_lifecycle_status(deal, calls)</code>{" "}
            in <code className="font-mono text-[11.5px]">backend/app/deal_lifecycle.py</code>.
            It collects the set of phases completed (from each call&apos;s{" "}
            <code className="font-mono text-[11.5px]">call_type</code>), compares against
            the supplier-specific required list, and returns one of:
          </p>
          <ul className="space-y-1 text-[12.5px] text-[var(--text-muted)]">
            {LIFECYCLE_STATES.map((s) => (
              <li key={s.state} className="flex items-start gap-2">
                <span className="mt-1.5 size-1 flex-shrink-0 rounded-full bg-[var(--emerald-400)]" />
                <span>
                  <strong className="text-[var(--text-primary)]">{s.state}</strong> — {s.meaning}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[12.5px] leading-relaxed text-[var(--text-muted)]">
            A single recording can contain MULTIPLE segments — an E.ON-style
            closer typically packs <strong>Pre-Sales + Verbal + LOA</strong> into one
            audio file. The 2026-05-12 content classifier slices the transcript by
            word-index boundaries and grades each segment against its own rubric;
            the call-level verdict aggregates worst-bucket-wins across segments.
          </p>

          {/* How the AI classifies the segments inside a recording */}
          <h3 className="mt-6 mb-2 text-[14px] font-semibold text-[var(--text-primary)]">
            How the AI decides which segments a recording contains
          </h3>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[var(--text-muted)]">
            Before the 2026-05-12 rebuild, stage was guessed from the filename and
            the whole recording got ONE rubric — so an E.ON closer that legitimately
            mixed pre-sales + verbal + LOA wording was graded against a single
            checklist. Now <strong>Claude Opus 4.7</strong> reads the transcript and
            emits 1-4 segments with word-index boundaries; each segment routes to
            its own rubric. Filenames are ignored. The rule the model is given:
          </p>
          <div className="overflow-hidden rounded-xl border border-[var(--border-subtle)]">
            <table className="w-full text-[12.5px]">
              <thead className="bg-[var(--bg-elev2)] text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                <tr>
                  <th className="px-3 py-2 text-left">Stage</th>
                  <th className="px-3 py-2 text-left">What it is</th>
                  <th className="px-3 py-2 text-left">Phrasal signals the model looks for</th>
                </tr>
              </thead>
              <tbody>
                {AI_CLASSIFIER_RULES.map((r) => (
                  <tr key={r.stage} className="border-t border-[var(--border-subtle)]">
                    <td className="px-3 py-2 font-mono text-[var(--emerald-400)]">
                      {r.stage}
                    </td>
                    <td className="px-3 py-2 text-[var(--text-primary)]">{r.tell}</td>
                    <td className="px-3 py-2 text-[var(--text-muted)]">{r.signals}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[12.5px] leading-relaxed text-[var(--text-muted)]">
            If the classifier can&apos;t identify any segment with confidence &gt;= 0.5
            (transcript too short, single voice indistinguishable, garbled audio), the
            call lands as <code className="font-mono text-[11.5px]">needs_classification</code>
            for manual triage. Reviewers can re-assign segments on the call detail
            page; the SegmentCards stack on the Checkpoints tab + the inline
            chips on the call header show the AI&apos;s detected segments together with
            classifier confidence so the reviewer can audit at a glance.
          </p>
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
