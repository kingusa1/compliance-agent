"use client";

/**
 * AddRejectionDialog — admin-only manual create flow. Most rejections enter
 * the system via auto-create on FAIL/REVIEW verdict (see useSubmitVerdict in
 * lib/mutations/reviewer.ts); this dialog covers the back-office case where
 * Watt's ops team types a rejection in directly off a supplier email.
 *
 * Plain controlled state (not RHF) — fewer fields than the customer form
 * and the zod schema's optional-field union types disagree with RHF's
 * stricter resolver in this codebase's TS setup.
 */
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useCreateRejection } from "@/lib/mutations/rejections";
import {
  REJECTION_CATEGORIES,
  REJECTION_CATEGORY_LABELS,
  REMEDIATION_ACTIONS,
  REMEDIATION_ACTION_LABELS,
  type RejectionCategory,
  type RemediationAction,
} from "@/lib/schemas/rejections";

export type AddRejectionDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (id: string) => void;
};

export function AddRejectionDialog({
  open,
  onOpenChange,
  onCreated,
}: AddRejectionDialogProps) {
  const [category, setCategory] = useState<RejectionCategory>("ADMIN_ERROR");
  const [reason, setReason] = useState("");
  const [customerSlug, setCustomerSlug] = useState("");
  const [siteId, setSiteId] = useState("");
  const [supplier, setSupplier] = useState("");
  const [salesAgent, setSalesAgent] = useState("");
  const [fixRequired, setFixRequired] = useState<RemediationAction | "">("");
  const [reasonError, setReasonError] = useState<string | null>(null);

  const create = useCreateRejection();

  useEffect(() => {
    if (open) return;
    setCategory("ADMIN_ERROR");
    setReason("");
    setCustomerSlug("");
    setSiteId("");
    setSupplier("");
    setSalesAgent("");
    setFixRequired("");
    setReasonError(null);
  }, [open]);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!reason.trim()) {
      setReasonError("Reason is required");
      return;
    }
    setReasonError(null);
    const payload: Record<string, unknown> = {
      category,
      rejection_reason: reason.trim(),
    };
    if (customerSlug.trim()) payload.customer_slug = customerSlug.trim();
    if (siteId.trim() && /^\d+$/.test(siteId.trim())) {
      payload.external_watt_site_id = Number(siteId.trim());
    }
    if (supplier.trim()) payload.supplier = supplier.trim();
    if (salesAgent.trim()) payload.sales_agent = salesAgent.trim();
    if (fixRequired) payload.fix_required = fixRequired;

    const res = (await create.mutateAsync(payload as never)) as { id?: string } | null;
    onOpenChange(false);
    if (res?.id) onCreated?.(res.id);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="!max-w-xl !w-[min(95vw,640px)]"
        data-testid="add-rejection-dialog"
      >
        <DialogHeader>
          <DialogTitle>Add rejection</DialogTitle>
          <DialogDescription>
            Manual entry — most rejections are auto-created when a verdict
            lands on FAIL or REVIEW.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={onSubmit}
          className="grid grid-cols-1 gap-4 py-2 sm:grid-cols-2"
        >
          <div className="sm:col-span-2 flex flex-col gap-1.5">
            <label className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-dim)]">
              Category *
            </label>
            <Select
              value={category}
              onValueChange={(v) => setCategory(v as RejectionCategory)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick category…" />
              </SelectTrigger>
              <SelectContent>
                {REJECTION_CATEGORIES.map((c) => (
                  <SelectItem key={c} value={c}>
                    {REJECTION_CATEGORY_LABELS[c]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="sm:col-span-2 flex flex-col gap-1.5">
            <label className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-dim)]">
              Rejection reason *
            </label>
            <Textarea
              rows={3}
              placeholder="What did the supplier reject and why?"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            {reasonError && (
              <p className="text-xs text-red-500">{reasonError}</p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-dim)]">
              Customer slug
            </label>
            <Input
              placeholder="auditfix-ltd"
              value={customerSlug}
              onChange={(e) => setCustomerSlug(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-dim)]">
              Watt site id
            </label>
            <Input
              placeholder="4271"
              type="number"
              value={siteId}
              onChange={(e) => setSiteId(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-dim)]">
              Supplier
            </label>
            <Input
              placeholder="E.ON Next Energy"
              value={supplier}
              onChange={(e) => setSupplier(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-dim)]">
              Sales agent
            </label>
            <Input
              placeholder="Sammie"
              value={salesAgent}
              onChange={(e) => setSalesAgent(e.target.value)}
            />
          </div>

          <div className="sm:col-span-2 flex flex-col gap-1.5">
            <label className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-dim)]">
              Fix required (optional)
            </label>
            <Select
              value={fixRequired || undefined}
              onValueChange={(v) => setFixRequired(v as RemediationAction)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick action…" />
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

          <DialogFooter className="sm:col-span-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={create.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={create.isPending}
              data-testid="add-rejection-submit"
            >
              {create.isPending ? "Adding…" : "Add rejection"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
