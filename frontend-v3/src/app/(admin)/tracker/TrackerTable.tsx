"use client";
import { useMemo } from "react";
import { CategoryChip } from "./CategoryChip";
import { VerdictBadge } from "./VerdictBadge";
import { StatusPipelinePill } from "./StatusPipelinePill";
import { InlineEditCell } from "./InlineEditCell";
import { SourceBadge } from "./SourceBadge";
import { formatCustomerName } from "@/lib/customer";
import type { TrackerFieldSource, TrackerRow, TrackerTab } from "@/lib/queries/tracker";
import { useActiveReviewersQuery } from "@/lib/queries/reviewers";

type Props = {
  rows: TrackerRow[];
  tab: TrackerTab;
  selectedRowId: string | null;
  onSelect: (row: TrackerRow) => void;
};

function fmtDate(s: string | null): string {
  if (!s) return "—";
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
  } catch { return "—"; }
}

function fmtCurrency(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 }).format(v);
}

function deadlineCountdown(deadline: string | null): { label: string; tone: "red" | "amber" | "green" | "muted" } {
  if (!deadline) return { label: "—", tone: "muted" };
  const d = new Date(deadline);
  if (isNaN(d.getTime())) return { label: "—", tone: "muted" };
  const ms = d.getTime() - Date.now();
  const hours = Math.floor(ms / (1000 * 60 * 60));
  if (ms <= 0) return { label: "overdue", tone: "red" };
  if (hours < 24) return { label: `in ${hours}h`, tone: "red" };
  if (hours < 48) return { label: `in ${Math.floor(hours / 24)}d ${hours % 24}h`, tone: "amber" };
  return { label: `in ${Math.floor(hours / 24)}d`, tone: "green" };
}

const HEADER_CELL = "px-2 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]";
const BODY_CELL = "px-2 py-2 text-[12px] align-top whitespace-nowrap";

function fieldSource(row: TrackerRow, field: string): TrackerFieldSource {
  return row.field_sources?.[field] ?? "placeholder";
}

const REJECTION_CATEGORIES = [
  "ADMIN_ERROR",
  "PROCESS_FAILURE",
  "VERBAL_SALES_ERROR",
  "COMPLIANCE_ISSUE",
  "COMPLIANCE_ERROR",
  "PRICING_ISSUE",
  "PRICING_ERROR",
  "DOCUSIGN_ERROR",
  "FAILED_CREDIT_CHECK",
] as const;

const REMEDIATION_ACTIONS = [
  "AMENDMENT_CALL",
  "CONFIRMATION_CALL",
  "NEW_LOA",
  "NEW_DOCUSIGN",
  "DD_MANDATE",
  "RESELL_TO_OTHER_SUPPLIER",
  "PRICE_RECHECK",
  "COT_CHANGE_OF_TENANCY",
  "CONTRACT_LENGTH_LIMIT",
  "MANUAL_ADMIN_SUBMISSION",
] as const;

const REJECTION_STATUSES = [
  "NOT_STARTED",
  "IN_PROGRESS",
  "FIXED",
  "BATCHED_TO_PORTAL",
  "SUBMITTED_TO_PORTAL",
  "FIXED_AND_APPROVED",
  "DEAD",
] as const;

export function TrackerTable({ rows, tab, selectedRowId, onSelect }: Props) {
  // Resolve fix_assignee_id (UUID) → reviewer display name. Lookup is hot
  // (one map per render across all rows) so we build a Record<id, name>
  // once per query refresh. Falls back to "Unassigned"/"Unknown reviewer"
  // when the UUID has no matching profile (stale assignment).
  const reviewersQ = useActiveReviewersQuery();
  const reviewerById = useMemo(() => {
    const out: Record<string, string> = {};
    (reviewersQ.data ?? []).forEach((p) => {
      out[p.id] = p.name || p.email || p.id.slice(0, 8);
    });
    return out;
  }, [reviewersQ.data]);

  if (rows.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-[var(--text-muted)]">
        {tab === "active" && "Nice work — zero open rejections."}
        {tab === "fixed" && "No rejections fixed yet."}
        {tab === "dead" && "No dead rejections."}
        {tab === "compliant" && "Upload a call to get started."}
        {tab === "awaiting_review" && "Reviewer queue is empty — every completed call has been signed off."}
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full table-fixed text-[12px]">
        <thead className="sticky top-0 bg-[var(--bg-canvas)]">
          <tr className="border-b border-[var(--border-subtle)]">
            <th className={HEADER_CELL}>Customer</th>
            <th className={HEADER_CELL}>MPAN/MPRN</th>
            <th className={HEADER_CELL}>Live date</th>
            <th className={HEADER_CELL}>Value</th>
            <th className={HEADER_CELL}>Supplier</th>
            {tab !== "compliant" && <th className={HEADER_CELL}>Rejected</th>}
            <th className={HEADER_CELL}>Agent</th>
            {tab !== "compliant" && (
              <>
                <th className={HEADER_CELL}>Reason</th>
                <th className={HEADER_CELL}>Category</th>
                <th className={HEADER_CELL}>Fix</th>
                <th className={HEADER_CELL}>Fixed by</th>
                <th className={HEADER_CELL}>Status</th>
                <th className={HEADER_CELL}>Last action</th>
                <th className={HEADER_CELL}>Deadline</th>
                <th className={HEADER_CELL}>Outcome</th>
                <th className={HEADER_CELL}>Notes</th>
              </>
            )}
            {tab === "compliant" && <th className={HEADER_CELL}>Score</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const id = row.rejection_id ?? row.call_id ?? "";
            const isSel = id === selectedRowId;
            const dl = deadlineCountdown(row.deadline);
            return (
              <tr
                key={id}
                onClick={() => onSelect(row)}
                className={`cursor-pointer border-b border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)] ${isSel ? "bg-[var(--bg-elev2)]" : ""}`}
                data-row-id={id}
              >
                <td className={BODY_CELL}>{formatCustomerName(row.customer_name)}</td>
                <td className={BODY_CELL + " font-mono"}>{row.mpan_mprn ?? "—"}</td>
                <td className={BODY_CELL}>{fmtDate(row.expected_live_date)}</td>
                <td className={BODY_CELL}>{fmtCurrency(row.deal_value_gbp)}</td>
                <td className={BODY_CELL} onClick={(e) => e.stopPropagation()}>
                  {row.rejection_id ? (
                    <InlineEditCell
                      rejectionId={row.rejection_id}
                      field="supplier"
                      value={row.supplier}
                      source={fieldSource(row, "supplier")}
                    />
                  ) : (
                    <>
                      {row.supplier ?? "—"}
                      <SourceBadge source={fieldSource(row, "supplier")} />
                    </>
                  )}
                </td>
                {tab !== "compliant" && <td className={BODY_CELL}>{fmtDate(row.rejected_at)}</td>}
                <td className={BODY_CELL} onClick={(e) => e.stopPropagation()}>
                  {row.rejection_id ? (
                    <InlineEditCell
                      rejectionId={row.rejection_id}
                      field="sales_agent"
                      value={row.sales_agent}
                      source={fieldSource(row, "sales_agent")}
                    />
                  ) : (
                    <>
                      {row.sales_agent ?? "—"}
                      <SourceBadge source={fieldSource(row, "sales_agent")} />
                    </>
                  )}
                </td>
                {tab !== "compliant" && (
                  <>
                    <td
                      className={BODY_CELL + " max-w-xs truncate"}
                      title={row.rejection_reason ?? ""}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.rejection_id ? (
                        <InlineEditCell
                          rejectionId={row.rejection_id}
                          field="rejection_reason"
                          value={row.rejection_reason}
                          source={fieldSource(row, "rejection_reason")}
                        />
                      ) : (
                        row.rejection_reason ?? "—"
                      )}
                    </td>
                    <td className={BODY_CELL} onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-1.5">
                        {row.rejection_id ? (
                          <InlineEditCell
                            rejectionId={row.rejection_id}
                            field="category"
                            value={row.category}
                            source={fieldSource(row, "category")}
                            options={REJECTION_CATEGORIES}
                            renderDisplay={(v) => <CategoryChip category={v} />}
                          />
                        ) : (
                          <CategoryChip category={row.category} />
                        )}
                        {row.rejection_id && (
                          <VerdictBadge state={row.verdict_state} />
                        )}
                      </div>
                    </td>
                    <td
                      className={BODY_CELL + " font-mono text-[11px]"}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.rejection_id ? (
                        <InlineEditCell
                          rejectionId={row.rejection_id}
                          field="fix_required"
                          value={row.fix_required}
                          source={fieldSource(row, "fix_required")}
                          options={REMEDIATION_ACTIONS}
                        />
                      ) : (
                        row.fix_required ?? "—"
                      )}
                    </td>
                    <td className={BODY_CELL + " text-[var(--text-muted)]"} title={row.fix_assignee_id ?? undefined}>
                      {/* 2026-05-15: resolve assignee UUID to the reviewer's
                          display name via /api/reviewers/active. Falls back
                          to "Assigned" while reviewers query is in flight so
                          we never flash "—" between page load and resolve. */}
                      {row.fix_assignee_id
                        ? (reviewerById[row.fix_assignee_id] ?? "Assigned")
                        : "—"}
                    </td>
                    <td className={BODY_CELL} onClick={(e) => e.stopPropagation()}>
                      {row.rejection_id ? (
                        <InlineEditCell
                          rejectionId={row.rejection_id}
                          field="status"
                          value={row.status}
                          source={fieldSource(row, "status")}
                          options={REJECTION_STATUSES}
                          renderDisplay={(v) => <StatusPipelinePill status={v} />}
                        />
                      ) : (
                        <StatusPipelinePill status={row.status} />
                      )}
                    </td>
                    <td className={BODY_CELL}>{fmtDate(row.last_action_date)}</td>
                    <td className={BODY_CELL}>
                      <span className={
                        dl.tone === "red" ? "text-red-600 font-medium" :
                        dl.tone === "amber" ? "text-amber-600 font-medium" :
                        dl.tone === "green" ? "text-emerald-600" :
                        "text-[var(--text-muted)]"
                      }>{dl.label}</span>
                    </td>
                    <td
                      className={BODY_CELL + " font-mono text-[11px]"}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.rejection_id ? (
                        <InlineEditCell
                          rejectionId={row.rejection_id}
                          field="outcome"
                          value={row.outcome}
                          source={fieldSource(row, "outcome")}
                        />
                      ) : (
                        row.outcome ?? "—"
                      )}
                    </td>
                    <td
                      className={BODY_CELL + " max-w-[18ch] truncate text-[var(--text-muted)]"}
                      title={row.outcome_narrative ?? ""}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {/* Watt XLSX col P = "Notes". 2026-05-15 audit: column
                          was missing from the table entirely; reviewers had
                          to open each row to scan notes. Truncate with a
                          hover-title so a 1-line preview fits the row. */}
                      {row.outcome_narrative ? row.outcome_narrative : "—"}
                    </td>
                  </>
                )}
                {tab === "compliant" && (
                  <td className={BODY_CELL + " font-mono"}>
                    <span className="text-emerald-600 font-medium">{row.score ?? "—"}</span>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
