"use client";

/**
 * /workflow — dedicated reference page for Watt's deal-lifecycle workflow.
 *
 * Built 2026-05-11 because the per-supplier stage rule (E.ON = 3 stages,
 * everyone else = 4 stages) was buried inside the Customer detail page
 * and the help-banner copy. Users couldn't find a single canonical
 * explanation. This page is THE place to learn / verify how the system
 * thinks about each deal.
 */
import Link from "next/link";
import {
  CheckCircle2,
  Circle,
  ArrowRight,
  Megaphone,
  Users as UsersIcon,
  FileSignature,
  Mic,
  PhoneCall,
  Wrench,
  Info,
} from "lucide-react";

type StageKey = "lead_gen" | "passover" | "closer" | "standalone_loa" | "c_call" | "amendment";

interface StageDef {
  key: StageKey;
  label: string;
  blurb: string;
  filenameHints: string[];
  icon: React.ComponentType<{ className?: string; size?: number }>;
}

const STAGES: Record<StageKey, StageDef> = {
  lead_gen: {
    key: "lead_gen",
    label: "Lead Gen",
    blurb:
      "Cold/warm introductory call. Watt agent introduces themselves, qualifies interest, captures site + contract details. Identity disclosure happens here.",
    filenameHints: ["lead.mp3", "Lead Gen.mp3", "LG.mp3", "lg.mp3"],
    icon: Megaphone,
  },
  passover: {
    key: "passover",
    label: "Passover",
    blurb:
      "Warm handover from the Lead Gen agent to the Closer. The lead agent stays on the line, introduces the closer, then drops off. Without a clean passover, the closer is essentially a cold call again.",
    filenameHints: ["passover.mp3", "Passover.mp3", "pass over"],
    icon: UsersIcon,
  },
  closer: {
    key: "closer",
    label: "Closer",
    blurb:
      "The legally-binding verbal contract reading. Closer agent reads the supplier-specific script, customer agrees, deal is captured. For E.ON, the LOA section is bundled INTO this call.",
    filenameHints: [
      "verbal.mp3",
      "Verbal.mp3",
      "closer.mp3",
      "full call.mp3",
      "FULL CALL.mp3",
    ],
    icon: Mic,
  },
  standalone_loa: {
    key: "standalone_loa",
    label: "Standalone LOA",
    blurb:
      "Separate Letter-of-Authority call required by every supplier EXCEPT E.ON. Confirms the customer authorises Watt to act on their behalf with the new supplier (data access, termination, objection resolution, billing). 12-month validity.",
    filenameHints: ["loa.mp3", "LOA.mp3", "letter of authority"],
    icon: FileSignature,
  },
  c_call: {
    key: "c_call",
    label: "C-Call",
    blurb:
      "Confirmation callback — sometimes from the SUPPLIER side, sometimes Watt. Optional corrective step, NOT required for verification but available on any supplier.",
    filenameHints: ["c call.mp3", "C call.mp3", "c_call.mp3"],
    icon: PhoneCall,
  },
  amendment: {
    key: "amendment",
    label: "Amendment",
    blurb:
      "Post-sale fix-up call when something went wrong on the verbal or LOA (wrong rate read, name correction, missing line). Optional corrective; doesn't block verified.",
    filenameHints: ["amendment.mp3", "Amendment.mp3"],
    icon: Wrench,
  },
};

interface SupplierBlock {
  name: string;
  required: StageKey[];
  reason: string;
}

const SUPPLIERS: SupplierBlock[] = [
  {
    name: "E.ON Next",
    required: ["lead_gen", "passover", "closer"],
    reason:
      "E.ON bundles the LOA confirmations INTO the Closer call, so a separate LOA recording isn't needed. The deal is fully verified after Lead Gen → Passover → Closer.",
  },
  {
    name: "British Gas",
    required: ["lead_gen", "passover", "closer", "standalone_loa"],
    reason:
      "British Gas requires a separately-recorded Letter of Authority call. The deal isn't verified until that LOA lands.",
  },
  {
    name: "British Gas Lite (BGL)",
    required: ["lead_gen", "passover", "closer", "standalone_loa"],
    reason:
      "Same as BG core — BGL needs a standalone LOA before the contract activates.",
  },
  {
    name: "EDF",
    required: ["lead_gen", "passover", "closer", "standalone_loa"],
    reason:
      "EDF's DDWA script + LOA are separate verbatim calls. Standalone LOA required.",
  },
  {
    name: "Scottish Power",
    required: ["lead_gen", "passover", "closer", "standalone_loa"],
    reason:
      "Scottish Power's For-Business tariff requires standalone authority confirmation in a separate recording.",
  },
  {
    name: "Pozitive",
    required: ["lead_gen", "passover", "closer", "standalone_loa"],
    reason:
      "Pozitive's micro-business threshold + GDPR T&Cs + termination notice each require explicit consent on a standalone LOA call.",
  },
];

function StageBadge({ stage, completed }: { stage: StageDef; completed?: boolean }) {
  const Icon = stage.icon;
  return (
    <div
      className="flex items-start gap-3 rounded-lg border px-3 py-3"
      style={{
        borderColor: completed
          ? "var(--emerald-pass)"
          : "var(--border-subtle)",
        background: completed
          ? "rgba(16, 185, 129, 0.06)"
          : "var(--bg-elev1)",
      }}
    >
      <div
        className="grid size-8 shrink-0 place-items-center rounded-md"
        style={{
          background: completed
            ? "var(--emerald-bg-strong)"
            : "var(--bg-elev3)",
          color: completed
            ? "var(--emerald-pass)"
            : "var(--text-muted)",
        }}
      >
        <Icon className="size-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <div className="text-[14px] font-semibold text-[var(--text-primary)]">
            {stage.label}
          </div>
          {completed ? (
            <CheckCircle2 className="size-3.5 text-[var(--emerald-pass)]" />
          ) : (
            <Circle className="size-3.5 text-[var(--text-dim)]" />
          )}
        </div>
        <div className="mt-1 text-[12.5px] leading-relaxed text-[var(--text-muted)]">
          {stage.blurb}
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {stage.filenameHints.map((h) => (
            <span
              key={h}
              className="rounded bg-[var(--bg-elev3)] px-1.5 py-0.5 font-mono text-[10.5px] text-[var(--text-muted)]"
            >
              {h}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function WorkflowPage() {
  return (
    <div className="flex h-screen flex-col overflow-y-auto">
      <header className="border-b border-[var(--border-subtle)] px-6 py-5">
        <h1 className="text-[24px] font-semibold tracking-tight">
          Deal-lifecycle workflow
        </h1>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          How Watt's per-supplier sales workflow maps onto the compliance
          system — what stages exist, which calls are required per supplier,
          and how the pipeline auto-detects each call's stage from its
          filename or audio content.
        </p>
      </header>

      <div className="space-y-8 px-6 py-6">
        {/* Headline rule */}
        <section className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-5">
          <div className="flex items-start gap-3">
            <Info className="mt-1 size-4 shrink-0 text-emerald-300" />
            <div>
              <h2 className="text-[16px] font-semibold text-emerald-200">
                The rule in one sentence
              </h2>
              <p className="mt-2 text-[13.5px] leading-relaxed text-emerald-100/90">
                <strong className="text-emerald-100">E.ON Next requires 3 stages</strong>{" "}
                (Lead Gen → Passover → Closer; LOA is bundled into the
                Closer). <strong className="text-emerald-100">Every other supplier requires 4 stages</strong>{" "}
                (+ Standalone LOA as a separately-recorded call).{" "}
                <strong className="text-emerald-100">Amendment</strong> and{" "}
                <strong className="text-emerald-100">C-Call</strong> are
                corrective stages available to any supplier; they never block
                verification.
              </p>
            </div>
          </div>
        </section>

        {/* All 6 stages catalogued */}
        <section>
          <h2 className="mb-3 text-[15px] font-semibold text-[var(--text-primary)]">
            The 6 lifecycle stages
          </h2>
          <p className="mb-4 text-[13px] text-[var(--text-muted)]">
            Every recording you upload is classified as one of these stages.
            Classification happens (a) from the filename if the basename
            matches a known hint, then (b) from the audio content via Opus
            4.7. The stage is stored as <code className="font-mono text-[12px]">Call.call_type</code> and
            feeds the deal-lifecycle resolver.
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            {(Object.values(STAGES) as StageDef[]).map((s) => (
              <StageBadge key={s.key} stage={s} />
            ))}
          </div>
        </section>

        {/* Per-supplier workflows */}
        <section>
          <h2 className="mb-3 text-[15px] font-semibold text-[var(--text-primary)]">
            Per-supplier required stages
          </h2>
          <p className="mb-4 text-[13px] text-[var(--text-muted)]">
            A deal reaches the <strong>verified</strong> lifecycle status only
            once <em>every</em> required stage is finalised. Below, the
            highlighted boxes are the stages the supplier requires. Amendment
            and C-Call always show at the end as optional correctives.
          </p>
          <div className="space-y-4">
            {SUPPLIERS.map((sup) => {
              const requiredSet = new Set(sup.required);
              const allStages: StageKey[] = [
                "lead_gen",
                "passover",
                "closer",
                "standalone_loa",
                "c_call",
                "amendment",
              ];
              return (
                <div
                  key={sup.name}
                  className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5"
                >
                  <div className="mb-3 flex items-baseline justify-between gap-3">
                    <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">
                      {sup.name}
                    </h3>
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11.5px] font-semibold text-emerald-300 ring-1 ring-emerald-500/30">
                      {sup.required.length} required stages
                    </span>
                  </div>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                    {allStages.map((sk) => (
                      <StageBadge
                        key={sk}
                        stage={STAGES[sk]}
                        completed={requiredSet.has(sk)}
                      />
                    ))}
                  </div>
                  <div className="mt-4 flex items-start gap-2 text-[12.5px] text-[var(--text-muted)]">
                    <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-emerald-300" />
                    <div>{sup.reason}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* How the system computes lifecycle_status */}
        <section className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5">
          <h2 className="mb-2 text-[15px] font-semibold text-[var(--text-primary)]">
            How the deal status is computed
          </h2>
          <p className="text-[13px] leading-relaxed text-[var(--text-muted)]">
            Every time a call finalises, the backend runs{" "}
            <code className="font-mono text-[12px]">derive_lifecycle_status(deal, calls)</code>{" "}
            in{" "}
            <code className="font-mono text-[12px]">backend/app/deal_lifecycle.py</code>.
            It collects the set of phases that have completed (from each
            call's <code className="font-mono text-[12px]">call_type</code>),
            compares that set against the supplier-specific required list,
            and returns one of:
          </p>
          <ul className="mt-3 space-y-1 text-[12.5px] text-[var(--text-muted)]">
            <li>
              <strong className="text-[var(--text-primary)]">open</strong> —
              no qualifying call yet.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">lead_gen_done</strong>{" "}
              — Lead Gen done; nothing else yet.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">passover_done</strong>{" "}
              — Passover landed; closer still pending.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">closer_done</strong>{" "}
              — Closer in; one or more required follow-ups still missing
              (e.g. standalone LOA for non-E.ON).
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">verified</strong>{" "}
              — every required stage finalised. The deal is contractually
              complete.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">amendment_done</strong> /{" "}
              <strong className="text-[var(--text-primary)]">c_call_done</strong>{" "}
              — corrective post-verification states.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">rejected</strong>{" "}
              — terminal. Manual reviewer override.
            </li>
          </ul>
          <p className="mt-3 text-[13px] leading-relaxed text-[var(--text-muted)]">
            A <code className="font-mono text-[12px]">full call.mp3</code>{" "}
            recording is treated specially — because such a file usually
            captures the entire E.ON-style bundled flow in one go, it counts
            as covering <strong>Lead Gen + Passover + Closer</strong>{" "}
            simultaneously, which is enough to verify any E.ON deal on its
            own.
          </p>
        </section>

        {/* Cross-link */}
        <section className="flex items-center gap-3 text-[12.5px] text-[var(--text-muted)]">
          <Link
            href="/customers"
            className="inline-flex items-center gap-1 text-emerald-300 hover:text-emerald-200"
          >
            See a real customer's workflow
            <ArrowRight className="size-3" />
          </Link>
          <span>·</span>
          <Link
            href="/scripts"
            className="inline-flex items-center gap-1 text-emerald-300 hover:text-emerald-200"
          >
            Browse the 12 supplier scripts
            <ArrowRight className="size-3" />
          </Link>
          <span>·</span>
          <Link
            href="/guide"
            className="inline-flex items-center gap-1 text-emerald-300 hover:text-emerald-200"
          >
            Full user guide
            <ArrowRight className="size-3" />
          </Link>
        </section>
      </div>
    </div>
  );
}
