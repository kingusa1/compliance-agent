/**
 * W2 (v3-watt-coverage): rejection enums + zod schemas + 5-color hex map.
 *
 * Single source of truth for the /rejections page. The backend mirrors these
 * vocabularies in `backend/app/rejections_routes.py` and the alembic
 * migration `b1d4f7e2c903_w2_rejections.py`. Drift between this file and
 * those will surface as 400s from /api/rejections.
 *
 * Color values are Watt's exact hand-typed cell-fill hex codes from the
 * rejection-tracker XLSX (see `.planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md`
 * §2.8). Two compliance categories share #FFFF00; two errors share #FFC000;
 * two failures share #00B0F0 — these are the spreadsheet's actual color
 * groupings, not a coincidence.
 */
import { z } from "zod";

export const REJECTION_CATEGORIES = [
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
export type RejectionCategory = (typeof REJECTION_CATEGORIES)[number];

export const REJECTION_STATUSES = [
  "NOT_STARTED",
  "IN_PROGRESS",
  "FIXED",
  "BATCHED_TO_PORTAL",
  "SUBMITTED_TO_PORTAL",
  "FIXED_AND_APPROVED",
  "DEAD",
] as const;
export type RejectionStatus = (typeof REJECTION_STATUSES)[number];

export const REJECTION_OUTCOMES = [
  "FIXED_AND_SUBMITTED",
  "CUSTOMER_LOST",
  "CANCELLED",
  "NOT_RECOVERABLE",
  "RESIGNED_TO_OTHER_SUPPLIER",
] as const;
export type RejectionOutcome = (typeof REJECTION_OUTCOMES)[number];

export const REMEDIATION_ACTIONS = [
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
export type RemediationAction = (typeof REMEDIATION_ACTIONS)[number];

/** Watt's exact 5-color hand-typed category hex map (XLSX cell.fill). */
export const REJECTION_CATEGORY_COLORS: Record<RejectionCategory, string> = {
  ADMIN_ERROR: "#FFC000",
  PROCESS_FAILURE: "#00B0F0",
  VERBAL_SALES_ERROR: "#FF0000",
  COMPLIANCE_ISSUE: "#FFFF00",
  COMPLIANCE_ERROR: "#FFFF00",
  PRICING_ISSUE: "#92D050",
  PRICING_ERROR: "#C00000",
  DOCUSIGN_ERROR: "#FFC000",
  FAILED_CREDIT_CHECK: "#00B0F0",
};

/** Foreground "ink" colors picked for legibility on each category fill. */
export const REJECTION_CATEGORY_INK: Record<RejectionCategory, string> = {
  ADMIN_ERROR: "#3a2a00",
  PROCESS_FAILURE: "#062234",
  VERBAL_SALES_ERROR: "#ffffff",
  COMPLIANCE_ISSUE: "#2c2c00",
  COMPLIANCE_ERROR: "#2c2c00",
  PRICING_ISSUE: "#0c2208",
  PRICING_ERROR: "#ffffff",
  DOCUSIGN_ERROR: "#3a2a00",
  FAILED_CREDIT_CHECK: "#062234",
};

export const REJECTION_CATEGORY_LABELS: Record<RejectionCategory, string> = {
  ADMIN_ERROR: "Admin error",
  PROCESS_FAILURE: "Process failure",
  VERBAL_SALES_ERROR: "Verbal sales error",
  COMPLIANCE_ISSUE: "Compliance issue",
  COMPLIANCE_ERROR: "Compliance error",
  PRICING_ISSUE: "Pricing issue",
  PRICING_ERROR: "Pricing error",
  DOCUSIGN_ERROR: "DocuSign error",
  FAILED_CREDIT_CHECK: "Failed credit check",
};

export const REJECTION_STATUS_LABELS: Record<RejectionStatus, string> = {
  NOT_STARTED: "Not started",
  IN_PROGRESS: "In progress",
  FIXED: "Fixed",
  BATCHED_TO_PORTAL: "Batched",
  SUBMITTED_TO_PORTAL: "Submitted",
  FIXED_AND_APPROVED: "Approved",
  DEAD: "Dead",
};

export const REMEDIATION_ACTION_LABELS: Record<RemediationAction, string> = {
  AMENDMENT_CALL: "Amendment call",
  CONFIRMATION_CALL: "Confirmation call",
  NEW_LOA: "New LOA",
  NEW_DOCUSIGN: "New DocuSign",
  DD_MANDATE: "DD mandate",
  RESELL_TO_OTHER_SUPPLIER: "Resell to other supplier",
  PRICE_RECHECK: "Price recheck",
  COT_CHANGE_OF_TENANCY: "Change of tenancy",
  CONTRACT_LENGTH_LIMIT: "Contract length limit",
  MANUAL_ADMIN_SUBMISSION: "Manual admin submission",
};

export const REJECTION_OUTCOME_LABELS: Record<RejectionOutcome, string> = {
  FIXED_AND_SUBMITTED: "Fixed and submitted",
  CUSTOMER_LOST: "Customer lost",
  CANCELLED: "Cancelled",
  NOT_RECOVERABLE: "Not recoverable",
  RESIGNED_TO_OTHER_SUPPLIER: "Resigned to other supplier",
};

/** Pipeline order used by the horizontal stepper on the detail panel. DEAD
 *  is rendered as a bypass state, not part of the linear ladder. */
export const PIPELINE_ORDER: ReadonlyArray<RejectionStatus> = [
  "NOT_STARTED",
  "IN_PROGRESS",
  "FIXED",
  "BATCHED_TO_PORTAL",
  "SUBMITTED_TO_PORTAL",
  "FIXED_AND_APPROVED",
];

/** zod for the AddRejectionDialog form. Mirrors backend ``RejectionCreate``
 *  pydantic schema. ``rejected_at`` left server-default on create. */
export const rejectionCreateSchema = z.object({
  category: z.enum(REJECTION_CATEGORIES),
  rejection_reason: z.string().min(1, "Reason is required"),
  customer_slug: z.string().optional().or(z.literal("")),
  external_watt_site_id: z
    .union([z.coerce.number().int().positive(), z.literal("")])
    .optional(),
  supplier: z.string().optional().or(z.literal("")),
  sales_agent: z.string().optional().or(z.literal("")),
  fix_required: z.enum(REMEDIATION_ACTIONS).optional(),
  fix_assignee_id: z.string().optional().or(z.literal("")),
  call_id: z.string().optional().or(z.literal("")),
});
export type RejectionCreateValues = z.infer<typeof rejectionCreateSchema>;

/** W4.6 — dead-reason vocabulary. Mirrors backend
 *  ``rejections_routes.DEAD_REASONS``. Glosses render as filter-chip
 *  tooltips on the Dead tab. */
export const DEAD_REASONS = [
  "in_contract",
  "customer_debt",
  "wrong_owner",
  "bacs_rejected",
  "hung_up",
] as const;
export type DeadReason = (typeof DEAD_REASONS)[number];

export const DEAD_REASON_LABELS: Record<DeadReason, string> = {
  in_contract: "In contract",
  customer_debt: "Customer debt",
  wrong_owner: "Wrong owner",
  bacs_rejected: "BACS rejected",
  hung_up: "Hung up",
};

/** Server-shape from `GET /api/rejections/dead-reasons` — populated by
 *  fetch, not hard-coded in the chip filter row. */
export type DeadReasonEntry = {
  key: DeadReason | string;
  label: string;
  gloss: string;
};

export type DeadReasonsResponse = {
  dead_reasons: DeadReasonEntry[];
};

/** Server-shape returned by /api/rejections endpoints. Loose-by-design — the
 *  backend evolves faster than openapi codegen so we narrow at the boundary. */
export type Rejection = {
  id: string;
  call_id: string | null;
  customer_slug: string | null;
  external_watt_site_id: number | null;
  supplier: string | null;
  sales_agent: string | null;
  category: RejectionCategory | string;
  rejection_reason: string;
  fix_required: RemediationAction | string | null;
  // Free-text 1-sentence corrective-action narrative. Populated by
  // rejection_factory; reviewer can edit via /override.
  fix_narrative: string | null;
  fix_assignee_id: string | null;
  status: RejectionStatus | string;
  outcome: RejectionOutcome | string | null;
  outcome_narrative: string | null;
  // W4.6 — only meaningful when status=DEAD; null otherwise.
  dead_reason: DeadReason | string | null;
  created_at: string | null;
  rejected_at: string | null;
  deadline: string | null;
  resolved_at: string | null;
  // AI/HUMAN provenance gate.
  verdict_state: "AI_PENDING" | "HUMAN_CONFIRMED" | "HUMAN_OVERRIDDEN";
  confirmed_by: string | null;
  confirmed_at: string | null;
};

export type RejectionsListResponse = {
  rejections: Rejection[];
  total: number;
  counts: { active: number; fixed: number; dead: number; archive: number };
  tab: string;
  limit: number;
  offset: number;
};

export type RejectionAuditEntry = {
  id: string;
  rejection_id: string;
  actor_id: string | null;
  action: string | null;
  from_status: string | null;
  to_status: string | null;
  notes: string | null;
  created_at: string | null;
};

export type RejectionAuditLogResponse = {
  audit_log: RejectionAuditEntry[];
};
