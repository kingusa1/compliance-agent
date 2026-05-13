"use client";

/**
 * VerdictTab — UX prototype for per-checkpoint verdict workflow.
 *
 * Replaces the simple 5-button + reason form with a per-checkpoint
 * review workflow:
 *   - Aggregate verdict row (5 actions) auto-suggests from per-CP picks
 *   - Per-CP cards grouped by status (FAIL → PARTIAL → PASS-collapsed)
 *   - Action select + comment textarea per non-pass CP
 *   - Live email preview (monospace, pre-formatted)
 *   - Submit currently console.logs payload + toasts (PROTOTYPE).
 *
 * TODO(backend): once `useSubmitVerdict` accepts per-CP payload + the
 * `/feedback-email` mutation accepts the structured email body, wire
 * `onSubmit` to actually fire those mutations. For now we deliberately
 * stay client-only so user can approve the UX before backend changes.
 */
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Ban,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  GraduationCap,
  Mail,
  RotateCcw,
  Undo2,
  XCircle,
  CircleSlash,
  Minus,
} from "lucide-react";
import type { LucideProps } from "lucide-react";
import { toast } from "sonner";

import type { CheckpointVerdict } from "./CheckpointCard";
import type { ScriptCheckpoint } from "@/lib/queries/reviewer";
import type { Flag } from "@/lib/queries/reviewer";
import type { VerdictAction } from "@/lib/mutations/reviewer";
import {
  RISK_TAGS,
  useCustomerEmail,
  useSetCallRiskTags,
  type RiskTag,
} from "@/lib/mutations/reviewer";
import {
  buildCustomerEmailPreview,
  buildEmailPreview,
  PER_CP_ACTION_LABEL,
  suggestAggregate,
  type AggregateAction,
  type PerCpAction,
} from "./email-preview";

// ── Types ─────────────────────────────────────────────────────────

export type VerdictTabCheckpoint = {
  key: string; // normalised name (matches CheckpointCard merge)
  script?: ScriptCheckpoint;
  verdict?: CheckpointVerdict;
};

export type VerdictTabProps = {
  callId: string;
  agentName?: string | null;
  customerName?: string | null;
  filename?: string | null;
  score?: string | null;
  reviewerEmail?: string | null;
  cpCards: VerdictTabCheckpoint[];
  flags: Flag[];
  /** W1.5 (v3-watt-coverage): preselected risk tags from /api/calls/{id}. */
  initialRiskTags?: RiskTag[];
  /**
   * W3.B (v3-watt-coverage): contract context for the second
   * (customer-facing) email card. All optional — missing values render
   * as visible ``{{ MISSING: <key> }}`` placeholders in the preview so
   * the reviewer can spot the gap before sending.
   */
  customerContract?: {
    supplier?: string | null;
    contractLength?: string | null;
    unitRate?: string | null;
    standingCharge?: string | null;
    docusignRef?: string | null;
    callRef?: string | null;
  };
  onSubmitted?: () => void;
};

// W1.5 (v3-watt-coverage): chip palette for the visible risk-tag toggles
// at the top of the verdict tab. W3.C added ``Vulnerable`` as a 5th pill
// — it can be toggled manually like the others AND is auto-asserted by
// the extraction pipeline when a VULNERABLE_CUSTOMER flag is present.
// Keep this list in sync with the backend ``_RISK_TAGS_ALLOWED`` set in
// app/routes.py.
const RISK_TAG_CHIPS: { key: RiskTag; label: string; bg: string; fg: string; border: string; fill: string }[] = [
  {
    key: "Ombudsman",
    label: "Ombudsman",
    bg: "var(--red-bg)",
    fg: "var(--red)",
    border: "var(--red-border)",
    fill: "var(--red)",
  },
  {
    key: "Mis-selling",
    label: "Mis-selling",
    bg: "var(--amber-bg)",
    fg: "var(--amber-400)",
    border: "var(--amber-border)",
    fill: "var(--amber)",
  },
  {
    key: "Complaint",
    label: "Complaint",
    bg: "var(--blue-bg)",
    fg: "var(--blue)",
    border: "var(--blue-border)",
    fill: "var(--blue)",
  },
  {
    key: "Cancellation",
    label: "Cancellation",
    bg: "var(--violet-bg)",
    fg: "var(--violet)",
    border: "var(--violet-border)",
    fill: "var(--violet)",
  },
  // W3.C — 5th pill. Amber palette mirrors VulnerabilityBanner.tsx so the
  // banner + pill read as the same signal.
  {
    key: "Vulnerable",
    label: "Vulnerable",
    bg: "var(--amber-bg)",
    fg: "var(--amber-400)",
    border: "var(--amber-border)",
    fill: "var(--amber)",
  },
];

// ── Action catalogue ──────────────────────────────────────────────

type ActionDef = {
  key: PerCpAction;
  label: string;
  Icon: React.ComponentType<LucideProps>;
};

const ACTIONS: ActionDef[] = [
  { key: "no_action", label: "No action", Icon: Minus },
  { key: "coach", label: "Coach agent", Icon: GraduationCap },
  { key: "reask_supp", label: "Re-ask in supp. call", Icon: Undo2 },
  { key: "schedule_another", label: "Schedule another call", Icon: Calendar },
  { key: "recall_redo", label: "Recall + redo whole call", Icon: RotateCcw },
  { key: "ignore", label: "Ignore (lenient pass)", Icon: CircleSlash },
];

type AggDef = {
  key: AggregateAction;
  label: string;
  Icon: React.ComponentType<LucideProps>;
  fillBg: string;
  fillFg: string;
  bg: string;
  fg: string;
  border: string;
};

// Plan §5b: the reviewer surfaces ONLY three buckets at the top —
// Pass / Non-Compliant / Needs Review. COACHING + BLOCK still exist
// server-side (mapped from the severity-weighted bucket); we just stop
// rendering them as buttons so the reviewer has a smaller decision space.
const AGG_OPTIONS: AggDef[] = [
  {
    key: "PASS",
    label: "Pass",
    Icon: CheckCircle2,
    fillBg: "var(--emerald)",
    fillFg: "#04201a",
    bg: "var(--emerald-bg)",
    fg: "var(--emerald-400)",
    border: "var(--emerald-border)",
  },
  {
    key: "REVIEW",
    label: "Needs Review",
    Icon: AlertTriangle,
    fillBg: "var(--amber)",
    fillFg: "#1a1100",
    bg: "var(--amber-bg)",
    fg: "var(--amber-400)",
    border: "var(--amber-border)",
  },
  {
    key: "FAIL",
    label: "Non-Compliant",
    Icon: XCircle,
    fillBg: "var(--red)",
    fillFg: "#fff",
    bg: "var(--red-bg)",
    fg: "var(--red)",
    border: "var(--red-border)",
  },
];

// ── Helpers ───────────────────────────────────────────────────────

function statusOf(cp: VerdictTabCheckpoint): "pass" | "fail" | "partial" | "unscored" {
  const s = cp.verdict?.status?.toLowerCase();
  if (s === "pass" || s === "fail" || s === "partial") return s;
  return "unscored";
}

function nameOf(cp: VerdictTabCheckpoint): string {
  return cp.script?.name ?? cp.verdict?.name ?? cp.key;
}

function trim(text: string | null | undefined, max = 300): string {
  const t = (text ?? "").trim();
  if (t.length <= max) return t;
  return t.slice(0, max - 1).trimEnd() + "…";
}

// ── Component ─────────────────────────────────────────────────────

export function VerdictTab(props: VerdictTabProps) {
  const {
    callId,
    agentName,
    customerName,
    filename,
    score,
    reviewerEmail,
    cpCards,
    flags,
    initialRiskTags,
    customerContract,
    onSubmitted,
  } = props;

  // W1.5 (v3-watt-coverage): risk-tag chip toggles. Optimistic local set;
  // PATCH on every toggle so the customer-page rollup stays fresh.
  const [riskTags, setRiskTags] = useState<Set<RiskTag>>(
    () => new Set(initialRiskTags ?? []),
  );
  const setRiskTagsMut = useSetCallRiskTags();

  // Sync from prop when caller hydrates after fetch.
  useEffect(() => {
    if (initialRiskTags) {
      setRiskTags(new Set(initialRiskTags));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialRiskTags?.join("|")]);

  function toggleRiskTag(tag: RiskTag) {
    const next = new Set(riskTags);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    setRiskTags(next);
    // Persist; failures rollback the local state via onError.
    setRiskTagsMut.mutate(
      { callId, tags: Array.from(next) as RiskTag[] },
      {
        onError: () => setRiskTags(riskTags),
      },
    );
  }

  // Group CPs by status (only consider scored ones for the action grid).
  const fails = cpCards.filter((c) => statusOf(c) === "fail");
  const partials = cpCards.filter((c) => statusOf(c) === "partial");
  const passes = cpCards.filter((c) => statusOf(c) === "pass");
  const total = fails.length + partials.length + passes.length;

  // Build a per-key prefilled comment from flags + verdict notes/evidence.
  // Pre-computed once per render — reused as the default for each CP.
  const flagReasonByName = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of flags) {
      const k = (f.rule_id || "").trim().toLowerCase();
      if (k && !m.has(k) && f.reason) m.set(k, f.reason);
    }
    return m;
  }, [flags]);

  function defaultCommentFor(cp: VerdictTabCheckpoint): string {
    // Prefer flag.reason (matches by rule_id ≈ checkpoint name) when present,
    // else verdict.notes (the LLM reasoning), else verdict.evidence quote.
    const flagReason = flagReasonByName.get(cp.key);
    if (flagReason) return trim(flagReason);
    if (cp.verdict?.notes) return trim(cp.verdict.notes);
    if (cp.verdict?.evidence) return trim(cp.verdict.evidence);
    return "";
  }

  // ── State ────────────────────────────────────────────────────────
  const [perCpActions, setPerCpActions] = useState<Map<string, PerCpAction>>(
    () => new Map(),
  );
  const [perCpComments, setPerCpComments] = useState<Map<string, string>>(
    () => new Map(),
  );
  const [overrideAggregate, setOverrideAggregate] =
    useState<AggregateAction | null>(null);
  const [overallReason, setOverallReason] = useState<string>("");
  const [emailPreviewOpen, setEmailPreviewOpen] = useState(true);
  const [sendEmail, setSendEmail] = useState(true);
  const [passedExpanded, setPassedExpanded] = useState(false);

  // W3.B — second email card (customer-facing). Off by default since not
  // every reviewed call needs an immediate confirmation send.
  const [sendCustomerEmail, setSendCustomerEmail] = useState(false);
  const [customerEmailPreviewOpen, setCustomerEmailPreviewOpen] = useState(true);
  const [customerEmailTo, setCustomerEmailTo] = useState("");
  const customerEmailMut = useCustomerEmail();

  // Initialise defaults once we have data: every scored CP gets
  // "no_action" + prefilled comment.
  useEffect(() => {
    if (cpCards.length === 0) return;
    setPerCpActions((prev) => {
      if (prev.size > 0) return prev;
      const m = new Map<string, PerCpAction>();
      for (const cp of cpCards) {
        if (statusOf(cp) === "unscored") continue;
        m.set(cp.key, "no_action");
      }
      return m;
    });
    setPerCpComments((prev) => {
      if (prev.size > 0) return prev;
      const m = new Map<string, string>();
      for (const cp of cpCards) {
        const def = defaultCommentFor(cp);
        if (def) m.set(cp.key, def);
      }
      return m;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cpCards.length]);

  function setAction(key: string, next: PerCpAction) {
    setPerCpActions((prev) => {
      const m = new Map(prev);
      m.set(key, next);
      return m;
    });
    // When user picks a non-no_action, ensure a comment seed exists.
    if (next !== "no_action") {
      setPerCpComments((prev) => {
        if (prev.has(key) && (prev.get(key) ?? "").length > 0) return prev;
        const cp = cpCards.find((c) => c.key === key);
        const def = cp ? defaultCommentFor(cp) : "";
        const m = new Map(prev);
        m.set(key, def);
        return m;
      });
    }
  }

  function setComment(key: string, value: string) {
    setPerCpComments((prev) => {
      const m = new Map(prev);
      m.set(key, value);
      return m;
    });
  }

  // ── Derived ──────────────────────────────────────────────────────
  const suggested = useMemo(
    () => suggestAggregate(perCpActions),
    [perCpActions],
  );
  const aggregate: AggregateAction | null = overrideAggregate ?? suggested;

  const needActionCount = useMemo(() => {
    let n = 0;
    for (const cp of cpCards) {
      const s = statusOf(cp);
      if (s === "fail" || s === "partial") {
        const a = perCpActions.get(cp.key);
        if (a && a !== "no_action") n++;
      }
    }
    return n;
  }, [cpCards, perCpActions]);

  const totalNonPass = fails.length + partials.length;

  // W3.B — customer-facing email preview (re-builds on contract changes).
  const customerEmail = useMemo(
    () =>
      buildCustomerEmailPreview({
        customerName,
        supplier: customerContract?.supplier,
        contractLength: customerContract?.contractLength,
        unitRate: customerContract?.unitRate,
        standingCharge: customerContract?.standingCharge,
        docusignRef: customerContract?.docusignRef,
        callRef: customerContract?.callRef,
        reviewerEmail,
      }),
    [
      customerName,
      customerContract?.supplier,
      customerContract?.contractLength,
      customerContract?.unitRate,
      customerContract?.standingCharge,
      customerContract?.docusignRef,
      customerContract?.callRef,
      reviewerEmail,
    ],
  );

  const email = useMemo(
    () =>
      buildEmailPreview({
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
        checkpoints: cpCards
          .filter((c) => statusOf(c) !== "unscored")
          .map((c) => ({ key: c.key, name: nameOf(c), status: statusOf(c) })),
      }),
    [
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
      cpCards,
    ],
  );

  // Auto-fill overall reason once when we have non-pass CPs (kept editable).
  useEffect(() => {
    if (overallReason.length > 0) return;
    if (totalNonPass === 0) return;
    const followUps = [...perCpActions.values()].filter(
      (a) => a === "reask_supp" || a === "schedule_another",
    ).length;
    const parts = [
      `${fails.length} checkpoint${fails.length === 1 ? "" : "s"} failed, ${partials.length} partial.`,
    ];
    if (followUps > 0) {
      parts.push(
        `Required: ${followUps} supplementary call${followUps === 1 ? "" : "s"}.`,
      );
    }
    parts.push("See per-CP comments below.");
    setOverallReason(parts.join(" "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalNonPass]);

  // Submit gate: aggregate picked + every non-pass CP that has an action
  // !== no_action must have comment ≥10 chars.
  const submitDisabled = (() => {
    if (!aggregate) return true;
    for (const cp of cpCards) {
      const s = statusOf(cp);
      if (s !== "fail" && s !== "partial") continue;
      const a = perCpActions.get(cp.key) ?? "no_action";
      if (a === "no_action") continue;
      const c = (perCpComments.get(cp.key) ?? "").trim();
      if (c.length < 10) return true;
    }
    return false;
  })();

  function handleCancel() {
    setPerCpActions(new Map());
    setPerCpComments(new Map());
    setOverrideAggregate(null);
    setOverallReason("");
    setSendEmail(true);
    setPassedExpanded(false);
  }

  function handleSubmit() {
    // PROTOTYPE: log payload + toast — do NOT fire useSubmitVerdict.
    // Backend payload shape needs extension before wiring.
    const payload = {
      callId,
      aggregate,
      overrideAggregate,
      suggested,
      overallReason,
      sendEmail,
      perCpActions: Object.fromEntries(perCpActions),
      perCpComments: Object.fromEntries(perCpComments),
      email,
    };
    // eslint-disable-next-line no-console
    console.log("[verdict-tab prototype] submit payload:", payload);
    toast.success("Verdict submitted (prototype — payload logged)", {
      description: "Backend wiring pending. See console for full shape.",
    });
    onSubmitted?.();
    handleCancel();
  }

  // ── Render helpers ───────────────────────────────────────────────

  function renderCpRow(cp: VerdictTabCheckpoint, status: "fail" | "partial" | "pass") {
    const action = perCpActions.get(cp.key) ?? "no_action";
    const comment = perCpComments.get(cp.key) ?? "";
    const showComment = status !== "pass" && action !== "no_action";
    const tone =
      status === "fail"
        ? { fg: "var(--red)", bg: "var(--red-bg)", border: "var(--red-border)", label: "FAIL" }
        : status === "partial"
          ? {
              fg: "var(--amber-400)",
              bg: "var(--amber-bg)",
              border: "var(--amber-border)",
              label: "PARTIAL",
            }
          : {
              fg: "var(--emerald-400)",
              bg: "var(--emerald-bg)",
              border: "var(--emerald-border)",
              label: "PASS",
            };

    return (
      <div
        key={cp.key}
        data-testid={`verdict-cp-${cp.key}`}
        style={{
          background: "var(--bg-elev2)",
          border: `1px solid ${tone.border}`,
          borderRadius: 8,
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: tone.fg,
              background: tone.bg,
              border: `1px solid ${tone.border}`,
              borderRadius: 4,
              padding: "2px 6px",
              letterSpacing: "0.04em",
            }}
          >
            {tone.label}
          </span>
          <div
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-primary)",
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={nameOf(cp)}
          >
            {nameOf(cp)}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label
            style={{
              fontSize: 10,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-faint)",
            }}
          >
            Action
          </label>
          <select
            disabled={status === "pass"}
            value={action}
            onChange={(e) => setAction(cp.key, e.target.value as PerCpAction)}
            data-testid={`verdict-cp-action-${cp.key}`}
            style={{
              height: 30,
              padding: "0 8px",
              background: "var(--bg-elev1)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 6,
              color: "var(--text-primary)",
              fontSize: 13,
              fontFamily: "inherit",
              outline: "none",
              cursor: status === "pass" ? "not-allowed" : "pointer",
              opacity: status === "pass" ? 0.6 : 1,
            }}
          >
            {ACTIONS.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </div>

        {showComment && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label
              style={{
                fontSize: 10,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                color: "var(--text-faint)",
              }}
            >
              Comment
            </label>
            <textarea
              value={comment}
              onChange={(e) => setComment(cp.key, e.target.value)}
              data-testid={`verdict-cp-comment-${cp.key}`}
              placeholder="Coaching note for the agent (≥10 chars)…"
              style={{
                width: "100%",
                minHeight: 64,
                padding: 8,
                background: "var(--bg-elev1)",
                border: `1px solid ${
                  comment.trim().length < 10
                    ? "var(--amber-border)"
                    : "var(--border-subtle)"
                }`,
                borderRadius: 6,
                color: "var(--text-primary)",
                fontSize: 12,
                lineHeight: 1.45,
                fontFamily: "inherit",
                resize: "vertical",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            {comment.trim().length > 0 && comment.trim().length < 10 && (
              <div style={{ fontSize: 11, color: "var(--amber-400)" }}>
                {10 - comment.trim().length} more character
                {10 - comment.trim().length === 1 ? "" : "s"} required.
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  // Plan §5b: only surface risk tags when the reviewer's aggregate verdict
  // is Needs Review or Non-Compliant. Pass-bound calls don't need them.
  const showRiskTags = aggregate === "REVIEW" || aggregate === "FAIL";

  return (
    <div
      data-testid="verdict-tab"
      style={{ padding: 20, display: "flex", flexDirection: "column", gap: 18 }}
    >
      {/* W1.5 — risk-tag chip strip (conditional per Plan §5b) */}
      {showRiskTags ? (
      <div data-testid="verdict-risk-tags" data-slot="risk-tags-strip">
        <div
          style={{
            fontSize: 11,
            color: "var(--text-faint)",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: 6,
          }}
        >
          Risk tags
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {RISK_TAG_CHIPS.map((c) => {
            const on = riskTags.has(c.key);
            return (
              <button
                key={c.key}
                type="button"
                role="switch"
                aria-checked={on}
                aria-label={c.label}
                data-testid={`risk-tag-${c.key.toLowerCase()}`}
                onClick={() => toggleRiskTag(c.key)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  height: 26,
                  padding: "0 10px",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  background: on ? c.fill : c.bg,
                  color: on ? "#fff" : c.fg,
                  border: `1px solid ${on ? c.fill : c.border}`,
                  borderRadius: 999,
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                {c.label}
              </button>
            );
          })}
        </div>
      </div>
      ) : null}

      {/* Aggregate verdict ───────────────────────────────────────── */}
      <div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 8,
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: "var(--text-faint)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Aggregate verdict
          </div>
          {suggested && (
            <div
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
              }}
            >
              Suggested: <strong>{suggested}</strong>
              {overrideAggregate && overrideAggregate !== suggested && (
                <>
                  {" "}
                  ·{" "}
                  <button
                    type="button"
                    onClick={() => setOverrideAggregate(null)}
                    style={{
                      background: "transparent",
                      color: "var(--blue)",
                      border: "none",
                      padding: 0,
                      cursor: "pointer",
                      fontSize: 11,
                      textDecoration: "underline",
                    }}
                  >
                    use suggestion
                  </button>
                </>
              )}
            </div>
          )}
        </div>
        <div
          role="radiogroup"
          aria-label="Aggregate verdict"
          style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}
        >
          {AGG_OPTIONS.map((v) => {
            const isChosen = aggregate === v.key;
            const isSuggested = suggested === v.key && overrideAggregate === null;
            const Icon = v.Icon;
            return (
              <button
                key={v.key}
                type="button"
                role="radio"
                aria-checked={isChosen}
                data-testid={`verdict-agg-${v.key}`}
                onClick={() => setOverrideAggregate(v.key)}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 4,
                  padding: "10px 4px",
                  background: isChosen ? v.fillBg : v.bg,
                  color: isChosen ? v.fillFg : v.fg,
                  border: `${isSuggested && !isChosen ? "2px dashed " : "1px solid "}${
                    isChosen ? v.fillBg : v.border
                  }`,
                  borderRadius: 8,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  fontWeight: 500,
                  boxShadow: isChosen
                    ? "var(--shadow-md), inset 0 1px 0 rgba(255,255,255,0.15)"
                    : "none",
                }}
              >
                <Icon size={16} strokeWidth={1.75} />
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    letterSpacing: "0.02em",
                    textTransform: "uppercase",
                  }}
                >
                  {v.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Required actions counter ─────────────────────────────────── */}
      <div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 10,
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: "var(--text-faint)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Required actions
          </div>
          <div
            data-testid="verdict-counter"
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "2px 8px",
              borderRadius: 999,
              background: needActionCount > 0 ? "var(--amber-bg)" : "var(--emerald-bg)",
              color:
                needActionCount > 0 ? "var(--amber-400)" : "var(--emerald-400)",
              border: `1px solid ${
                needActionCount > 0 ? "var(--amber-border)" : "var(--emerald-border)"
              }`,
            }}
          >
            {needActionCount} of {total} need action
          </div>
        </div>

        {/* Failed group */}
        {fails.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: "var(--red)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              Failed ({fails.length})
            </div>
            {fails.map((cp) => renderCpRow(cp, "fail"))}
          </div>
        )}

        {/* Partial group */}
        {partials.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: "var(--amber-400)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              Partial ({partials.length})
            </div>
            {partials.map((cp) => renderCpRow(cp, "partial"))}
          </div>
        )}

        {/* Passed group — collapsed by default */}
        {passes.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <button
              type="button"
              onClick={() => setPassedExpanded((v) => !v)}
              data-testid="verdict-passed-toggle"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 8px",
                background: "transparent",
                border: "1px solid var(--border-subtle)",
                borderRadius: 6,
                color: "var(--emerald-400)",
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
                cursor: "pointer",
                fontFamily: "inherit",
                textAlign: "left",
              }}
            >
              {passedExpanded ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
              Passed ({passes.length}) — no action needed
            </button>
            {passedExpanded && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {passes.map((cp) => renderCpRow(cp, "pass"))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Overall reason ───────────────────────────────────────────── */}
      <div>
        <div
          style={{
            fontSize: 11,
            color: "var(--text-faint)",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: 6,
          }}
        >
          Overall reason
        </div>
        <textarea
          value={overallReason}
          onChange={(e) => setOverallReason(e.target.value)}
          data-testid="verdict-overall-reason"
          placeholder="Auto-summary appears once per-CP actions are picked. Edit freely."
          style={{
            width: "100%",
            minHeight: 90,
            padding: 12,
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            color: "var(--text-primary)",
            fontSize: 13,
            lineHeight: 1.5,
            fontFamily: "inherit",
            resize: "vertical",
            outline: "none",
            boxSizing: "border-box",
          }}
        />
      </div>

      {/* Email preview — Plan §5b: "Coming soon" stub, label is visually
          disabled and toggling is blocked. Server endpoint stays parked. */}
      <div>
        <label
          title="Coming soon — agent feedback emails are not wired up yet"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 12px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            cursor: "not-allowed",
            opacity: 0.55,
            marginBottom: 8,
          }}
        >
          <div
            style={{
              width: 28,
              height: 16,
              borderRadius: 8,
              background: "var(--border-strong)",
              position: "relative",
              flexShrink: 0,
            }}
          >
            <div
              style={{
                position: "absolute",
                top: 2,
                left: 2,
                width: 12,
                height: 12,
                borderRadius: 6,
                background: "#fff",
              }}
            />
          </div>
          <Mail size={14} color="var(--text-muted)" />
          <div style={{ flex: 1, fontSize: 13, color: "var(--text-primary)" }}>
            Send feedback email to agent
            <span
              style={{
                marginLeft: 8,
                fontSize: 11,
                color: "var(--text-faint)",
                fontStyle: "italic",
              }}
            >
              · Coming soon
            </span>
          </div>
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              setEmailPreviewOpen((v) => !v);
            }}
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
              padding: 2,
            }}
            data-testid="verdict-email-preview-toggle"
          >
            {emailPreviewOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </label>

        {emailPreviewOpen && sendEmail && (
          <div
            data-testid="verdict-email-preview"
            style={{
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 6,
              padding: 12,
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
              fontSize: 12,
              color: "var(--text-primary)",
              whiteSpace: "pre",
              overflowX: "auto",
            }}
          >
            <div
              style={{
                color: "var(--text-muted)",
                marginBottom: 6,
                whiteSpace: "normal",
              }}
            >
              <div>
                <span style={{ color: "var(--text-faint)" }}>To: </span>
                {agentName
                  ? `${agentName.toLowerCase().replace(/\s+/g, ".")}@agent.local`
                  : "agent@agent.local"}
              </div>
              <div>
                <span style={{ color: "var(--text-faint)" }}>Subject: </span>
                {email.subject}
              </div>
            </div>
            <div
              style={{
                borderTop: "1px solid var(--border-subtle)",
                paddingTop: 8,
              }}
            >
              {email.body}
            </div>
          </div>
        )}
      </div>

      {/* Customer confirmation email card (W3.B v3-watt-coverage) ──── */}
      <div data-testid="verdict-customer-email-card">
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 12px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            cursor: "pointer",
            marginBottom: 8,
          }}
        >
          <div
            onClick={(e) => {
              e.preventDefault();
              setSendCustomerEmail((v) => !v);
            }}
            style={{
              width: 28,
              height: 16,
              borderRadius: 8,
              background: sendCustomerEmail ? "var(--emerald)" : "var(--border-strong)",
              position: "relative",
              flexShrink: 0,
              transition: "background 100ms",
            }}
          >
            <div
              style={{
                position: "absolute",
                top: 2,
                left: sendCustomerEmail ? 14 : 2,
                width: 12,
                height: 12,
                borderRadius: 6,
                background: "#fff",
                transition: "left 120ms ease",
              }}
            />
          </div>
          <Mail size={14} color="var(--text-muted)" />
          <div style={{ flex: 1, fontSize: 13, color: "var(--text-primary)" }}>
            Send confirmation to customer
          </div>
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              setCustomerEmailPreviewOpen((v) => !v);
            }}
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
              padding: 2,
            }}
            data-testid="verdict-customer-email-preview-toggle"
          >
            {customerEmailPreviewOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </label>

        {customerEmailPreviewOpen && sendCustomerEmail && (
          <div
            data-testid="verdict-customer-email-preview"
            style={{
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 6,
              padding: 12,
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
              fontSize: 12,
              color: "var(--text-primary)",
              whiteSpace: "pre",
              overflowX: "auto",
            }}
          >
            <div
              style={{
                color: "var(--text-muted)",
                marginBottom: 8,
                whiteSpace: "normal",
                fontFamily: "inherit",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ color: "var(--text-faint)", minWidth: 36 }}>To:</span>
                <input
                  type="email"
                  value={customerEmailTo}
                  onChange={(e) => setCustomerEmailTo(e.target.value)}
                  placeholder="customer@company.example (optional override)"
                  data-testid="verdict-customer-email-to"
                  style={{
                    flex: 1,
                    height: 24,
                    padding: "0 6px",
                    background: "var(--bg-elev1)",
                    border: "1px solid var(--border-subtle)",
                    borderRadius: 4,
                    color: "var(--text-primary)",
                    fontSize: 12,
                    fontFamily: "inherit",
                    outline: "none",
                  }}
                />
              </div>
              <div>
                <span style={{ color: "var(--text-faint)" }}>Subject: </span>
                {customerEmail.subject}
              </div>
            </div>
            <div
              style={{
                borderTop: "1px solid var(--border-subtle)",
                paddingTop: 8,
              }}
            >
              {customerEmail.body}
            </div>
            <div
              style={{
                marginTop: 10,
                paddingTop: 10,
                borderTop: "1px solid var(--border-subtle)",
                display: "flex",
                justifyContent: "flex-end",
              }}
            >
              <button
                type="button"
                disabled={customerEmailMut.isPending}
                onClick={() =>
                  customerEmailMut.mutate({
                    callId,
                    to: customerEmailTo.trim() || undefined,
                  })
                }
                data-testid="verdict-customer-email-send"
                style={{
                  height: 28,
                  padding: "0 12px",
                  background: "var(--blue)",
                  color: "#04162a",
                  border: "1px solid var(--blue)",
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 500,
                  cursor: customerEmailMut.isPending ? "not-allowed" : "pointer",
                  opacity: customerEmailMut.isPending ? 0.6 : 1,
                  fontFamily: "inherit",
                }}
              >
                {customerEmailMut.isPending ? "Sending…" : "Send to customer"}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Submit row ────────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
        <button
          onClick={handleCancel}
          data-testid="verdict-cancel"
          style={{
            flex: 1,
            height: 32,
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            color: "var(--text-primary)",
            borderRadius: 6,
            fontSize: 13,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          Cancel
        </button>
        <button
          disabled={submitDisabled}
          onClick={handleSubmit}
          data-testid="verdict-submit"
          style={{
            flex: 2,
            height: 32,
            background: "var(--emerald)",
            color: "#04201a",
            border: "1px solid var(--emerald)",
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 500,
            cursor: submitDisabled ? "not-allowed" : "pointer",
            opacity: submitDisabled ? 0.5 : 1,
            fontFamily: "inherit",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          Submit verdict
        </button>
      </div>
    </div>
  );
}
