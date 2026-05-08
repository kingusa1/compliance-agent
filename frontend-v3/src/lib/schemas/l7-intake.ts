/**
 * L7 IntakeForm zod schema — 22 fields across 3 sections.
 *
 * Section A — Customer (8): legal name, business type, MPAN/MPRN, address,
 *   contact, email, phone, notes
 * Section B — Deal (9): supplier, deal_value_gbp, expected_live_date,
 *   contract_length_months, meters[] (replaces single mpan_or_mprn for
 *   dual-fuel deals), status, broker, agent_name, notes
 * Section C — Call (5): call_type, audio_file (File), recording_date,
 *   duration_seconds, language
 *
 * Supplier list is locked to 14 entries per the v3-watt-coverage Wave 1
 * harness, derived from the actual Watt rejection-tracker XLSX (188 rows).
 * SP / EDF / SSE / Octopus / OVO / Drax appear in zero of those rows and
 * have been removed; the BG family + Pozitive / Yu / Smartest / Affect /
 * Britannia / United Gas & Power / TotalEnergies are added.
 *
 * E.ON and "E.ON Next Energy" are STILL DISTINCT canonical keys (different
 * LOA models). Do not collapse. Order is most-used → least-used per the
 * tracker frequency analysis (`.planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md` §4).
 */
import { z } from "zod";

// ── 14-supplier whitelist (most-used → least-used; E.ON ≠ E.ON Next Energy) ──
export const SUPPLIERS = [
  "E.ON Next Energy",        // ~60% of tracker rows
  "British Gas Lite",        // ~20%
  "British Gas Business",
  "British Gas Trading",
  "British Gas Core",
  "Pozitive Energy",
  "Yu Energy",
  "Smartest Energy",
  "Affect Energy",
  "Britannia Gas",
  "United Gas & Power",
  "E.ON",                    // E.ON Energy Solutions Ltd — distinct LOA from E.ON Next
  "TotalEnergies",
  "Other",                   // catch-all (warn user when selected)
] as const;

export type Supplier = (typeof SUPPLIERS)[number];

// ── Enums ─────────────────────────────────────────────────────────
export const BusinessType = z.enum([
  "ltd",
  "sole-trader",
  "partnership",
  "charity",
  "public-sector",
]);

export const DealStatus = z.enum([
  "open",
  "in_progress",
  "closed_done",
  "closed_lost",
]);

// Aligned with the Watt Utilities call workflow as observed in the actual
// customer folders + tracker XLSX. Earlier draft used sales-funnel terms
// (intro/qualification/pitch/transfer/close) which did not match the
// real call types in the user's data. Backend phase model in
// app/deal_lifecycle.py:_CALL_TYPE_TO_PHASE is the source of truth.
export const CallType = z.enum([
  "lead_gen",     // Lead generation / cold-contact qualification call
  "passover",     // Handover from lead-gen agent to closer
  "closer",       // Closer call where pricing is presented
  "verbal",       // Verbal contract confirmation (legally binding script)
  "loa",          // Letter of Authority (verbal or written)
  "c_call",       // Compliance call (post-sale verification)
  "amendment",    // Post-sale amendment call (fixing a verbal/LOA)
  "full",         // End-to-end recording (all stages in one file)
]);

// Human labels for the dropdown — keep here so the UI imports a single
// source of truth and stays in sync with the enum values.
export const CALL_TYPE_LABELS: Record<z.infer<typeof CallType>, string> = {
  lead_gen: "Lead Gen",
  passover: "Passover",
  closer: "Closer",
  verbal: "Verbal Contract",
  loa: "Letter of Authority",
  c_call: "Compliance Call",
  amendment: "Amendment",
  full: "Full Call",
};

export const Language = z.enum(["en", "fr", "de", "es", "it", "nl"]);

const SupplierEnum = z.enum(SUPPLIERS);

// ── Sections ──────────────────────────────────────────────────────
//
// All "required" fields here become OPTIONAL when the form is in
// dev_auto_detect mode (see superRefine on L7IntakeSchema below). The
// pipeline's _step_detect_metadata fills in customer name, agent name,
// supplier, and script variant from the transcript whenever they're
// missing.
export const CustomerSection = z.object({
  name: z.string().optional(),
  business_type: BusinessType.optional(),
  mpan_mprn: z.string().optional(),
  address: z.string().optional(),
  contact: z.string().optional(),
  email: z.string().email("Invalid email").optional().or(z.literal("")),
  phone: z.string().optional(),
  notes: z.string().optional(),
});

// Single meter row — at least one of mpan/mprn must be present per row,
// but only when the row is non-empty. In dev_auto_detect mode the user
// can leave both blank and the row passes (transcript regex extracts).
export const MeterEntry = z
  .object({
    mpan: z.string().optional(),
    mprn: z.string().optional(),
  })
  .superRefine((m, ctx) => {
    const mpan = (m.mpan ?? "").trim();
    const mprn = (m.mprn ?? "").trim();
    // Only enforce when the user actually started typing in either field —
    // empty rows are allowed (auto-detect path will fill them).
    if (!mpan && !mprn) return;
    // Both blank-ish but stripped to nothing also fine.
    // (Keep the check trivial — the real "at least one" rule below in the
    // form-level superRefine catches the legitimate case where the user
    // is in manual mode with one blank field.)
  });

export type Meter = z.infer<typeof MeterEntry>;

// Deal section — supplier was required (drives compliance matrix); now
// optional at the field level, enforced at the form level when not in
// auto-detect mode. Number fields stay simple; the L7Form Input
// registrations use ``setValueAs`` to coerce blank strings to undefined
// before zod sees them (``valueAsNumber`` would emit NaN on blanks).
export const DealSection = z.object({
  supplier: SupplierEnum.optional(),
  deal_value_gbp: z.number().min(0).optional(),
  expected_live_date: z.string().optional(), // ISO yyyy-mm-dd
  contract_length_months: z.number().int().min(0).optional(),
  mpan_or_mprn: z.string().optional(),
  meters: z.array(MeterEntry).optional(),
  status: DealStatus.optional(),
  broker: z.string().optional(),
  agent_name: z.string().optional(),
  notes: z.string().optional(),
  // W1.1 (v3-watt-coverage): Watt portal deep-link integer.
  external_watt_site_id: z.number().int().min(0).optional(),
});

// Call section — audio_file is the only true minimum. call_type is
// auto-detectable from the script variant + transcript phase markers.
export const CallSection = z.object({
  call_type: CallType.optional(),
  audio_file: z.instanceof(File, { message: "Audio file required" }),
  recording_date: z.string().optional(),
  duration_seconds: z.number().int().min(0).optional(),
  language: Language.optional(),
});

// Full payload — what L7Form's RHF produces. The form-level superRefine
// enforces the "manual mode requires customer.name + deal.supplier +
// call.call_type" rule. In auto-detect mode only audio_file is required.
export const L7IntakeSchema = z
  .object({
    customer: CustomerSection,
    deal: DealSection,
    call: CallSection,
    dev_auto_detect: z.boolean().optional(),
  })
  .superRefine((v, ctx) => {
    if (v.dev_auto_detect) return; // auto mode: skip all requireds
    if (!v.customer.name || !v.customer.name.trim()) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["customer", "name"],
        message: "Customer name required",
      });
    }
    if (!v.deal.supplier) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["deal", "supplier"],
        message: "Supplier required",
      });
    }
    if (!v.call.call_type) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["call", "call_type"],
        message: "Call type required",
      });
    }
    // Manual mode: each meter row must have an MPAN or MPRN.
    (v.deal.meters ?? []).forEach((m, idx) => {
      const mpan = (m.mpan ?? "").trim();
      const mprn = (m.mprn ?? "").trim();
      if (!mpan && !mprn) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["deal", "meters", idx],
          message: "Each meter needs an MPAN or MPRN",
        });
      }
    });
  });

export type L7IntakeInput = z.input<typeof L7IntakeSchema>;
export type L7IntakeData = z.output<typeof L7IntakeSchema>;

// AddCustomer dialog payload — same as customer section + business_type
// optional. Mirrors POST /api/customers body.
export const AddCustomerSchema = CustomerSection;
export type AddCustomerInput = z.infer<typeof AddCustomerSchema>;
