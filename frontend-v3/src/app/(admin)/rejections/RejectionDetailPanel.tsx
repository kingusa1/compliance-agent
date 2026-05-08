"use client";

/**
 * RejectionDetailPanel — right pane (40%) of the master-detail layout.
 *
 * Faithful port of design/handoff-bundle/project/hifi/rejections-hifi.jsx
 * (RejDetailPanel + AuditLog + RejDropdown).
 *
 * Layout (top-down):
 *   1. Header — id pill + status pill + DeadlineBadge + customer name
 *      + MPAN + supplier + agent + "Open in Watt portal ↗"
 *   2. Category (CategoryBadgeLarge)
 *   3. Rejection reason (mono card)
 *   4. Pipeline (StatusPipeline)
 *   5. Fix Required + Fix Assignee (2-col)  [only on Active mode]
 *   6. Audit log (vertical timeline)
 *   7. Footer actions
 */
import { Ban, Check, CheckCircle2, Download } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Avatar } from "@/components/design/Avatar";
import { Pill, type PillTone } from "@/components/design/Pill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useRejectionAuditLogQuery, useReviewersQuery } from "@/lib/queries/rejections";
import { usePatchRejection, useTransitionRejection } from "@/lib/mutations/rejections";
import {
  DEAD_REASONS,
  REJECTION_OUTCOME_LABELS,
  REJECTION_OUTCOMES,
  REJECTION_STATUS_LABELS,
  REMEDIATION_ACTION_LABELS,
  REMEDIATION_ACTIONS,
  type DeadReason,
  type Rejection,
  type RejectionOutcome,
  type RejectionStatus,
  type RemediationAction,
} from "@/lib/schemas/rejections";

import { CategoryBadgeLarge } from "./CategoryChip";
import { DeadReasonChip } from "./DeadReasonChip";
import { DeadlineBadge } from "./DeadlineBadge";
import { StatusPipeline } from "./StatusPipeline";

const STATUS_TONES: Record<RejectionStatus, PillTone> = {
  NOT_STARTED: "neutral",
  IN_PROGRESS: "amber",
  FIXED: "emerald",
  BATCHED_TO_PORTAL: "blue",
  SUBMITTED_TO_PORTAL: "violet",
  FIXED_AND_APPROVED: "emerald",
  DEAD: "red",
};

const MICRO: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-dim)",
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  fontWeight: 500,
};

function StatusPill({ status }: { status: RejectionStatus | string }) {
  const tone = (STATUS_TONES[status as RejectionStatus] ?? "neutral") as PillTone;
  return (
    <Pill tone={tone} dot>
      {REJECTION_STATUS_LABELS[status as RejectionStatus] ?? String(status)}
    </Pill>
  );
}

function AuditTimeline({ rejectionId }: { rejectionId: string }) {
  const q = useRejectionAuditLogQuery(rejectionId);
  const entries = q.data?.audit_log ?? [];
  if (q.isLoading && entries.length === 0) {
    return <div style={{ color: "var(--text-dim)", fontSize: 12 }}>Loading audit log…</div>;
  }
  if (entries.length === 0) {
    return <div style={{ color: "var(--text-dim)", fontSize: 12 }}>No audit entries yet.</div>;
  }
  return (
    <div style={{ position: "relative", paddingLeft: 18 }}>
      <div
        style={{
          position: "absolute",
          left: 5,
          top: 6,
          bottom: 6,
          width: 1,
          background: "var(--border-subtle)",
        }}
      />
      {entries.map((e, i) => {
        const tone =
          e.action === "created"
            ? "neutral"
            : e.to_status === "DEAD"
              ? "red"
              : e.to_status === "FIXED_AND_APPROVED" || e.to_status === "FIXED"
                ? "emerald"
                : e.action === "transitioned"
                  ? "amber"
                  : "neutral";
        const dotColor =
          tone === "emerald"
            ? "var(--emerald)"
            : tone === "red"
              ? "var(--red)"
              : tone === "amber"
                ? "var(--amber)"
                : "var(--bg-elev3)";
        const ringColor =
          tone === "emerald"
            ? "var(--emerald)"
            : tone === "red"
              ? "var(--red)"
              : tone === "amber"
                ? "var(--amber)"
                : "var(--border-strong)";
        const who = e.actor_id ?? "system";
        const when = e.created_at
          ? new Date(e.created_at).toLocaleString(undefined, {
              month: "short",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "";
        const what =
          e.action === "transitioned"
            ? `${e.from_status ?? "?"} → ${e.to_status ?? "?"}`
            : e.action ?? "—";
        return (
          <div
            key={e.id}
            style={{
              position: "relative",
              paddingBottom: i === entries.length - 1 ? 0 : 14,
            }}
          >
            <div
              style={{
                position: "absolute",
                left: -17,
                top: 5,
                width: 11,
                height: 11,
                borderRadius: "50%",
                background: dotColor,
                border: "1.5px solid var(--bg-elev1)",
                boxShadow: `0 0 0 1.5px ${ringColor}`,
              }}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
              <Avatar name={who} size={16} />
              <span
                style={{
                  fontSize: 12.5,
                  color: "var(--text-primary)",
                  fontWeight: 500,
                  letterSpacing: "-0.003em",
                }}
              >
                {who}
              </span>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{what}</span>
              <div style={{ flex: 1 }} />
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10.5,
                  color: "var(--text-dim)",
                }}
              >
                {when}
              </span>
            </div>
            {e.notes && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  marginTop: 2,
                  lineHeight: 1.5,
                  letterSpacing: "-0.003em",
                }}
              >
                {e.notes}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export type RejectionDetailPanelProps = {
  rejection: Rejection;
};

export function RejectionDetailPanel({ rejection }: RejectionDetailPanelProps) {
  const isDead = rejection.status === "DEAD";
  const isTerminal =
    rejection.status === "FIXED_AND_APPROVED" || rejection.status === "DEAD";

  // Form-ish local state — mirrors server, only flushed on "Save changes".
  const [fixRequired, setFixRequired] = useState<RemediationAction | "">(
    (rejection.fix_required as RemediationAction | null) ?? "",
  );
  const [assigneeId, setAssigneeId] = useState<string>(rejection.fix_assignee_id ?? "");
  const [outcome, setOutcome] = useState<RejectionOutcome | "">(
    (rejection.outcome as RejectionOutcome | null) ?? "",
  );
  const [outcomeNarrative, setOutcomeNarrative] = useState<string>(
    rejection.outcome_narrative ?? "",
  );
  // W4.6 — dead-reason picker. Only shown when status=DEAD; gates "Save outcome".
  const [deadReason, setDeadReason] = useState<DeadReason | "">(
    (rejection.dead_reason as DeadReason | null) ?? "",
  );

  // Reset local state when the parent picks a different rejection.
  useEffect(() => {
    setFixRequired((rejection.fix_required as RemediationAction | null) ?? "");
    setAssigneeId(rejection.fix_assignee_id ?? "");
    setOutcome((rejection.outcome as RejectionOutcome | null) ?? "");
    setOutcomeNarrative(rejection.outcome_narrative ?? "");
    setDeadReason((rejection.dead_reason as DeadReason | null) ?? "");
  }, [rejection.id, rejection.fix_required, rejection.fix_assignee_id, rejection.outcome, rejection.outcome_narrative, rejection.dead_reason]);

  const reviewers = useReviewersQuery();
  const patch = usePatchRejection();
  const transition = useTransitionRejection();

  const reviewerLookup = useMemo(() => {
    const map = new Map<string, string>();
    for (const r of reviewers.data?.reviewers ?? []) {
      map.set(r.id, r.name || r.email || r.id);
    }
    return map;
  }, [reviewers.data]);

  const dirty =
    fixRequired !== ((rejection.fix_required as string | null) ?? "") ||
    assigneeId !== (rejection.fix_assignee_id ?? "") ||
    outcome !== ((rejection.outcome as string | null) ?? "") ||
    outcomeNarrative !== (rejection.outcome_narrative ?? "") ||
    deadReason !== ((rejection.dead_reason as string | null) ?? "");

  const onSave = () => {
    patch.mutate({
      id: rejection.id,
      body: {
        fix_required: fixRequired || null,
        fix_assignee_id: assigneeId || null,
        outcome: outcome || null,
        outcome_narrative: outcomeNarrative || null,
        // W4.6 — only send dead_reason on DEAD rows; otherwise omit so we
        // don't accidentally clear a value on an unrelated patch.
        ...(isDead ? { dead_reason: deadReason || null } : {}),
      },
    });
  };

  const onMarkDead = () => {
    transition.mutate({ id: rejection.id, to_status: "DEAD", notes: "Marked dead from detail panel" });
  };

  return (
    <div
      data-slot="rejection-detail-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        background: "var(--bg-elev1)",
      }}
    >
      {/* HEADER */}
      <div
        style={{
          padding: "20px 24px 18px",
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 10,
            flexWrap: "wrap",
          }}
        >
          <Pill tone="neutral" mono>
            {rejection.id.slice(0, 8)}
          </Pill>
          <StatusPill status={rejection.status} />
          {!isTerminal && (
            <DeadlineBadge deadline={rejection.deadline} status={rejection.status} />
          )}
          {rejection.status === "FIXED_AND_APPROVED" && (
            <Pill tone="emerald" dot>
              resolved
            </Pill>
          )}
          {isDead && (
            <Pill tone="red" dot>
              cannot recover
            </Pill>
          )}
        </div>
        <div
          style={{
            fontSize: 24,
            fontWeight: 600,
            letterSpacing: "-0.022em",
            color: "var(--text-primary)",
            lineHeight: 1.15,
          }}
        >
          {rejection.customer_slug ?? "—"}
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginTop: 8,
            flexWrap: "wrap",
          }}
        >
          {rejection.supplier && (
            <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
              {rejection.supplier}
            </span>
          )}
          {rejection.sales_agent && (
            <>
              <span style={{ color: "var(--text-dim)", fontSize: 12 }}>·</span>
              <span
                style={{
                  fontSize: 12.5,
                  color: "var(--text-muted)",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                }}
              >
                <Avatar name={rejection.sales_agent} size={16} /> {rejection.sales_agent}
              </span>
            </>
          )}
          <div style={{ flex: 1 }} />
          {rejection.external_watt_site_id != null && (
            <a
              href={`https://api.wattutilities.co.uk:4433/sites/${rejection.external_watt_site_id}`}
              target="_blank"
              rel="noreferrer noopener"
              style={{
                fontSize: 12,
                color: "var(--emerald-400)",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                textDecoration: "none",
                fontWeight: 500,
                padding: "4px 8px",
                borderRadius: 4,
                background: "var(--emerald-bg)",
                border: "1px solid var(--emerald-border)",
                cursor: "pointer",
              }}
            >
              Open in Watt portal
              <span style={{ fontSize: 11 }} aria-hidden>
                ↗
              </span>
            </a>
          )}
        </div>
      </div>

      {/* BODY */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "18px 24px",
          display: "flex",
          flexDirection: "column",
          gap: 18,
        }}
      >
        <div>
          <div style={{ ...MICRO, marginBottom: 8 }}>Category</div>
          <CategoryBadgeLarge category={rejection.category} />
        </div>

        <div>
          <div style={{ ...MICRO, marginBottom: 8 }}>Rejection reason</div>
          <div
            style={{
              padding: 14,
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 6,
              fontFamily: "var(--font-mono)",
              fontSize: 12.5,
              color: "var(--text-primary)",
              lineHeight: 1.6,
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <span style={{ color: "var(--text-dim)" }}>›</span>{" "}
            {rejection.rejection_reason}
          </div>
        </div>

        <div>
          <div style={{ ...MICRO, marginBottom: 12 }}>Pipeline</div>
          <div
            style={{
              padding: "16px 12px 10px",
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 6,
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <StatusPipeline current={rejection.status} isDead={isDead} />
          </div>
        </div>

        {!isTerminal && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={MICRO}>Fix required</label>
              <Select
                value={fixRequired || undefined}
                onValueChange={(v) => setFixRequired(v as RemediationAction)}
              >
                <SelectTrigger
                  data-slot="fix-required-trigger"
                  style={{ height: 34 }}
                >
                  <SelectValue placeholder="Choose action…" />
                </SelectTrigger>
                <SelectContent>
                  {REMEDIATION_ACTIONS.map((a) => (
                    <SelectItem key={a} value={a}>
                      {REMEDIATION_ACTION_LABELS[a]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={MICRO}>Fix assignee</label>
              <Select
                value={assigneeId || undefined}
                onValueChange={(v) => setAssigneeId(v ?? "")}
              >
                <SelectTrigger
                  data-slot="fix-assignee-trigger"
                  style={{ height: 34 }}
                >
                  <SelectValue placeholder="Pick reviewer…" />
                </SelectTrigger>
                <SelectContent>
                  {(reviewers.data?.reviewers ?? []).map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      {r.name || r.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {assigneeId && reviewerLookup.has(assigneeId) && (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--text-dim)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {reviewerLookup.get(assigneeId)}
                </div>
              )}
            </div>
          </div>
        )}

        {isTerminal && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12 }}>
            {/* W4.6 — dead-reason picker (DEAD rows only). Mirrors the
                Fix Required dropdown shape so the form rhythm stays
                consistent. Read-only chip preview right of the select. */}
            {isDead && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={MICRO}>Dead reason</label>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    flexWrap: "wrap",
                  }}
                >
                  <Select
                    value={deadReason || undefined}
                    onValueChange={(v) => setDeadReason(v as DeadReason)}
                  >
                    <SelectTrigger
                      data-slot="dead-reason-trigger"
                      style={{ height: 34, flex: 1, minWidth: 220 }}
                    >
                      <SelectValue placeholder="Pick a reason…" />
                    </SelectTrigger>
                    <SelectContent>
                      {DEAD_REASONS.map((dr) => (
                        <SelectItem key={dr} value={dr}>
                          {dr.replace(/_/g, " ")}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {deadReason && <DeadReasonChip reason={deadReason} />}
                </div>
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={MICRO}>Outcome</label>
              <Select
                value={outcome || undefined}
                onValueChange={(v) => setOutcome(v as RejectionOutcome)}
              >
                <SelectTrigger style={{ height: 34 }}>
                  <SelectValue placeholder="Pick outcome…" />
                </SelectTrigger>
                <SelectContent>
                  {REJECTION_OUTCOMES.map((o) => (
                    <SelectItem key={o} value={o}>
                      {REJECTION_OUTCOME_LABELS[o]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={MICRO}>Outcome narrative</label>
              <Textarea
                value={outcomeNarrative}
                onChange={(e) => setOutcomeNarrative(e.target.value)}
                rows={3}
                placeholder="What happened, in one or two lines."
              />
            </div>
          </div>
        )}

        <div>
          <div style={{ ...MICRO, marginBottom: 12 }}>Audit log</div>
          <AuditTimeline rejectionId={rejection.id} />
        </div>
      </div>

      {/* FOOTER */}
      <div
        style={{
          padding: 16,
          borderTop: "1px solid var(--border-subtle)",
          display: "flex",
          gap: 8,
          background: "var(--bg-elev1)",
        }}
      >
        {!isTerminal && (
          <>
            <Button
              variant="destructive"
              size="sm"
              onClick={onMarkDead}
              disabled={transition.isPending}
            >
              <Ban size={14} strokeWidth={1.75} />
              Mark dead
            </Button>
            <div style={{ flex: 1 }} />
            <Button
              variant="default"
              size="sm"
              onClick={onSave}
              disabled={!dirty || patch.isPending}
            >
              <Check size={14} strokeWidth={1.75} />
              Save changes
            </Button>
          </>
        )}
        {rejection.status === "FIXED_AND_APPROVED" && (
          <>
            <div style={{ flex: 1 }} />
            <Button variant="outline" size="sm">
              <Download size={14} strokeWidth={1.75} />
              Export audit
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={onSave}
              disabled={!dirty || patch.isPending}
            >
              <CheckCircle2 size={14} strokeWidth={1.75} />
              Save outcome
            </Button>
          </>
        )}
        {isDead && (
          <>
            <div style={{ flex: 1 }} />
            <Button variant="outline" size="sm">
              <Download size={14} strokeWidth={1.75} />
              Export
            </Button>
            {/* W4.6 — Save button surfaces when reviewer edits dead_reason. */}
            <Button
              variant="default"
              size="sm"
              onClick={onSave}
              disabled={!dirty || patch.isPending}
            >
              <CheckCircle2 size={14} strokeWidth={1.75} />
              Save reason
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
