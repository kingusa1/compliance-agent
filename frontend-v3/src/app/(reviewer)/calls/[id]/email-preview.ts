/**
 * email-preview — pure builder for the per-checkpoint feedback email
 * shown in the Verdict tab live preview card.
 *
 * Kept side-effect-free + framework-agnostic so it can be unit-tested
 * without React or any TanStack hooks. The Verdict tab calls this on
 * every state change to refresh the monospace preview block.
 *
 * NOTE: this is a UX prototype — backend `/feedback-email` mutation
 * still expects a single `body_markdown` string. We compose that
 * string here from per-CP picks; when backend is extended to take the
 * structured payload directly, this helper stays useful as the
 * "human-readable" rendering for the email body.
 */

import type { CheckpointVerdict } from "./CheckpointCard";

export type PerCpAction =
  | "no_action"
  | "coach"
  | "reask_supp"
  | "schedule_another"
  | "recall_redo"
  | "ignore";

export const PER_CP_ACTION_LABEL: Record<PerCpAction, string> = {
  no_action: "No action",
  coach: "Coach agent",
  reask_supp: "Re-ask in supplementary call",
  schedule_another: "Schedule another call",
  recall_redo: "Recall + redo whole call",
  ignore: "Ignore (lenient pass)",
};

export type AggregateAction = "PASS" | "REVIEW" | "COACHING" | "FAIL" | "BLOCK";

export type EmailCheckpointInput = {
  /** normalised lookup key (matches keys used in perCpActions/Comments) */
  key: string;
  name: string;
  status: string; // "pass" | "fail" | "partial" | "unscored"
};

export type BuildEmailArgs = {
  callId: string;
  agentName?: string | null;
  customerName?: string | null;
  filename?: string | null;
  score?: string | null; // e.g. "22/25" — already-formatted
  aggregate: AggregateAction | null;
  overallReason: string;
  reviewerEmail?: string | null;
  perCpActions: Map<string, PerCpAction>;
  perCpComments: Map<string, string>;
  /** All scored checkpoints — used to pick non-pass + actioned ones into the email */
  checkpoints: EmailCheckpointInput[];
};

export type BuiltEmail = {
  subject: string;
  body: string;
};

/**
 * Suggest the aggregate verdict.
 *
 * Severity rules (when statusCounts present) trump per-CP action picks
 * so a call with AI-flagged fails or partials never suggests PASS just
 * because the reviewer hasn't manually picked actions yet:
 *   fails > 0       → FAIL
 *   partials > 0    → REVIEW
 *   blockedBucket   → FAIL
 *
 * Then fall back to per-CP action priority:
 *   recall_redo                              → FAIL
 *   schedule_another / reask_supp            → REVIEW
 *   coach                                    → COACHING
 *   all no_action / ignore                   → PASS
 *
 * Returns null only when statusCounts is absent AND no per-CP actions are
 * set yet (preserves the "no suggestion yet" UX path).
 */
export type SuggestStatusCounts = {
  fails: number;
  partials: number;
  blockedBucket?: boolean;
};

export function suggestAggregate(
  perCpActions: Map<string, PerCpAction>,
  statusCounts?: SuggestStatusCounts,
): AggregateAction | null {
  if (statusCounts) {
    if (statusCounts.blockedBucket) return "FAIL";
    if (statusCounts.fails > 0) return "FAIL";
    if (statusCounts.partials > 0) return "REVIEW";
  }
  const acts = [...perCpActions.values()];
  if (acts.length === 0) return statusCounts ? "PASS" : null;
  if (acts.some((a) => a === "recall_redo")) return "FAIL";
  if (acts.some((a) => a === "schedule_another" || a === "reask_supp")) {
    return "REVIEW";
  }
  if (acts.some((a) => a === "coach")) return "COACHING";
  if (acts.every((a) => a === "no_action" || a === "ignore")) return "PASS";
  return "PASS";
}

/**
 * Build the agent-facing feedback email — subject + body.
 *
 * Body is plain text (monospace-friendly). Includes:
 *   1. Per-CP issues for non-pass + actioned items
 *   2. Required follow-up call counter (if any reask_supp / schedule_another)
 *   3. Reviewer overall notes
 *   4. Reviewer signature
 *
 * Pure — no React, no fetch, no toast. Safe to call inside a useMemo.
 */
export function buildEmailPreview(args: BuildEmailArgs): BuiltEmail {
  const {
    callId,
    agentName,
    customerName,
    filename,
    score,
    aggregate,
    overallReason,
    reviewerEmail,
    perCpActions,
    perCpComments,
    checkpoints,
  } = args;

  const callLabel = filename || callId;
  const agentLabel = agentName?.trim() || "team";
  const customerLabel = customerName?.trim() || "customer";
  const verdictLabel = aggregate ?? "REVIEW";

  // Issues: every CP whose action is set AND not "no_action".
  const issues = checkpoints.filter((cp) => {
    const a = perCpActions.get(cp.key);
    return a && a !== "no_action";
  });

  const followUps = issues.filter((cp) => {
    const a = perCpActions.get(cp.key);
    return a === "schedule_another" || a === "reask_supp";
  });

  const subject = `Compliance review — ${callLabel} — ${verdictLabel}`;

  const lines: string[] = [];
  lines.push(`Hi ${agentLabel},`);
  lines.push("");
  lines.push(
    `Your call (${callLabel} for ${customerLabel}) has been reviewed.${
      score ? ` Score: ${score}.` : ""
    }`,
  );
  lines.push("");

  if (issues.length > 0) {
    lines.push("ISSUES IDENTIFIED:");
    for (const cp of issues) {
      const action = perCpActions.get(cp.key) ?? "no_action";
      const comment = (perCpComments.get(cp.key) ?? "").trim();
      lines.push(`  • ${cp.name} — ${cp.status.toUpperCase()}`);
      if (comment) lines.push(`    Issue: ${comment}`);
      lines.push(`    Action: ${PER_CP_ACTION_LABEL[action]}`);
    }
    lines.push("");
  } else {
    lines.push("ISSUES IDENTIFIED:");
    lines.push("  None — all checkpoints passed.");
    lines.push("");
  }

  if (followUps.length > 0) {
    lines.push("REQUIRED FOLLOW-UP CALLS:");
    lines.push(
      `  — ${followUps.length} supplementary call${
        followUps.length === 1 ? "" : "s"
      } needed.`,
    );
    lines.push("");
  }

  lines.push("REVIEWER NOTES:");
  lines.push(overallReason.trim() || "(none)");
  lines.push("");
  lines.push("Please confirm receipt + complete the required actions within 7 days.");
  lines.push("");
  lines.push(`Reviewer: ${reviewerEmail || "compliance@xaia.ae"}`);

  return { subject, body: lines.join("\n") };
}

// ── Customer confirmation email (W3.B v3-watt-coverage) ──────────────
//
// Builder for the *customer*-facing confirmation email — distinct from
// the agent-facing one above. The Verdict tab's second email card uses
// this to show a plain-text preview before the reviewer hits "Send",
// while the backend's ``/customer-email`` endpoint owns the canonical
// HTML template and is the source of truth for what's actually sent.
//
// Pure: no React, no fetch — safe inside useMemo. Subject uses the
// call_ref when available so the customer can quote it back to support.

export type CustomerEmailArgs = {
  customerName?: string | null;
  supplier?: string | null;
  /** "24 months" / "2 years" — already-formatted upstream. */
  contractLength?: string | null;
  /** "28.4p / kWh" or null when not yet extracted. */
  unitRate?: string | null;
  /** "42.7p / day" or null when not yet extracted. */
  standingCharge?: string | null;
  docusignRef?: string | null;
  callRef?: string | null;
  reviewerEmail?: string | null;
};

const _MISSING = (k: string) => `{{ MISSING: ${k} }}`;

/**
 * Build the customer-confirmation preview shown in the Verdict tab's
 * second email card. Returns the same ``{subject, body}`` shape as
 * ``buildEmailPreview`` so the existing preview-card render path can
 * stay symmetrical.
 *
 * Body deliberately mirrors the backend HTML template's information
 * order — customer salutation, contract summary, cooling-off
 * paragraph, signed-contract reference — so the reviewer's preview
 * and the customer's inbox tell the same story.
 */
export function buildCustomerEmailPreview(args: CustomerEmailArgs): BuiltEmail {
  const customer = args.customerName?.trim() || _MISSING("customer_name");
  const supplier = args.supplier?.trim() || _MISSING("supplier");
  const contractLength = args.contractLength?.trim() || _MISSING("contract_length");
  const unitRate = args.unitRate?.trim() || _MISSING("unit_rate");
  const standingCharge = args.standingCharge?.trim() || _MISSING("standing_charge");
  const docusign = args.docusignRef?.trim() || _MISSING("docusign_ref");
  const callRef = args.callRef?.trim() || _MISSING("call_ref");

  const subject = `Confirmation of your energy contract — ${callRef}`;

  const lines: string[] = [];
  lines.push(`Dear ${customer},`);
  lines.push("");
  lines.push(
    `Thank you for speaking with us today. This email confirms the contract you agreed verbally on the call, signed via DocuSign envelope ${docusign} (call reference ${callRef}).`,
  );
  lines.push("");
  lines.push("CONTRACT SUMMARY");
  lines.push(`  Supplier:               ${supplier}`);
  lines.push(`  Contract length:        ${contractLength}`);
  lines.push(`  Unit rate (quoted):     ${unitRate}`);
  lines.push(`  Standing charge:        ${standingCharge}`);
  lines.push("");
  lines.push("YOUR 14-DAY COOLING-OFF PERIOD");
  lines.push(
    "You have a 14-day cooling-off period under the Consumer Contracts Regulations 2013, starting from today. During this window you may cancel the contract without penalty by replying to this email or calling us. After 14 days the contract becomes binding for the term above.",
  );
  lines.push("");
  lines.push(`Signed contract reference: ${docusign}`);
  lines.push(
    "If you did not authorise this contract, please contact us immediately.",
  );
  lines.push("");
  lines.push(args.reviewerEmail || "compliance@xaia.ae");

  return { subject, body: lines.join("\n") };
}
