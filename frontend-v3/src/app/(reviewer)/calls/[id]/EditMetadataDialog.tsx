"use client";
import { useMemo, useState } from "react";
import { useEditCallMetadata, type EditCallMetadataPayload } from "@/lib/mutations/admin";

type CallSeed = {
  id: string;
  customer_name?: string | null;
  agent_name?: string | null;
};

type DealSeed = {
  customer_name?: string | null;
  supplier?: string | null;
  mpan_or_mprn?: string | null;
  expected_live_date?: string | null;
  deal_value_gbp?: number | null;
  term_months?: number | null;
  notes?: string | null;
};

const SUPPLIERS = [
  "E.ON Next Energy", "British Gas Lite", "British Gas Business",
  "British Gas Trading", "British Gas Core", "Pozitive Energy",
  "Yu Energy", "Smartest Energy", "Affect Energy", "Britannia Gas",
  "United Gas & Power", "E.ON", "TotalEnergies", "Other",
];

export function EditMetadataDialog({
  call, deal, open, onClose,
}: {
  call: CallSeed;
  deal: DealSeed | null;
  open: boolean;
  onClose: () => void;
}) {
  const m = useEditCallMetadata(call.id);

  // Prefer the deal-resolved canonical customer name over the call-row
  // value when both exist. The call row sometimes carries a truncated
  // first-token-only customer ("Awais") while the deal canonical is the
  // full business name ("Awais Mustafa Ta Charles Palace"). Pre-filling
  // from the deal canonical prevents the modal from inviting an
  // accidental no-op save that silently shortens the customer record.
  // See BRAIN 2026-05-16 audit P0 #5.
  const seededCustomerName = (deal?.customer_name?.trim() || call.customer_name?.trim() || "") as string;

  // Original seed — anchor for "did the user change this field?".
  const seed = useMemo<EditCallMetadataPayload>(
    () => ({
      customer_name: seededCustomerName,
      agent_name: call.agent_name ?? "",
      supplier: deal?.supplier ?? "",
      mpan_or_mprn: deal?.mpan_or_mprn ?? "",
      expected_live_date: deal?.expected_live_date ?? "",
      deal_value_gbp: deal?.deal_value_gbp ?? undefined,
      contract_length_months: deal?.term_months ?? undefined,
      notes: deal?.notes ?? "",
    }),
    // seed is frozen at first render — re-opening the modal remounts the
    // component (gated by `open` prop below) which rebuilds the seed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [call.id],
  );
  const [form, setForm] = useState<EditCallMetadataPayload>(seed);

  if (!open) return null;

  // Heuristic guard: if the user is about to save a customer_name that
  // is a strict prefix of the existing canonical (e.g. "Awais" replacing
  // "Awais Mustafa Ta Charles Palace") AND they didn't actively edit it,
  // surface a confirm so a careless Save can't truncate a deal record.
  const customerNameWouldShrink =
    !!form.customer_name?.trim() &&
    !!seededCustomerName &&
    form.customer_name.trim() === seededCustomerName &&
    seededCustomerName.split(/\s+/).length === 1 &&
    !!deal?.customer_name?.trim() &&
    deal.customer_name.trim().length > seededCustomerName.length;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    // CHANGED-FIELDS-ONLY payload. Only send fields the user actively
    // touched (current value ≠ seed). This prevents the modal from
    // silently re-writing every field on Save when the user only wanted
    // to change one. Previously a no-op Save would re-stamp call.customer_name
    // with whatever was in the pre-fill (and could shorten a name).
    const payload: EditCallMetadataPayload = {};
    const txt = (v: string | null | undefined) => (v ?? "").trim();

    if (txt(form.customer_name) !== txt(seed.customer_name)) {
      const v = txt(form.customer_name);
      if (v) payload.customer_name = v;
    }
    if (txt(form.agent_name) !== txt(seed.agent_name)) {
      const v = txt(form.agent_name);
      if (v) payload.agent_name = v;
    }
    if (txt(form.supplier) !== txt(seed.supplier)) {
      const v = txt(form.supplier);
      if (v) payload.supplier = v;
    }
    if (txt(form.mpan_or_mprn) !== txt(seed.mpan_or_mprn)) {
      const v = txt(form.mpan_or_mprn);
      if (v) payload.mpan_or_mprn = v;
    }
    if (txt(form.expected_live_date) !== txt(seed.expected_live_date)) {
      const v = txt(form.expected_live_date);
      if (v) payload.expected_live_date = v;
    }
    if (form.deal_value_gbp !== seed.deal_value_gbp) {
      if (typeof form.deal_value_gbp === "number" && !Number.isNaN(form.deal_value_gbp)) {
        payload.deal_value_gbp = form.deal_value_gbp;
      }
    }
    if (form.contract_length_months !== seed.contract_length_months) {
      if (typeof form.contract_length_months === "number" && !Number.isNaN(form.contract_length_months)) {
        payload.contract_length_months = form.contract_length_months;
      }
    }
    if (txt(form.notes) !== txt(seed.notes)) {
      const v = txt(form.notes);
      if (v) payload.notes = v;
    }

    // Nothing changed → close without firing the mutation.
    if (Object.keys(payload).length === 0) {
      onClose();
      return;
    }
    m.mutate(payload, { onSuccess: () => onClose() });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="relative w-full max-w-2xl rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Edit metadata</h2>
            <p className="text-[12px] text-[var(--text-muted)]">
              Override anything auto-detect missed. Saves to call, deal, and customer rows.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-[var(--text-muted)] hover:text-[var(--text-default)]">×</button>
        </header>

        <form onSubmit={submit} className="grid grid-cols-2 gap-3">
          <label className="col-span-2 flex flex-col gap-1 text-[12px]">
            Customer name
            <input
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.customer_name ?? ""}
              onChange={(e) => setForm({ ...form, customer_name: e.target.value })}
              placeholder="Type the canonical business name…"
            />
            {customerNameWouldShrink && (
              <p className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] text-amber-900">
                Heads up: the deal canonical is{" "}
                <strong>{deal?.customer_name}</strong> — saving without editing
                will keep the shorter value <strong>{seededCustomerName}</strong> on
                the call row. Type the full name if you want them to agree.
              </p>
            )}
          </label>

          <label className="flex flex-col gap-1 text-[12px]">
            Sales agent
            <input
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.agent_name ?? ""}
              onChange={(e) => setForm({ ...form, agent_name: e.target.value })}
              placeholder="Type to override auto-detected agent…"
            />
          </label>

          <label className="flex flex-col gap-1 text-[12px]">
            Supplier
            <select
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.supplier ?? ""}
              onChange={(e) => setForm({ ...form, supplier: e.target.value })}
            >
              <option value="">—</option>
              {SUPPLIERS.map((s) => (<option key={s} value={s}>{s}</option>))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-[12px]">
            MPAN / MPRN
            <input
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px] font-mono"
              value={form.mpan_or_mprn ?? ""}
              onChange={(e) => setForm({ ...form, mpan_or_mprn: e.target.value })}
              placeholder="1234567890"
            />
          </label>

          <label className="flex flex-col gap-1 text-[12px]">
            Expected live date
            <input
              type="date"
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.expected_live_date ?? ""}
              onChange={(e) => setForm({ ...form, expected_live_date: e.target.value })}
            />
          </label>

          <label className="flex flex-col gap-1 text-[12px]">
            Deal value (£)
            <input
              type="number"
              step="100"
              min="0"
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.deal_value_gbp ?? ""}
              onChange={(e) => setForm({ ...form, deal_value_gbp: e.target.value === "" ? undefined : Number(e.target.value) })}
              placeholder="42000"
            />
          </label>

          <label className="flex flex-col gap-1 text-[12px]">
            Contract length (months)
            <input
              type="number"
              step="1"
              min="0"
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.contract_length_months ?? ""}
              onChange={(e) => setForm({ ...form, contract_length_months: e.target.value === "" ? undefined : Number(e.target.value) })}
              placeholder="36"
            />
          </label>

          <label className="col-span-2 flex flex-col gap-1 text-[12px]">
            Notes
            <textarea
              rows={3}
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.notes ?? ""}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="Any context the reviewer wants to keep with this deal."
            />
          </label>

          {m.error && (
            <p className="col-span-2 rounded border border-red-300 bg-red-50 p-2 text-[12px] text-red-800">
              {m.error.message}
            </p>
          )}

          <div className="col-span-2 mt-2 flex items-center justify-end gap-2 border-t border-[var(--border-subtle)] pt-3">
            <button type="button" onClick={onClose} className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-1.5 text-[12px] hover:bg-[var(--bg-elev2)]">
              Cancel
            </button>
            <button type="submit" disabled={m.isPending} className="rounded-md bg-emerald-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-emerald-700 disabled:opacity-60">
              {m.isPending ? "Saving…" : "Save metadata"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
