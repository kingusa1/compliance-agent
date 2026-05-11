"use client";

import { useEffect, useMemo, useState } from "react";
import { useForm, Controller, useFieldArray } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Upload as UploadIcon, FileAudio2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SupplierCombobox } from "@/components/intake/SupplierCombobox";
import {
  MetadataMismatchBanner,
  type MismatchChoice,
} from "@/components/intake/MetadataMismatchBanner";
import {
  L7IntakeSchema,
  SUPPLIERS,
  type L7IntakeInput,
} from "@/lib/schemas/l7-intake";
import {
  useUploadCall,
  uploadCall,
  buildUploadFormData,
  MetadataMismatchError,
} from "@/lib/mutations/admin";
import { apiFetch } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";

// Compile-time check that the supplier whitelist stays at 14 — the
// SupplierCombobox unit test enforces this at runtime too. Bumped from 13
// in W1.3 (v3-watt-coverage) when the dropdown was rewritten against
// real Watt rejection-tracker data.
if (SUPPLIERS.length !== 14) {
  throw new Error(`SUPPLIERS must contain exactly 14 entries (got ${SUPPLIERS.length})`);
}

const BUSINESS_TYPES = [
  { value: "ltd", label: "Limited company" },
  { value: "sole-trader", label: "Sole trader" },
  { value: "partnership", label: "Partnership" },
  { value: "charity", label: "Charity" },
  { value: "public-sector", label: "Public sector" },
] as const;

const DEAL_STATUSES = [
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "closed_done", label: "Closed — done" },
  { value: "closed_lost", label: "Closed — lost" },
] as const;

// Aligned with the Watt Utilities call workflow (see backend
// app/deal_lifecycle.py:_CALL_TYPE_TO_PHASE for the source of truth).
// Earlier draft used sales-funnel terms (intro/qualification/pitch/
// transfer/close) which did not match the actual call types in the
// user's data — Lead Gen, Passover, Verbal Contract, LOA, Compliance,
// Amendment, Closer, Full Call.
const CALL_TYPES = [
  { value: "lead_gen", label: "Lead Gen" },
  { value: "passover", label: "Passover" },
  { value: "closer", label: "Closer" },
  { value: "verbal", label: "Verbal Contract" },
  { value: "loa", label: "Letter of Authority" },
  { value: "c_call", label: "Compliance Call" },
  { value: "amendment", label: "Amendment" },
  { value: "full", label: "Full Call" },
] as const;

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "es", label: "Español" },
  { value: "it", label: "Italiano" },
  { value: "nl", label: "Nederlands" },
] as const;

export interface L7FormPrefill {
  customer?: Partial<L7IntakeInput["customer"]> & { slug?: string };
  deal?: Partial<L7IntakeInput["deal"]>;
}

export interface L7FormProps {
  /** Pre-fill the customer section + lock it (used by /customers/[slug]). */
  prefill?: L7FormPrefill;
  /** Slug for the customer this upload is being attached to. */
  customerSlug?: string;
  onSuccess?: (callId: string | undefined) => void;
  onCancel?: () => void;
}

/**
 * L7Form — 22-field RHF form (8 customer + 9 deal + 5 call) wrapping the
 * `/api/calls/upload` multipart endpoint. Three sections rendered as
 * cards; supplier section uses SupplierCombobox to enforce the 13-entry
 * whitelist; on METADATA_MISMATCH the form swaps the submit area for
 * MetadataMismatchBanner so reviewers can pick manual/auto/edit.
 *
 * dev_auto_detect toggle is only rendered when NEXT_PUBLIC_DEV_MODE === "1"
 * OR `process.env.NODE_ENV === "development"`. Production reviewers don't
 * see it.
 */
export function L7Form({ prefill, customerSlug, onSuccess, onCancel }: L7FormProps) {
  // Auto-detect toggle is now a real reviewer feature, not a dev flag.
  // Show it always; persistence in localStorage so the choice sticks
  // across uploads.
  const showDevToggle = true;

  const form = useForm<L7IntakeInput>({
    resolver: zodResolver(L7IntakeSchema),
    defaultValues: {
      customer: {
        name: prefill?.customer?.name ?? "",
        business_type: prefill?.customer?.business_type,
        mpan_mprn: prefill?.customer?.mpan_mprn ?? "",
        address: prefill?.customer?.address ?? "",
        contact: prefill?.customer?.contact ?? "",
        email: prefill?.customer?.email ?? "",
        phone: prefill?.customer?.phone ?? "",
        notes: prefill?.customer?.notes ?? "",
      },
      deal: {
        supplier: prefill?.deal?.supplier,
        deal_value_gbp: prefill?.deal?.deal_value_gbp,
        expected_live_date: prefill?.deal?.expected_live_date ?? "",
        contract_length_months: prefill?.deal?.contract_length_months,
        mpan_or_mprn: prefill?.deal?.mpan_or_mprn ?? "",
        // W1.2 (v3-watt-coverage): start with one empty meter row; user
        // adds more for dual-fuel.
        meters: prefill?.deal?.meters ?? [{ mpan: "", mprn: "" }],
        status: prefill?.deal?.status,
        broker: prefill?.deal?.broker ?? "",
        agent_name: prefill?.deal?.agent_name ?? "",
        notes: prefill?.deal?.notes ?? "",
        external_watt_site_id: prefill?.deal?.external_watt_site_id,
      },
      call: {
        call_type: undefined,
        audio_file: undefined as unknown as File,
        recording_date: "",
        duration_seconds: undefined,
        language: "en",
      },
      // Auto-detect ON by default — the AI extracts customer/supplier/agent
      // from the audio. Manual fill is one click away if reviewers prefer.
      // (audit-late 2026-05-10 UX4: hide L7 metadata fields by default.)
      dev_auto_detect: true,
    },
  });

  const customerLocked = !!prefill?.customer?.name;
  const upload = useUploadCall();
  const [mismatch, setMismatch] = useState<{ field: string; manual: string; auto: string } | null>(null);

  // ── Multi-file auto-detect batch upload (added 2026-05-05) ─────
  // Reviewer drops 2+ audio files into the dropzone in auto-detect mode,
  // each file fires its own POST /api/calls/upload with no metadata so the
  // pipeline runs auto-detect end-to-end on every call. Shows per-file
  // status row (pending → uploading → done | error) inside the dropzone.
  type BatchRow = {
    name: string;
    size: number;
    status: "pending" | "uploading" | "done" | "error";
    callId?: string;
    error?: string;
  };
  const [batchUploads, setBatchUploads] = useState<BatchRow[]>([]);
  // Same-deal toggle: when ON alongside auto-detect, batch uploads share
  // a single CustomerDeal record created via POST /api/deals/stub before
  // the parallel uploads fire. Pipeline _step_detect_metadata is race-safe
  // (only-fill-if-blank), so the first-finished call's detection
  // backfills the shared deal.
  const [sameDeal, setSameDeal] = useState(false);
  const qc = useQueryClient();
  // Bypass useUploadCall hook here. TanStack Query's useMutation tracks a
  // single in-flight state; rapid parallel mutate() calls only render the
  // latest call's status, leaving siblings stuck at "uploading" even after
  // their fetch returned. Calling uploadCall() directly per-file gives us
  // an independent Promise per upload + reliable per-row state updates.
  const fireBatchUpload = async (files: File[]) => {
    const valid = files.filter((f) => /\.(mp3|wav|m4a)$/i.test(f.name) || f.type.startsWith("audio/"));
    if (valid.length === 0) {
      toast.error("Only MP3, WAV, or M4A audio files are accepted.");
      return;
    }
    setBatchUploads(valid.map((f) => ({ name: f.name, size: f.size, status: "pending" })));

    // Same-deal mode: create one stub deal up-front, attach every file's
    // upload to it. Pipeline _step_detect_metadata is race-safe so the
    // first-finished call's detection backfills the shared deal.
    let sharedDealId: string | null = null;
    if (sameDeal) {
      try {
        const res = await apiFetch<{ deal_id: string }>("/api/deals/stub", {
          method: "POST",
        });
        sharedDealId = res.deal_id;
      } catch {
        toast.error("Failed to create shared deal — uploads will create separate stubs");
      }
    }

    // For the single-file case (most common in auto-detect mode), route
    // the success callback back to UploadModal so the user lands on
    // /calls/{id} the moment the upload returns — matching the old
    // click-Upload behaviour. Multi-file keeps the inline status UI so
    // reviewers can pick which call to open.
    let firedNavigate = false;
    await Promise.allSettled(
      valid.map(async (file, idx) => {
        const fd = new FormData();
        fd.append("file", file);
        if (sharedDealId) fd.append("deal_id", sharedDealId);
        if (customerSlug) fd.append("customer_slug", customerSlug);
        if (prefill?.customer?.name) fd.append("customer_name", prefill.customer.name);
        setBatchUploads((prev) => prev.map((u, i) => (i === idx ? { ...u, status: "uploading" } : u)));
        try {
          const data = await uploadCall(fd);
          const cid = data.call_id ?? data.id;
          setBatchUploads((prev) =>
            prev.map((u, i) => (i === idx ? { ...u, status: "done", callId: cid } : u)),
          );
          // Navigate on the first finished single-file upload. Multi-file
          // batches keep the user on the modal so they can see all results.
          if (valid.length === 1 && !firedNavigate && cid && onSuccess) {
            firedNavigate = true;
            toast.success("Call uploaded, processing");
            onSuccess(cid);
          }
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          setBatchUploads((prev) =>
            prev.map((u, i) => (i === idx ? { ...u, status: "error", error: msg } : u)),
          );
          toast.error(msg || "Upload failed");
        }
      }),
    );
    qc.invalidateQueries({ queryKey: ["admin", "calls"] });
    qc.invalidateQueries({ queryKey: ["admin", "customers"] });
    qc.invalidateQueries({ queryKey: ["admin", "tracker"] });
  };

  // W1.2 (v3-watt-coverage): dynamic meter list — RHF useFieldArray.
  const meters = useFieldArray({
    control: form.control,
    name: "deal.meters",
  });

  const onSubmit = (values: L7IntakeInput, override?: "manual" | "auto") => {
    // Frontend CallType enum is now aligned with the backend phase model.
    // Pass through verbatim; no remap needed. `loa` maps to backend
    // `standalone_loa` for the deal_lifecycle phase resolver — keep this
    // single explicit mapping to honor the legacy backend phase name.
    const callTypeMap: Record<string, string> = {
      loa: "standalone_loa",
    };
    const rawCallType = values.call.call_type ?? "full";
    const mappedCallType = callTypeMap[rawCallType] ?? rawCallType;

    // Frontend supplier list uses display labels; backend SupplierEnum
    // is stricter (e.g. "Pozitive" not "Pozitive Energy"; "TotalEnergies
    // (out-of-matrix)" not bare "TotalEnergies"). Map at the boundary.
    const supplierMap: Record<string, string> = {
      "Pozitive Energy": "Pozitive",
      "TotalEnergies": "TotalEnergies (out-of-matrix)",
    };
    const rawSupplier = values.deal.supplier as string | undefined;
    const mappedSupplier = rawSupplier ? supplierMap[rawSupplier] ?? rawSupplier : null;

    // Strip empty strings recursively — backend's IntakePayload rejects
    // "" for enum/date fields (supplier, expected_live_date) but accepts
    // null/undefined. Auto-detect mode leaves most fields blank, so the
    // payload would otherwise 400 even with a valid audio file.
    const stripEmpty = <T,>(v: T): T => {
      if (v === "" || v === undefined || v === null) return undefined as unknown as T;
      if (Array.isArray(v)) return v.map(stripEmpty) as unknown as T;
      if (typeof v === "object") {
        const out: Record<string, unknown> = {};
        for (const [k, vv] of Object.entries(v as Record<string, unknown>)) {
          const cleaned = stripEmpty(vv);
          if (cleaned !== undefined) out[k] = cleaned;
        }
        return out as unknown as T;
      }
      return v;
    };
    const cleanedCustomer = stripEmpty(values.customer) as typeof values.customer;
    const cleanedDeal = stripEmpty(values.deal) as typeof values.deal;
    const cleanedCall = stripEmpty(values.call) as typeof values.call;

    // Auto-detect mode: backend's _step_detect_metadata fills in
    // customer.name / deal.supplier / call.call_type from the transcript.
    // Send empty placeholders so the multipart envelope is well-formed
    // without forcing the reviewer to type anything.
    const fd = buildUploadFormData({
      customer: { ...cleanedCustomer, name: cleanedCustomer.name ?? "" },
      deal: { ...cleanedDeal, supplier: (mappedSupplier ?? null) as string },
      call: { ...cleanedCall, call_type: mappedCallType, audio_file: values.call.audio_file },
      customer_slug: customerSlug ?? prefill?.customer?.slug,
      supplier_override: override,
      dev_auto_detect: values.dev_auto_detect,
    });
    upload.mutate(
      { formData: fd },
      {
        onSuccess: (data) => {
          toast.success("Call uploaded, processing");
          setMismatch(null);
          onSuccess?.(data.call_id ?? data.id);
        },
        onError: (err) => {
          if (err instanceof MetadataMismatchError) {
            setMismatch({ field: err.field, manual: err.manual, auto: err.auto });
            return;
          }
          toast.error(err.message || "Upload failed");
        },
      },
    );
  };

  const handleMismatchPick = (choice: MismatchChoice) => {
    if (choice === "edit") {
      // Let the reviewer correct the field manually — clear the banner;
      // they re-submit by clicking Upload again.
      setMismatch(null);
      return;
    }
    // "manual" or "auto" — re-submit immediately with the override flag.
    form.handleSubmit((vals) => onSubmit(vals, choice))();
  };

  // Reset mismatch state when the user changes supplier — they may have
  // already corrected the field; don't keep showing stale banner.
  const supplierWatch = form.watch("deal.supplier");
  useEffect(() => {
    if (mismatch) setMismatch(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supplierWatch]);

  // Auto-detect toggle: clear validation errors and persist preference
  // when the reviewer flips the switch. Errors are scoped to the form-
  // level superRefine that fires only in manual mode, so re-validation
  // wipes them automatically.
  const autoDetect = form.watch("dev_auto_detect");
  useEffect(() => {
    form.clearErrors();
    try {
      localStorage.setItem("l7-auto-detect", autoDetect ? "1" : "0");
    } catch {
      /* noop */
    }
  }, [autoDetect, form]);
  // Restore preference on mount.
  useEffect(() => {
    try {
      const saved = localStorage.getItem("l7-auto-detect");
      if (saved === "1") form.setValue("dev_auto_detect", true);
    } catch {
      /* noop */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fileFieldId = useMemo(() => `l7-audio-${Math.random().toString(36).slice(2, 8)}`, []);

  return (
    <form
      data-slot="l7-form"
      onSubmit={form.handleSubmit(
        (v) => onSubmit(v),
        (errors) => {
          // Surface "why nothing happens when I click Upload" — RHF
          // silently swallows submit when validation fails. Show the
          // first error to the reviewer so they can act on it.
          // eslint-disable-next-line no-console
          console.warn("[L7Form] submit blocked by validation:", errors);
          const flat: string[] = [];
          const walk = (obj: unknown, path: string) => {
            if (!obj || typeof obj !== "object") return;
            for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
              if (v && typeof v === "object" && "message" in v && typeof (v as { message?: unknown }).message === "string") {
                flat.push(`${path ? path + "." : ""}${k}: ${(v as { message: string }).message}`);
              } else if (v && typeof v === "object") {
                walk(v, path ? `${path}.${k}` : k);
              }
            }
          };
          walk(errors, "");
          toast.error(flat[0] ?? "Form validation failed — see console for details");
        },
      )}
      className="flex flex-col gap-4"
      noValidate
    >
      {/* Auto-detect toggle banner — primary control */}
      <Controller
        control={form.control}
        name="dev_auto_detect"
        render={({ field }) => (
          <div
            className={`flex items-center gap-3 rounded-lg border px-4 py-3 ${
              field.value
                ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                : "border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-default)]"
            }`}
          >
            <input
              type="checkbox"
              data-testid="l7-auto-detect-banner"
              id="l7-auto-detect-banner"
              checked={!!field.value}
              onChange={(e) => field.onChange(e.target.checked)}
              className="h-4 w-4"
            />
            <label htmlFor="l7-auto-detect-banner" className="flex flex-1 cursor-pointer flex-col">
              <span className="text-sm font-medium">
                {field.value ? "Auto-detect ON" : "Manual entry"}
              </span>
              <span className="text-[12px] opacity-80">
                {field.value
                  ? "Drop audio + Upload. Customer name, supplier, agent, and call type are detected from the transcript."
                  : "Fill all fields below. Toggle ON to skip and let the AI extract metadata from the audio."}
              </span>
            </label>
          </div>
        )}
      />

      {/* Same-deal sub-toggle — only meaningful when auto-detect is ON.
          Manual mode already lets the reviewer pick a single deal explicitly. */}
      {autoDetect && (
        <label className="flex items-center gap-2 mt-2 ml-6 cursor-pointer">
          <input
            type="checkbox"
            checked={sameDeal}
            onChange={(e) => setSameDeal(e.target.checked)}
            data-testid="l7-same-deal"
          />
          <span className="text-[12px] text-[var(--text-muted)]">
            Same deal — group these files as one deal record
          </span>
        </label>
      )}

      {/* SECTION A — Customer (hidden in auto-detect mode) */}
      <div style={{ display: autoDetect ? "none" : undefined }}>
      <Section letter="A" title="Customer" sub={customerLocked ? "prefilled · read-only" : "8 fields"}>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <FieldRow label="Customer name" error={form.formState.errors.customer?.name?.message}>
            <Input
              data-testid="l7-customer-name"
              disabled={customerLocked}
              {...form.register("customer.name")}
            />
          </FieldRow>
          <FieldRow label="Business type">
            <Controller
              control={form.control}
              name="customer.business_type"
              render={({ field }) => (
                <Select
                  value={field.value ?? undefined}
                  onValueChange={field.onChange}
                  disabled={customerLocked}
                >
                  <SelectTrigger className="h-9 w-full">
                    <SelectValue placeholder="Select…" />
                  </SelectTrigger>
                  <SelectContent>
                    {BUSINESS_TYPES.map((b) => (
                      <SelectItem key={b.value} value={b.value}>
                        {b.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </FieldRow>
          <FieldRow label="MPAN / MPRN">
            <Input disabled={customerLocked} {...form.register("customer.mpan_mprn")} />
          </FieldRow>
          <FieldRow label="Address">
            <Input disabled={customerLocked} {...form.register("customer.address")} />
          </FieldRow>
          <FieldRow label="Primary contact">
            <Input disabled={customerLocked} {...form.register("customer.contact")} />
          </FieldRow>
          <FieldRow label="Email" error={form.formState.errors.customer?.email?.message}>
            <Input type="email" disabled={customerLocked} {...form.register("customer.email")} />
          </FieldRow>
          <FieldRow label="Phone">
            <Input disabled={customerLocked} {...form.register("customer.phone")} />
          </FieldRow>
          <FieldRow label="Notes" full>
            <Input disabled={customerLocked} {...form.register("customer.notes")} />
          </FieldRow>
        </div>
      </Section>
      </div>

      {/* SECTION B — Deal (hidden in auto-detect mode) */}
      <div style={{ display: autoDetect ? "none" : undefined }}>
      <Section letter="B" title="Deal" sub="9 fields">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <FieldRow
            label="Supplier"
            hint={`${SUPPLIERS.length} options · note E.ON ≠ E.ON Next Energy`}
            error={form.formState.errors.deal?.supplier?.message}
          >
            <Controller
              control={form.control}
              name="deal.supplier"
              render={({ field }) => (
                <SupplierCombobox
                  value={field.value}
                  onValueChange={field.onChange}
                  highlight={!!mismatch}
                />
              )}
            />
          </FieldRow>
          <FieldRow label="Contract value (£)">
            <Input
              type="number"
              step="0.01"
              min="0"
              {...form.register("deal.deal_value_gbp", { setValueAs: (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)) })}
            />
          </FieldRow>
          <FieldRow label="Expected live date">
            <Input type="date" {...form.register("deal.expected_live_date")} />
          </FieldRow>
          <FieldRow label="Contract length (months)">
            <Input
              type="number"
              min="0"
              {...form.register("deal.contract_length_months", { setValueAs: (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)) })}
            />
          </FieldRow>
          <FieldRow label="Meters" full hint="MPAN (electricity) and/or MPRN (gas). Add a row for dual-fuel.">
            <div data-slot="l7-meters" className="flex flex-col gap-2">
              {meters.fields.map((field, idx) => {
                const fieldErrors = form.formState.errors.deal?.meters?.[idx];
                return (
                  <div
                    key={field.id}
                    data-slot="l7-meter-row"
                    data-meter-index={idx}
                    className="flex items-start gap-2"
                  >
                    <Input
                      placeholder="MPAN (elec)"
                      data-testid={`l7-meter-${idx}-mpan`}
                      {...form.register(`deal.meters.${idx}.mpan` as const)}
                    />
                    <Input
                      placeholder="MPRN (gas)"
                      data-testid={`l7-meter-${idx}-mprn`}
                      {...form.register(`deal.meters.${idx}.mprn` as const)}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Remove meter"
                      data-testid={`l7-meter-${idx}-remove`}
                      onClick={() => meters.remove(idx)}
                      disabled={meters.fields.length <= 1}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                    {fieldErrors && (
                      <p className="mt-1 text-[11px] text-red-400">
                        {(fieldErrors as { message?: string })?.message ?? "Each meter needs an MPAN or MPRN"}
                      </p>
                    )}
                  </div>
                );
              })}
              <button
                type="button"
                data-testid="l7-meter-add"
                onClick={() => meters.append({ mpan: "", mprn: "" })}
                className="inline-flex w-fit items-center gap-1.5 rounded-md border border-dashed border-[var(--border-strong)] bg-transparent px-2.5 py-1 text-[12px] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)] hover:text-[var(--text-primary)]"
              >
                <Plus className="h-3 w-3" />
                Add meter
              </button>
            </div>
          </FieldRow>
          <FieldRow label="Watt site ID" hint="Watt portal site_id (deep-link integer)">
            <Input
              type="number"
              min="0"
              data-testid="l7-external-watt-site-id"
              {...form.register("deal.external_watt_site_id", { setValueAs: (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)) })}
            />
          </FieldRow>
          <FieldRow label="Status">
            <Controller
              control={form.control}
              name="deal.status"
              render={({ field }) => (
                <Select value={field.value ?? undefined} onValueChange={field.onChange}>
                  <SelectTrigger className="h-9 w-full">
                    <SelectValue placeholder="Select…" />
                  </SelectTrigger>
                  <SelectContent>
                    {DEAL_STATUSES.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </FieldRow>
          <FieldRow label="Broker">
            <Input {...form.register("deal.broker")} />
          </FieldRow>
          <FieldRow label="Agent name">
            <Input {...form.register("deal.agent_name")} />
          </FieldRow>
          <FieldRow label="Notes" full>
            <Textarea
              rows={2}
              placeholder="Internal notes for this deal…"
              {...form.register("deal.notes")}
            />
          </FieldRow>
        </div>
      </Section>
      </div>

      {/* SECTION C — Call */}
      <Section letter="C" title="Call" sub={autoDetect ? "audio file only" : "5 fields"}>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div style={{ display: autoDetect ? "none" : "contents" }}>
          <FieldRow label="Call type" error={form.formState.errors.call?.call_type?.message}>
            <Controller
              control={form.control}
              name="call.call_type"
              render={({ field }) => (
                <Select value={field.value ?? undefined} onValueChange={field.onChange}>
                  <SelectTrigger data-testid="l7-call-type" className="h-9 w-full">
                    <SelectValue placeholder="Select call type…" />
                  </SelectTrigger>
                  <SelectContent>
                    {CALL_TYPES.map((c) => (
                      <SelectItem key={c.value} value={c.value}>
                        {c.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </FieldRow>
          <FieldRow label="Recording date">
            <Input type="datetime-local" {...form.register("call.recording_date")} />
          </FieldRow>
          </div>
          <FieldRow label="Audio file" full error={form.formState.errors.call?.audio_file?.message}>
            <Controller
              control={form.control}
              name="call.audio_file"
              render={({ field: { onChange, value } }) => (
                <label
                  htmlFor={fileFieldId}
                  onDragOver={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                  }}
                  onDragEnter={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const all = Array.from(e.dataTransfer.files ?? []);
                    if (all.length === 0) return;
                    // Auto-detect mode: any drop (1 or many) fires the
                    // no-metadata upload immediately. Single-file path used
                    // to set the RHF field and wait for the Upload click,
                    // which silently 422'd when the user hit Upload first.
                    // Now both paths behave the same — drop = upload starts,
                    // navigate to /calls/{id} on success.
                    if (autoDetect) {
                      fireBatchUpload(all);
                      return;
                    }
                    const f = all[0];
                    if (!/\.(mp3|wav|m4a)$/i.test(f.name) && !f.type.startsWith("audio/")) {
                      toast.error("Only MP3, WAV, or M4A audio files are accepted.");
                      return;
                    }
                    onChange(f);
                  }}
                  className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed border-[var(--border-strong)] bg-[var(--bg-canvas)] px-4 py-5 text-center hover:bg-[var(--bg-elev2)]"
                >
                  {batchUploads.length > 0 ? (
                    <div className="flex w-full flex-col gap-1">
                      <span className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                        Batch upload · {batchUploads.filter((u) => u.status === "done").length}/
                        {batchUploads.length} done
                      </span>
                      {batchUploads.map((u, i) => (
                        <div
                          key={i}
                          className="flex items-center gap-2 rounded border border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-2 py-1 text-[12px]"
                        >
                          <span className="font-mono flex-1 truncate text-left">{u.name}</span>
                          <span className="text-[10px] text-[var(--text-muted)]">
                            {Math.round((u.size / 1024 / 1024) * 10) / 10} MB
                          </span>
                          {u.status === "pending" && (
                            <span className="text-[10px] text-[var(--text-dim)]">queued</span>
                          )}
                          {u.status === "uploading" && (
                            <span className="text-[10px] text-amber-400">uploading…</span>
                          )}
                          {u.status === "done" && u.callId && (
                            <a
                              href={`/calls/${u.callId}`}
                              target="_blank"
                              rel="noreferrer"
                              className="text-[10px] text-emerald-400 underline"
                            >
                              ✓ {u.callId.slice(0, 8)}
                            </a>
                          )}
                          {u.status === "error" && (
                            <span
                              className="text-[10px] text-red-400"
                              title={u.error}
                            >
                              ✗ {u.error?.slice(0, 30) ?? "failed"}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : value ? (
                    <span className="flex items-center gap-2 text-[13px]">
                      <FileAudio2 className="h-4 w-4 text-emerald-400" />
                      <span className="font-mono">{(value as File).name}</span>
                      <span className="text-[var(--text-muted)]">
                        · {Math.round(((value as File).size / 1024 / 1024) * 10) / 10} MB
                      </span>
                    </span>
                  ) : (
                    <>
                      <span className="text-[13px] text-[var(--text-primary)]">
                        {autoDetect
                          ? "Drop one or more audio files (auto-detect runs per file)"
                          : "Drop audio file here or click to browse"}
                      </span>
                      <span className="text-[11px] text-[var(--text-dim)]">
                        MP3, WAV, M4A · up to 200 MB
                      </span>
                    </>
                  )}
                  <input
                    id={fileFieldId}
                    data-testid="l7-audio-file"
                    type="file"
                    accept="audio/*,.mp3,.wav,.m4a"
                    multiple={autoDetect}
                    className="hidden"
                    onChange={(e) => {
                      const all = Array.from(e.target.files ?? []);
                      if (all.length === 0) return;
                      // Same logic as onDrop: auto-detect = any count fires
                      // the no-metadata batch upload immediately.
                      if (autoDetect) {
                        fireBatchUpload(all);
                        return;
                      }
                      onChange(all[0]);
                    }}
                  />
                </label>
              )}
            />
          </FieldRow>
          <div style={{ display: autoDetect ? "none" : "contents" }}>
          <FieldRow label="Duration (seconds)">
            <Input
              type="number"
              min="0"
              {...form.register("call.duration_seconds", { setValueAs: (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)) })}
            />
          </FieldRow>
          <FieldRow label="Language">
            <Controller
              control={form.control}
              name="call.language"
              render={({ field }) => (
                <Select value={field.value ?? undefined} onValueChange={field.onChange}>
                  <SelectTrigger className="h-9 w-full">
                    <SelectValue placeholder="Select…" />
                  </SelectTrigger>
                  <SelectContent>
                    {LANGUAGES.map((l) => (
                      <SelectItem key={l.value} value={l.value}>
                        {l.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </FieldRow>
          </div>
        </div>
      </Section>

      {/* Mismatch banner — shown above footer when pipeline rejects manual */}
      {mismatch && (
        <MetadataMismatchBanner
          field={mismatch.field}
          manual={mismatch.manual}
          auto={mismatch.auto}
          onPick={handleMismatchPick}
        />
      )}

      {/* Footer */}
      <div className="flex items-center gap-3 border-t border-[var(--border-subtle)] pt-3">
        {showDevToggle && (
          <Controller
            control={form.control}
            name="dev_auto_detect"
            render={({ field }) => (
              <label className="flex cursor-pointer items-center gap-2 text-[12px] text-[var(--text-muted)]">
                <input
                  type="checkbox"
                  data-testid="l7-dev-auto-detect"
                  checked={!!field.value}
                  onChange={(e) => field.onChange(e.target.checked)}
                />
                <span>
                  <span className="font-mono">dev_auto_detect</span>
                  <span className="ml-2 text-[11px] text-[var(--text-dim)]">
                    Cross-check metadata from audio
                  </span>
                </span>
              </label>
            )}
          />
        )}
        <div className="flex-1" />
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            {batchUploads.length > 0 ? "Close" : "Cancel"}
          </Button>
        )}
        {/* Hide the form's Upload button while a batch upload is in flight or
            already done — each file already POSTed itself via fireBatchUpload,
            and clicking Upload would only fire RHF validation against the now-
            empty audio_file slot, surfacing a confusing "Audio file required"
            toast right next to the green ✓ rows. */}
        {batchUploads.length === 0 && (
          <Button type="submit" disabled={upload.isPending} data-testid="l7-submit">
            <UploadIcon className="mr-1.5 h-4 w-4" />
            {upload.isPending ? "Uploading…" : "Upload"}
          </Button>
        )}
      </div>
    </form>
  );
}

// ── Section + Field row helpers ───────────────────────────────────
function Section({
  letter,
  title,
  sub,
  children,
}: {
  letter: string;
  title: string;
  sub?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      data-slot="l7-section"
      data-section={letter}
      className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4"
    >
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-md border border-[var(--border-strong)] bg-[var(--bg-elev2)] text-[11px] font-semibold">
          {letter}
        </span>
        <span className="text-[14px] font-semibold">{title}</span>
        {sub && <span className="text-[12px] text-[var(--text-dim)]">{sub}</span>}
      </div>
      {children}
    </section>
  );
}

function FieldRow({
  label,
  hint,
  error,
  full,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  full?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={full ? "md:col-span-2" : undefined}>
      <Label className="mb-1 block text-[12px] text-[var(--text-muted)]">{label}</Label>
      {children}
      {hint && !error && <p className="mt-1 text-[11px] text-[var(--text-dim)]">{hint}</p>}
      {error && <p className="mt-1 text-[11px] text-red-400">{error}</p>}
    </div>
  );
}
