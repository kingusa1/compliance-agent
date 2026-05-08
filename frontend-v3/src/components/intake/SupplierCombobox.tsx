"use client";

import * as React from "react";

import { SUPPLIERS, type Supplier } from "@/lib/schemas/l7-intake";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * SupplierCombobox — exactly 14 suppliers in the locked W1 order
 * (most-used → least-used per Watt rejection-tracker frequency analysis).
 *
 * E.ON and "E.ON Next Energy" are DISTINCT canonical keys (different LOA
 * models); do not collapse them. The unit test in
 * `tests/unit/SupplierCombobox.test.tsx` enforces this — both must render
 * as separate selectable options.
 *
 * Built on the shadcn Select primitive (base-ui) rather than Combobox
 * because the list is fixed-size and we don't need free-text search.
 */
export interface SupplierComboboxProps {
  value?: Supplier | "";
  onValueChange?: (value: Supplier) => void;
  disabled?: boolean;
  placeholder?: string;
  highlight?: boolean; // amber border when reconciling METADATA_MISMATCH
  id?: string;
}

export function SupplierCombobox({
  value,
  onValueChange,
  disabled,
  placeholder = "Select supplier…",
  highlight,
  id,
}: SupplierComboboxProps) {
  return (
    <Select
      value={value ?? undefined}
      onValueChange={(v) => onValueChange?.((v ?? "") as Supplier)}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        data-slot="supplier-combobox-trigger"
        data-highlight={highlight ? "true" : undefined}
        className={
          "h-9 w-full" +
          (highlight ? " border-amber-500/45 ring-2 ring-amber-500/20" : "")
        }
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent data-slot="supplier-combobox-content">
        {SUPPLIERS.map((s) => (
          <SelectItem key={s} value={s} data-supplier={s}>
            {s}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// Re-export SUPPLIERS for tests + AddCustomer dialog.
export { SUPPLIERS } from "@/lib/schemas/l7-intake";
