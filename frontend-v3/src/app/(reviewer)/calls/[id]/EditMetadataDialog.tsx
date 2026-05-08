"use client";
import { useState } from "react";
import { useEditCallMetadata, type EditCallMetadataPayload } from "@/lib/mutations/admin";

type CallSeed = {
  id: string;
  customer_name?: string | null;
  agent_name?: string | null;
};

type DealSeed = {
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
  const [form, setForm] = useState<EditCallMetadataPayload>({
    customer_name: call.customer_name ?? "",
    agent_name: call.agent_name ?? "",
    supplier: deal?.supplier ?? "",
    mpan_or_mprn: deal?.mpan_or_mprn ?? "",
    expected_live_date: deal?.expected_live_date ?? "",
    deal_value_gbp: deal?.deal_value_gbp ?? undefined,
    contract_length_months: deal?.term_months ?? undefined,
    notes: deal?.notes ?? "",
  });

  if (!open) return null;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    // Strip empty strings + NaN
    const payload: EditCallMetadataPayload = {};
    if (form.customer_name?.trim()) payload.customer_name = form.customer_name.trim();
    if (form.agent_name?.trim()) payload.agent_name = form.agent_name.trim();
    if (form.supplier?.trim()) payload.supplier = form.supplier.trim();
    if (form.mpan_or_mprn?.trim()) payload.mpan_or_mprn = form.mpan_or_mprn.trim();
    if (form.expected_live_date?.trim()) payload.expected_live_date = form.expected_live_date.trim();
    if (typeof form.deal_value_gbp === "number" && !Number.isNaN(form.deal_value_gbp)) {
      payload.deal_value_gbp = form.deal_value_gbp;
    }
    if (typeof form.contract_length_months === "number" && !Number.isNaN(form.contract_length_months)) {
      payload.contract_length_months = form.contract_length_months;
    }
    if (form.notes?.trim()) payload.notes = form.notes.trim();
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
              placeholder="Acme Industrial Ltd"
            />
          </label>

          <label className="flex flex-col gap-1 text-[12px]">
            Sales agent
            <input
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1.5 text-[13px]"
              value={form.agent_name ?? ""}
              onChange={(e) => setForm({ ...form, agent_name: e.target.value })}
              placeholder="Sammy R."
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
