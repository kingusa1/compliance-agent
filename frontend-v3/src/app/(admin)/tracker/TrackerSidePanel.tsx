"use client";
/**
 * /tracker side panel — full draft + Save form (2026-05-15 rewrite).
 *
 * Every editable field is held in local ``draft`` state until the reviewer
 * presses **Save changes**. Save routes each diffed field to the right
 * endpoint:
 *
 *   * Rejection-level fields (reason / fix_narrative / category /
 *     fix_required / status / outcome / deadline / outcome_narrative) →
 *     PATCH /api/tracker/rows/{rejection_id}.
 *   * Deal-level fields (supplier / agent / MPAN / MPRN / annual value /
 *     live date / term months / docusign) → PATCH /api/tracker/calls/{call_id}/meta.
 *   * Assignee id → POST /api/tracker/rows/{rejection_id}/assignee.
 *
 * Reviewer can ALSO confirm the AI verdict in one click via "Confirm AI
 * verdict" (replaces Save when no fields are dirty). The Confirm button
 * fires /api/rejections/{id}/confirm — humans only path, by spec.
 *
 * Compliant rows (no rejection, no AWAITING_REVIEW status) render a
 * read-only summary; their AI verdict is locked.
 */
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { CategoryChip, CATEGORY_KEYS, CATEGORY_LABEL } from "./CategoryChip";
import { StatusPipelinePill, PIPELINE_STEPS, PIPELINE_LABELS } from "./StatusPipelinePill";
import { formatCustomerName, isPlaceholderCustomerName } from "@/lib/customer";
import type { TrackerRow } from "@/lib/queries/tracker";
import {
  useConfirmVerdict,
  useEditCallMeta,
  useEditTrackerRow,
  useOverrideVerdict,
  useSetAssignee,
} from "@/lib/mutations/tracker";
import { useActiveReviewersQuery } from "@/lib/queries/reviewers";

// Canonical supplier list (mirrors backend SupplierEnum + legacy aliases).
const SUPPLIER_OPTIONS = [
  "E.ON",
  "E.ON Next",
  "E.ON Next Energy",
  "British Gas",
  "British Gas Core",
  "British Gas Lite",
  "British Gas Business",
  "British Gas Trading",
  "BGL",
  "Pozitive",
  "Pozitive Energy",
  "Yu Energy",
  "Smartest Energy",
  "Affect Energy",
  "Britannia Gas",
  "United Gas & Power",
  "TotalEnergies (out-of-matrix)",
  "EDF",
  "Scottish Power",
  "Other",
];

const TERM_MONTHS_OPTIONS = [12, 24, 36, 48, 60] as const;

const FIX_ACTIONS = [
  "AMENDMENT_CALL", "CONFIRMATION_CALL", "NEW_LOA", "NEW_DOCUSIGN",
  "DD_MANDATE", "RESELL_TO_OTHER_SUPPLIER", "PRICE_RECHECK",
  "COT_CHANGE_OF_TENANCY", "CONTRACT_LENGTH_LIMIT", "MANUAL_ADMIN_SUBMISSION",
];

const OUTCOMES = [
  "FIXED_AND_SUBMITTED", "CUSTOMER_LOST", "CANCELLED",
  "NOT_RECOVERABLE", "RESIGNED_TO_OTHER_SUPPLIER",
];

// Every editable field that maps to the side panel form. Each value is
// always a string (or "" for null) so the diff logic stays trivial. Final
// numeric/date coercion happens in the per-field save router.
type DraftFields = {
  // Call-level — 2026-05-24 added customer_name editable so reviewers
  // can correct it when AI extraction failed (e.g. unclear audio).
  customer_name: string;
  // Rejection-level
  rejection_reason: string;
  fix_narrative: string;
  category: string;
  fix_required: string;
  status: string;
  outcome: string;
  outcome_narrative: string;
  deadline: string;       // YYYY-MM-DD
  fix_assignee_id: string;
  // Deal-level
  supplier: string;
  sales_agent: string;
  mpan_electricity: string;
  mprn_gas: string;
  deal_value_gbp: string;
  expected_live_date: string;  // YYYY-MM-DD
  term_months: string;         // "" | "12" ... "60"
  docusign_reference: string;
};

const REJECTION_KEYS: ReadonlyArray<keyof DraftFields> = [
  "rejection_reason",
  "fix_narrative",
  "category",
  "fix_required",
  "status",
  "outcome",
  "outcome_narrative",
  "deadline",
];
const DEAL_KEYS: ReadonlyArray<keyof DraftFields> = [
  "supplier",
  "sales_agent",
  "mpan_electricity",
  "mprn_gas",
  "deal_value_gbp",
  "expected_live_date",
  "term_months",
  "docusign_reference",
];
// Call-level keys live on the Call row itself (not on Deal). Route through
// `editCallMeta` to PATCH /api/tracker/calls/{call_id}/meta which accepts
// `customer_name`, `agent_name`, `detected_supplier`.
const CALL_KEYS: ReadonlyArray<keyof DraftFields> = ["customer_name"];

function rowToDraft(row: TrackerRow): DraftFields {
  return {
    customer_name: row.customer_name ?? "",
    rejection_reason: row.rejection_reason ?? "",
    fix_narrative: row.fix_narrative ?? "",
    category: row.category ?? "",
    fix_required: row.fix_required ?? "",
    status: row.status ?? "",
    outcome: row.outcome ?? "",
    outcome_narrative: row.outcome_narrative ?? "",
    deadline: row.deadline?.slice(0, 10) ?? "",
    fix_assignee_id: row.fix_assignee_id ?? "",
    supplier: row.supplier ?? "",
    sales_agent: row.sales_agent ?? "",
    mpan_electricity: row.mpan_electricity ?? "",
    mprn_gas: row.mprn_gas ?? "",
    deal_value_gbp: row.deal_value_gbp != null ? String(row.deal_value_gbp) : "",
    expected_live_date: row.expected_live_date?.slice(0, 10) ?? "",
    term_months: row.term_months != null ? String(row.term_months) : "",
    docusign_reference: row.docusign_reference ?? "",
  };
}

export function TrackerSidePanel({
  row,
  onClose,
}: {
  row: TrackerRow;
  onClose: () => void;
}) {
  // Row classification — kept verbatim from prior version. Awaiting-review
  // rows live without a Rejection (they're calls flagged by the AI but not
  // yet signed off); compliant rows have neither. Both still expose every
  // editable field except the verdict bucket.
  const isAwaitingReview = !row.rejection_id && row.status === "AWAITING_REVIEW";
  const isCompliant = !row.rejection_id && !isAwaitingReview;
  const editRow = useEditTrackerRow();
  const editCallMeta = useEditCallMeta();
  const confirm = useConfirmVerdict();
  const override = useOverrideVerdict();
  const setAssignee = useSetAssignee();
  const reviewersQ = useActiveReviewersQuery();

  // ── Draft state ───────────────────────────────────────────────────────
  // Single source of truth for every editable field. Reset whenever the
  // selected row changes OR a successful mutation refreshes the server
  // value so the form re-anchors to the saved state.
  const [draft, setDraft] = useState<DraftFields>(() => rowToDraft(row));
  useEffect(() => {
    setDraft(rowToDraft(row));
  }, [
    row.rejection_id,
    row.call_id,
    row.customer_name,
    row.rejection_reason,
    row.fix_narrative,
    row.category,
    row.fix_required,
    row.status,
    row.outcome,
    row.outcome_narrative,
    row.deadline,
    row.fix_assignee_id,
    row.supplier,
    row.sales_agent,
    row.mpan_electricity,
    row.mprn_gas,
    row.deal_value_gbp,
    row.expected_live_date,
    row.term_months,
    row.docusign_reference,
  ]);

  const original = useMemo(() => rowToDraft(row), [row]);

  const editable = Boolean(row.rejection_id || (isAwaitingReview && row.call_id));

  // Which keys are editable on THIS row.
  const rejectionEditable = Boolean(row.rejection_id);
  const dealEditable = editable && Boolean(row.deal_id);
  // Call-level (customer_name) is editable whenever we have a call_id at
  // all — covers awaiting-review rows and rejection rows alike. The
  // backend route accepts the field on any call.
  const callEditable = Boolean(row.call_id);

  // ── Dirty calculation ─────────────────────────────────────────────────
  const diffs = useMemo(() => {
    const out: Partial<Record<keyof DraftFields, string>> = {};
    (Object.keys(draft) as (keyof DraftFields)[]).forEach((k) => {
      if (draft[k] !== original[k]) out[k] = draft[k];
    });
    return out;
  }, [draft, original]);
  const dirty = Object.keys(diffs).length > 0;

  const verdictState = row.verdict_state ?? "AI_PENDING";
  const isAiPending = verdictState === "AI_PENDING";

  const saving =
    editRow.isPending ||
    editCallMeta.isPending ||
    setAssignee.isPending ||
    override.isPending;

  const update = <K extends keyof DraftFields>(key: K, value: string) =>
    setDraft((d) => ({ ...d, [key]: value }));

  // ── Save router ───────────────────────────────────────────────────────
  // Splits dirty fields into (rejection-level / deal-level / assignee) and
  // fires one mutation per group. Each mutation invalidates the tracker
  // query so the row refreshes after Save resolves.
  const onSave = () => {
    if (!dirty) return;
    const dirtyKeys = Object.keys(diffs) as (keyof DraftFields)[];

    // Coerce string-form draft values back to backend types.
    const toServerValue = (k: keyof DraftFields): string | number | null => {
      const v = diffs[k];
      if (v === undefined) return null;
      if (v === "") return null;
      if (k === "deal_value_gbp") return Number(v);
      if (k === "term_months") return Number(v);
      return v;
    };

    // 1. Rejection-level fields (only when a rejection_id exists).
    if (rejectionEditable) {
      const rejectionDiffs: Record<string, string | number | null> = {};
      dirtyKeys.forEach((k) => {
        if (REJECTION_KEYS.includes(k)) {
          rejectionDiffs[k] = toServerValue(k);
        }
      });
      // We deliberately use the override path when any reviewer-overrideable
      // field changes so verdict_state flips to HUMAN_OVERRIDDEN. Otherwise
      // a plain PATCH leaves provenance as AI_PENDING which is wrong after
      // a manual edit. Other simple fields (status / deadline / outcome /
      // outcome_narrative) still go through the row-edit PATCH.
      const overrideKeys = new Set([
        "rejection_reason",
        "fix_narrative",
        "category",
        "fix_required",
      ]);
      const overrideBody: Record<string, string | null> = {};
      const editBody: Record<string, string | number | null> = {};
      Object.entries(rejectionDiffs).forEach(([k, v]) => {
        if (overrideKeys.has(k)) overrideBody[k] = v as string | null;
        else editBody[k] = v;
      });
      if (Object.keys(overrideBody).length > 0 && row.rejection_id) {
        override.mutate({
          rejectionId: row.rejection_id,
          body: overrideBody,
        });
      }
      if (Object.keys(editBody).length > 0 && row.rejection_id) {
        editRow.mutate({
          rejectionId: row.rejection_id,
          fields: editBody,
        });
      }
    }

    // 2. Deal-level fields — route through the right endpoint based on
    //    whether a rejection exists for this call.
    if (dealEditable) {
      const dealDiffs: Record<string, string | number | null> = {};
      dirtyKeys.forEach((k) => {
        if (DEAL_KEYS.includes(k)) {
          dealDiffs[k] = toServerValue(k);
        }
      });
      if (Object.keys(dealDiffs).length > 0) {
        if (row.rejection_id) {
          editRow.mutate({
            rejectionId: row.rejection_id,
            fields: dealDiffs,
          });
        } else if (row.call_id) {
          editCallMeta.mutate({
            callId: row.call_id,
            fields: dealDiffs,
          });
        }
      }
    }

    // 3. Assignee — separate endpoint.
    if (rejectionEditable && diffs.fix_assignee_id !== undefined && row.rejection_id) {
      setAssignee.mutate({
        rejectionId: row.rejection_id,
        assigneeId: diffs.fix_assignee_id === "" ? null : diffs.fix_assignee_id,
      });
    }

    // 4. Call-level fields (customer_name) — route through call-meta PATCH.
    // Always uses editCallMeta regardless of rejection_id so a reviewer
    // can correct the AI's name extraction on rejection rows too.
    if (callEditable && row.call_id) {
      const callDiffs: Record<string, string | number | null> = {};
      dirtyKeys.forEach((k) => {
        if (CALL_KEYS.includes(k)) callDiffs[k] = toServerValue(k);
      });
      if (Object.keys(callDiffs).length > 0) {
        editCallMeta.mutate({ callId: row.call_id, fields: callDiffs });
      }
    }
  };

  const onConfirm = () => {
    if (!row.rejection_id) return;
    confirm.mutate(row.rejection_id);
  };

  // ── Render ────────────────────────────────────────────────────────────
  const nameIsPlaceholder = isPlaceholderCustomerName(row.customer_name);
  const headerLabel = formatCustomerName(row.customer_name);

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto border-l border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-medium">
            {headerLabel}
            {nameIsPlaceholder && (
              <span
                className="ml-2 rounded-sm bg-amber-100 px-1.5 py-0.5 align-middle text-[10px] font-medium uppercase text-amber-900"
                title="The AI couldn't read the customer name from the audio. Edit below to set it manually."
              >
                AI couldn&apos;t read
              </span>
            )}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[var(--text-muted)]">
            {row.score && <span className="font-medium">Score {row.score}</span>}
            {row.verdict_state && (
              <span className="rounded border border-[var(--border-subtle)] px-1 py-0.5">
                {row.verdict_state.replace(/_/g, " ").toLowerCase()}
              </span>
            )}
            {row.rejected_at && (
              <span title="Rejection raised">
                rejected {new Date(row.rejected_at).toLocaleDateString("en-GB")}
              </span>
            )}
            {row.last_action_date && (
              <span title="Last reviewer action">
                last activity {new Date(row.last_action_date).toLocaleDateString("en-GB")}
              </span>
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-[var(--text-muted)] hover:text-[var(--text-default)]"
          aria-label="Close panel"
        >
          ×
        </button>
      </header>

      {/* Customer name editor — always present when a call_id is known so
          reviewers can correct an unread/garbled name straight from the
          panel. Empty value submits as NULL which the backend treats as
          "clear". 2026-05-24 user-reported issue. */}
      {callEditable && (
        <section className="space-y-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 text-[12px]">
          <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
            Customer
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
              Name
            </span>
            <input
              type="text"
              value={draft.customer_name}
              onChange={(e) => update("customer_name", e.target.value)}
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              placeholder={nameIsPlaceholder ? "Type the customer name (Unknown by default)" : "Customer name…"}
              aria-label="Customer name"
            />
            {nameIsPlaceholder && (
              <span className="text-[10px] text-amber-700">
                AI extraction failed. Type the customer name and press Save to
                push it onto every page that references this call.
              </span>
            )}
          </label>
        </section>
      )}

      {/* AI auto-categorized banner — only on AI_PENDING rejection rows. */}
      {!isCompliant && !isAwaitingReview && isAiPending && rejectionEditable && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-[12px] text-amber-900">
          <div className="flex items-start gap-2">
            <svg
              width="14" height="14" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round"
              strokeLinejoin="round" className="mt-0.5 flex-shrink-0" aria-hidden
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v4l3 3" />
            </svg>
            <div>
              <div className="font-medium">AI auto-categorized — review required</div>
              <div className="mt-0.5 text-[11px] text-amber-800">
                Review and confirm before this counts toward Compliant /
                Non-compliant totals. Editing any field then pressing Save
                flips this to a human-overridden record.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Identity card */}
      {editable ? (
        <section className="space-y-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 text-[12px]">
          <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
            Identity
          </div>
          <div className="grid grid-cols-1 gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Supplier</span>
              <select
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                value={draft.supplier}
                onChange={(e) => update("supplier", e.target.value)}
              >
                <option value="">—</option>
                {SUPPLIER_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Agent</span>
              <input
                type="text"
                value={draft.sales_agent}
                onChange={(e) => update("sales_agent", e.target.value)}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                placeholder="Agent name…"
              />
            </label>
          </div>
        </section>
      ) : (
        <dl className="space-y-1 text-[12px]">
          <div className="flex justify-between"><dt className="text-[var(--text-muted)]">Supplier</dt><dd>{row.supplier ?? "—"}</dd></div>
          <div className="flex justify-between"><dt className="text-[var(--text-muted)]">Agent</dt><dd>{row.sales_agent ?? "—"}</dd></div>
          <div className="flex justify-between"><dt className="text-[var(--text-muted)]">MPAN/MPRN</dt><dd className="font-mono">{row.mpan_mprn ?? "—"}</dd></div>
        </dl>
      )}

      {/* Meter + Deal card */}
      {dealEditable && (
        <section className="space-y-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 text-[12px]">
          <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Meter &amp; deal</div>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">MPAN</span>
              <input
                type="text"
                value={draft.mpan_electricity}
                onChange={(e) => update("mpan_electricity", e.target.value)}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 font-mono text-[11px]"
                placeholder="13-digit core"
                inputMode="numeric"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">MPRN</span>
              <input
                type="text"
                value={draft.mprn_gas}
                onChange={(e) => update("mprn_gas", e.target.value)}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 font-mono text-[11px]"
                placeholder="6-10 digits"
                inputMode="numeric"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Annual value (£)</span>
              <input
                type="number"
                min={0}
                step={1}
                value={draft.deal_value_gbp}
                onChange={(e) => update("deal_value_gbp", e.target.value)}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                placeholder="0"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Live date</span>
              <input
                type="date"
                value={draft.expected_live_date}
                onChange={(e) => update("expected_live_date", e.target.value)}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Term (mo)</span>
              <select
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                value={draft.term_months}
                onChange={(e) => update("term_months", e.target.value)}
              >
                <option value="">—</option>
                {TERM_MONTHS_OPTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">DocuSign ref</span>
              <input
                type="text"
                value={draft.docusign_reference}
                onChange={(e) => update("docusign_reference", e.target.value)}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 font-mono text-[11px]"
                placeholder="envelope id"
              />
            </label>
          </div>
        </section>
      )}

      {isCompliant ? (
        <div className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-[12px] text-emerald-900">
          <div className="font-medium">Compliant — score {row.score ?? "—"}</div>
          <div className="mt-1 text-[11px]">No rejection. Customer-confirmation email sent.</div>
        </div>
      ) : isAwaitingReview ? (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-[12px] text-amber-900">
          <div className="font-medium">Awaiting reviewer sign-off — score {row.score ?? "—"}</div>
          {row.rejection_reason ? (
            <div className="mt-1 text-[11px] italic">
              AI flagged: &ldquo;{row.rejection_reason}&rdquo;
            </div>
          ) : null}
          <div className="mt-1 text-[11px]">
            Open the call analysis to commit a Pass / Needs Review / Non-Compliant verdict.
            Until a reviewer signs off, this call doesn&apos;t roll up to the Compliant or
            Non-Compliant totals and stays out of the Rejections tab.
          </div>
          {row.call_id && (
            <Link
              href={`/calls/${row.call_id}`}
              className="mt-2 inline-flex items-center gap-1 rounded-md bg-amber-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-amber-700"
            >
              Open call analysis →
            </Link>
          )}
        </div>
      ) : (
        <>
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Reason</div>
            <textarea
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2 text-[12px]"
              value={draft.rejection_reason}
              onChange={(e) => update("rejection_reason", e.target.value)}
              rows={2}
            />
          </div>
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Fix narrative</div>
            <textarea
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2 text-[12px]"
              value={draft.fix_narrative}
              onChange={(e) => update("fix_narrative", e.target.value)}
              rows={2}
              placeholder="Free-text fix narrative…"
            />
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Category</div>
            <select
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              value={draft.category}
              onChange={(e) => update("category", e.target.value)}
            >
              <option value="">—</option>
              {CATEGORY_KEYS.map((k) => <option key={k} value={k}>{CATEGORY_LABEL[k]}</option>)}
            </select>
            <div className="mt-1"><CategoryChip category={draft.category || row.category} /></div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Fix required</div>
            <select
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              value={draft.fix_required}
              onChange={(e) => update("fix_required", e.target.value)}
            >
              <option value="">—</option>
              {FIX_ACTIONS.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Status</div>
            <select
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              value={draft.status}
              onChange={(e) => update("status", e.target.value)}
            >
              <option value="">—</option>
              {PIPELINE_STEPS.map((step) => (
                <option key={step} value={step}>{PIPELINE_LABELS[step] ?? step}</option>
              ))}
              <option value="DEAD">Dead</option>
            </select>
            <div className="mt-2"><StatusPipelinePill status={draft.status || row.status} /></div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Outcome</div>
            <select
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              value={draft.outcome}
              onChange={(e) => update("outcome", e.target.value)}
            >
              <option value="">—</option>
              {OUTCOMES.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[12px]">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Deadline</span>
              <input
                type="date"
                value={draft.deadline}
                onChange={(e) => update("deadline", e.target.value)}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[12px]">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Assigned to</span>
              <select
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                value={draft.fix_assignee_id}
                onChange={(e) => update("fix_assignee_id", e.target.value)}
              >
                <option value="">— Unassigned</option>
                {(reviewersQ.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name || p.email} ({p.role})
                  </option>
                ))}
              </select>
            </label>
          </div>
        </>
      )}

      {/* Notes — visible on rejection rows. Always controlled by the draft. */}
      {rejectionEditable && (
        <div className="mt-2">
          <label className="text-[10px] uppercase text-[var(--text-muted)]">Notes</label>
          <textarea
            value={draft.outcome_narrative}
            onChange={(e) => update("outcome_narrative", e.target.value)}
            className="mt-1 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2 text-[12px] min-h-[80px]"
            placeholder="Reviewer notes…"
          />
          {row.last_action_date && (
            <p className="mt-1 text-[10px] text-[var(--text-dim)]">
              Last activity {new Date(row.last_action_date).toLocaleString("en-GB")}
            </p>
          )}
        </div>
      )}

      {/* Footer — Open call · Open in /rejections · Save / Confirm. */}
      <div className="mt-auto flex flex-col gap-2 border-t border-[var(--border-subtle)] pt-3">
        <div className="flex flex-wrap items-center gap-3">
          {row.call_id && (
            <Link
              href={`/calls/${row.call_id}`}
              className="text-[12px] text-emerald-700 hover:underline"
            >
              Open call analysis →
            </Link>
          )}
          {row.rejection_id && (
            <Link
              href={`/rejections?id=${encodeURIComponent(row.rejection_id)}`}
              className="text-[12px] text-emerald-700 hover:underline"
            >
              Open in Rejections →
            </Link>
          )}
        </div>
        <div className="flex items-center justify-end gap-2">
          {dirty && (
            <button
              type="button"
              onClick={() => setDraft(original)}
              disabled={saving}
              className="inline-flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-3 py-1.5 text-[12px] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)] disabled:opacity-50"
            >
              Discard
            </button>
          )}
          {dirty ? (
            <button
              type="button"
              onClick={onSave}
              disabled={saving}
              className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                <polyline points="17 21 17 13 7 13 7 21" />
                <polyline points="7 3 7 8 15 8" />
              </svg>
              {saving ? "Saving…" : `Save (${Object.keys(diffs).length})`}
            </button>
          ) : rejectionEditable && isAiPending ? (
            <button
              type="button"
              onClick={onConfirm}
              disabled={confirm.isPending}
              className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M20 6L9 17l-5-5" />
              </svg>
              {confirm.isPending ? "Confirming…" : "Confirm AI verdict"}
            </button>
          ) : null}
        </div>
      </div>
    </aside>
  );
}
