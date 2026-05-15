"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { CategoryChip, CATEGORY_KEYS, CATEGORY_LABEL } from "./CategoryChip";
import { StatusPipelinePill, PIPELINE_STEPS, PIPELINE_LABELS } from "./StatusPipelinePill";
import type { TrackerRow } from "@/lib/queries/tracker";
import {
  useConfirmVerdict,
  useOverrideVerdict,
  useEditTrackerRow,
  useSetAssignee,
} from "@/lib/mutations/tracker";
import { useActiveReviewersQuery } from "@/lib/queries/reviewers";

// Canonical supplier list (mirrors backend SupplierEnum in payload_schema.py)
// plus the legacy short-form aliases that the pipeline's auto-detector emits
// on older rows (``E.ON Next`` vs canonical ``E.ON Next Energy``). Side-panel
// uses it as the source of truth for the supplier dropdown so a reviewer can
// correct a misdetected supplier on any rejection row. If we don't include
// the legacy aliases the dropdown shows an empty value for current rows.
const SUPPLIER_OPTIONS = [
  "E.ON",
  "E.ON Next",          // legacy alias for E.ON Next Energy
  "E.ON Next Energy",
  "British Gas",        // legacy generic
  "British Gas Core",
  "British Gas Lite",
  "British Gas Business",
  "British Gas Trading",
  "BGL",                // legacy abbrev (British Gas Lite)
  "Pozitive",
  "Pozitive Energy",    // legacy alias
  "Yu Energy",
  "Smartest Energy",
  "Affect Energy",
  "Britannia Gas",
  "United Gas & Power",
  "TotalEnergies (out-of-matrix)",
  "EDF",                // historical pipeline value
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

type DraftFields = {
  category: string;
  fix_required: string;
  fix_narrative: string;
  rejection_reason: string;
};

function _initialDraft(row: TrackerRow): DraftFields {
  return {
    category: row.category ?? "",
    fix_required: row.fix_required ?? "",
    fix_narrative: row.fix_narrative ?? "",
    rejection_reason: row.rejection_reason ?? "",
  };
}

export function TrackerSidePanel({ row, onClose }: { row: TrackerRow; onClose: () => void }) {
  // 2026-05-14: a row without `rejection_id` could be one of TWO things —
  // a fully-compliant call (green) OR an awaiting-review call the AI flagged
  // (amber). Previously both were collapsed into a single "Compliant" branch,
  // which mis-labelled every AI-flagged-but-not-yet-reviewed call as
  // compliant. Distinguish via the synthetic AWAITING_REVIEW status that
  // tracker_aggregator._awaiting_review_row stamps.
  const isAwaitingReview = !row.rejection_id && row.status === "AWAITING_REVIEW";
  const isCompliant = !row.rejection_id && !isAwaitingReview;
  const editNotes = useEditTrackerRow();
  const confirm = useConfirmVerdict();
  const override = useOverrideVerdict();
  const setAssignee = useSetAssignee();
  const reviewersQ = useActiveReviewersQuery();

  // Helper — patch a single editable field via the tracker PATCH endpoint.
  // Used by the new Identity / Meter+Deal / Status cards so each blur or
  // dropdown change persists immediately. We never block on the mutation
  // (background invalidation refreshes the row) so the UI stays snappy.
  const patchField = (field: string, value: string | number | null) => {
    if (!row.rejection_id) return;
    editNotes.mutate({
      rejectionId: row.rejection_id,
      fields: { [field]: value },
    });
  };

  // Local draft + dirty tracking. When reviewer edits any of the 4
  // override-eligible fields, dirty becomes true → Save replaces the
  // Confirm button. Save flips state to HUMAN_OVERRIDDEN backend-side.
  const [draft, setDraft] = useState<DraftFields>(() => _initialDraft(row));
  // Reset draft whenever the selected row changes (different rejection).
  useEffect(() => setDraft(_initialDraft(row)), [row.rejection_id]);

  const dirty = useMemo(() => {
    const original = _initialDraft(row);
    return (Object.keys(draft) as (keyof DraftFields)[]).some(
      (k) => (draft[k] ?? "") !== (original[k] ?? ""),
    );
  }, [draft, row]);

  const verdictState = row.verdict_state ?? "AI_PENDING";
  const isAiPending = verdictState === "AI_PENDING";

  const onConfirm = () => {
    if (!row.rejection_id) return;
    confirm.mutate(row.rejection_id);
  };

  const onSave = () => {
    if (!row.rejection_id || !dirty) return;
    const original = _initialDraft(row);
    const body: Record<string, string | null> = {};
    (Object.keys(draft) as (keyof DraftFields)[]).forEach((k) => {
      const v = draft[k];
      if ((v ?? "") !== (original[k] ?? "")) {
        body[k] = v === "" ? null : v;
      }
    });
    override.mutate({ rejectionId: row.rejection_id, body });
  };

  return (
    <aside className="flex h-full flex-col gap-4 border-l border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-medium">{row.customer_name ?? "Untitled"}</h2>
        <button
          onClick={onClose}
          className="text-[var(--text-muted)] hover:text-[var(--text-default)]"
          aria-label="Close panel"
        >
          ×
        </button>
      </header>

      {/* AI auto-categorized banner — shown only on AI_PENDING REJECTION rows.
          2026-05-14 audit: previously this also rendered for awaiting-review
          rows (rejection_id null, verdict_state="AI_PENDING") which stacked
          two amber banners on top of each other. Gate by `!isAwaitingReview`
          so each row type gets exactly one banner. */}
      {!isCompliant && !isAwaitingReview && isAiPending && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-[12px] text-amber-900">
          <div className="flex items-start gap-2">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="mt-0.5 flex-shrink-0"
              aria-hidden
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v4l3 3" />
            </svg>
            <div>
              <div className="font-medium">AI auto-categorized — review required</div>
              <div className="mt-0.5 text-[11px] text-amber-800">
                Review and confirm before this counts toward Compliant /
                Non-compliant totals. Editing any field then saving flips
                this to a human-overridden record.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Identity card — every field editable when a rejection_id exists;
          compliant/awaiting-review rows fall back to the read-only summary
          so the reviewer can still see the values. */}
      {row.rejection_id ? (
        <section className="space-y-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 text-[12px]">
          <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
            Identity
          </div>
          <div className="grid grid-cols-1 gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Supplier
              </span>
              <select
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                value={row.supplier ?? ""}
                disabled={editNotes.isPending}
                onChange={(e) => patchField("supplier", e.target.value || null)}
              >
                <option value="">—</option>
                {SUPPLIER_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Agent
              </span>
              <input
                type="text"
                key={`agent-${row.rejection_id ?? ""}-${row.sales_agent ?? ""}`}
                defaultValue={row.sales_agent ?? ""}
                onBlur={(e) => {
                  if (e.target.value !== (row.sales_agent ?? "")) {
                    patchField("sales_agent", e.target.value || null);
                  }
                }}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                placeholder="Agent name…"
              />
            </label>
          </div>
        </section>
      ) : (
        <dl className="space-y-1 text-[12px]">
          <div className="flex justify-between">
            <dt className="text-[var(--text-muted)]">Supplier</dt>
            <dd>{row.supplier ?? "—"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-[var(--text-muted)]">Agent</dt>
            <dd>{row.sales_agent ?? "—"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-[var(--text-muted)]">MPAN/MPRN</dt>
            <dd className="font-mono">{row.mpan_mprn ?? "—"}</dd>
          </div>
        </dl>
      )}

      {/* Meter + Deal card — only renders on rows that resolved to a deal.
          The PATCH endpoint will 400 if the underlying call has no deal,
          so we hide this section entirely rather than show fields that
          would explode on save. ``deal_id`` is set on every row whose
          parent call has a deal — true for any reviewer-initiated row
          and the auto-detect path post-2026-05-15 matcher landing. */}
      {row.rejection_id && row.deal_id && (
        <section className="space-y-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 text-[12px]">
          <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
            Meter &amp; deal
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                MPAN
              </span>
              <input
                type="text"
                key={`mpan-${row.deal_id ?? ""}-${row.mpan_electricity ?? ""}`}
                defaultValue={row.mpan_electricity ?? ""}
                onBlur={(e) =>
                  patchField("mpan_electricity", e.target.value || null)
                }
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 font-mono text-[11px]"
                placeholder="13-digit core"
                inputMode="numeric"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                MPRN
              </span>
              <input
                type="text"
                key={`mprn-${row.deal_id ?? ""}-${row.mprn_gas ?? ""}`}
                defaultValue={row.mprn_gas ?? ""}
                onBlur={(e) =>
                  patchField("mprn_gas", e.target.value || null)
                }
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 font-mono text-[11px]"
                placeholder="6-10 digits"
                inputMode="numeric"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Annual value (£)
              </span>
              <input
                type="number"
                min={0}
                step={1}
                key={`value-${row.deal_id ?? ""}-${row.deal_value_gbp ?? ""}`}
                defaultValue={row.deal_value_gbp ?? ""}
                onBlur={(e) =>
                  patchField(
                    "deal_value_gbp",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                placeholder="0"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Live date
              </span>
              <input
                type="date"
                key={`live-${row.deal_id ?? ""}-${row.expected_live_date ?? ""}`}
                defaultValue={row.expected_live_date?.slice(0, 10) ?? ""}
                onBlur={(e) =>
                  patchField("expected_live_date", e.target.value || null)
                }
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Term (mo)
              </span>
              <select
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                value={row.term_months?.toString() ?? ""}
                onChange={(e) =>
                  patchField(
                    "term_months",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
              >
                <option value="">—</option>
                {TERM_MONTHS_OPTIONS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                DocuSign ref
              </span>
              <input
                type="text"
                key={`ds-${row.deal_id ?? ""}-${row.docusign_reference ?? ""}`}
                defaultValue={row.docusign_reference ?? ""}
                onBlur={(e) =>
                  patchField("docusign_reference", e.target.value || null)
                }
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
            Until a reviewer signs off, this call doesn&apos;t roll up to the
            Compliant or Non-Compliant totals.
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
              onChange={(e) => setDraft((d) => ({ ...d, rejection_reason: e.target.value }))}
              rows={2}
            />
          </div>
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Fix narrative</div>
            <textarea
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2 text-[12px]"
              value={draft.fix_narrative}
              onChange={(e) => setDraft((d) => ({ ...d, fix_narrative: e.target.value }))}
              rows={2}
              placeholder="LLM-generated free-text fix narrative…"
            />
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Category</div>
            <select
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              value={draft.category}
              onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))}
            >
              <option value="">—</option>
              {CATEGORY_KEYS.map((k) => (
                <option key={k} value={k}>{CATEGORY_LABEL[k]}</option>
              ))}
            </select>
            <div className="mt-1"><CategoryChip category={draft.category || row.category} /></div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Fix required</div>
            <select
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              value={draft.fix_required}
              onChange={(e) => setDraft((d) => ({ ...d, fix_required: e.target.value }))}
            >
              <option value="">—</option>
              {FIX_ACTIONS.map((k) => (<option key={k} value={k}>{k}</option>))}
            </select>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Status</div>
            <div className="flex flex-wrap gap-1">
              {PIPELINE_STEPS.map((step) => {
                const active = row.status === step;
                return (
                  <span
                    key={step}
                    className={`rounded-full border px-2 py-0.5 text-[10px] ${active ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "border-[var(--border-subtle)] text-[var(--text-muted)]"}`}
                  >
                    {PIPELINE_LABELS[step] ?? step}
                  </span>
                );
              })}
            </div>
            <div className="mt-2"><StatusPipelinePill status={row.status} /></div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Outcome</div>
            <select
              className="w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              value={row.outcome ?? ""}
              disabled={!row.rejection_id || editNotes.isPending}
              onChange={(e) => {
                // Commit immediately on selection — outcome is a simple
                // enum field so the row-by-row PATCH endpoint (same flow as
                // Notes textarea) is enough; no need to thread it through
                // the draft / Save-changes / Override gate.
                if (!row.rejection_id) return;
                const v = e.target.value;
                editNotes.mutate({
                  rejectionId: row.rejection_id,
                  fields: { outcome: v === "" ? null : v },
                });
              }}
            >
              <option value="">—</option>
              {OUTCOMES.map((k) => (<option key={k} value={k}>{k}</option>))}
            </select>
          </div>

          {/* Deadline + Assignee — sibling fields that were read-only
              previously. Both persist immediately on change. */}
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[12px]">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Deadline
              </span>
              <input
                type="date"
                key={`deadline-${row.rejection_id ?? ""}-${row.deadline ?? ""}`}
                defaultValue={row.deadline?.slice(0, 10) ?? ""}
                onBlur={(e) => {
                  const v = e.target.value;
                  if (v !== (row.deadline?.slice(0, 10) ?? "")) {
                    patchField("deadline", v || null);
                  }
                }}
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[12px]">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Assigned to
              </span>
              <select
                className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-1.5 text-[12px]"
                value={row.fix_assignee_id ?? ""}
                disabled={!row.rejection_id || setAssignee.isPending}
                onChange={(e) => {
                  if (!row.rejection_id) return;
                  setAssignee.mutate({
                    rejectionId: row.rejection_id,
                    assigneeId: e.target.value === "" ? null : e.target.value,
                  });
                }}
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

      {row.rejection_id && (
        <div className="mt-2">
          <label className="text-[10px] uppercase text-[var(--text-muted)]">Notes</label>
          <textarea
            // 2026-05-14 audit: `defaultValue` makes this uncontrolled, so
            // it never refreshes after a TanStack query invalidation. Remount
            // on every change to the underlying field by keying on the row id
            // + the canonical value. Editing-in-flight stays smooth because
            // the key only changes when the SERVER value changes (after a
            // successful PATCH), not while the user is typing.
            key={`${row.rejection_id ?? ""}-${row.outcome_narrative ?? ""}`}
            defaultValue={row.outcome_narrative ?? ""}
            onBlur={(e) => {
              if (e.target.value !== (row.outcome_narrative ?? "")) {
                editNotes.mutate({
                  rejectionId: row.rejection_id!,
                  fields: { outcome_narrative: e.target.value || null },
                });
              }
            }}
            className="mt-1 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2 text-[12px] min-h-[80px]"
            placeholder="Reviewer notes…"
          />
          {row.last_action_date && (
            <p className="mt-1 text-[10px] text-[var(--text-dim)]">
              {/* `last_action_date` is the MAX(rejection_audit_log.created_at)
                  — ANY audit event on this rejection, not specifically a
                  notes edit. Renamed from "Last edited" to "Last activity"
                  to stop reviewers thinking it stamps every keystroke in
                  the box. */}
              Last activity {new Date(row.last_action_date).toLocaleString("en-GB")}
            </p>
          )}
        </div>
      )}

      <div className="mt-auto flex items-center justify-between gap-2 border-t border-[var(--border-subtle)] pt-3">
        {row.call_id && (
          <Link
            href={`/calls/${row.call_id}`}
            className="text-[12px] text-emerald-700 hover:underline"
          >
            Open call analysis →
          </Link>
        )}
        {row.rejection_id && (
          <div className="flex items-center gap-2">
            {dirty ? (
              <button
                onClick={onSave}
                disabled={override.isPending}
                className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4z" />
                </svg>
                {override.isPending ? "Saving…" : "Save changes"}
              </button>
            ) : isAiPending ? (
              <button
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
        )}
      </div>
    </aside>
  );
}
