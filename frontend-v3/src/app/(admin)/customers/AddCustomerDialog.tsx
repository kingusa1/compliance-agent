"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useAddCustomer } from "@/lib/mutations/admin";

/**
 * AddCustomerDialog — 8-field RHF/zod form. POST /api/customers.
 *
 *   legal_name (req)
 *   trading_as
 *   business_type
 *   address_postcode
 *   company_number
 *   charity_number
 *   contact_email
 *   vulnerable_customer_flag (checkbox)
 */
const schema = z.object({
  legal_name: z.string().min(1, "Legal name is required"),
  trading_as: z.string().optional(),
  business_type: z.string().optional(),
  address_postcode: z.string().optional(),
  company_number: z.string().optional(),
  charity_number: z.string().optional(),
  contact_email: z
    .string()
    .email("Must be a valid email")
    .optional()
    .or(z.literal("")),
  vulnerable_customer_flag: z.boolean(),
});
type FormValues = z.infer<typeof schema>;

export type AddCustomerDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (slug: string) => void;
};

export function AddCustomerDialog({ open, onOpenChange, onCreated }: AddCustomerDialogProps) {
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      legal_name: "",
      trading_as: "",
      business_type: "",
      address_postcode: "",
      company_number: "",
      charity_number: "",
      contact_email: "",
      vulnerable_customer_flag: false,
    },
  });

  const add = useAddCustomer();

  useEffect(() => {
    if (!open) form.reset();
  }, [open, form]);

  async function onSubmit(values: FormValues) {
    // Strip empty optional strings so backend gets nulls for unset fields.
    const payload: Record<string, unknown> = { ...values };
    for (const k of Object.keys(payload)) {
      if (payload[k] === "") delete payload[k];
    }
    const res = await add.mutateAsync(
      payload as Parameters<typeof add.mutateAsync>[0],
    );
    onOpenChange(false);
    onCreated?.(res.slug);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="!max-w-xl !w-[min(95vw,560px)]"
        data-testid="add-customer-dialog"
      >
        <DialogHeader>
          <DialogTitle>Add customer</DialogTitle>
          <DialogDescription>
            Create a customer record. You can attach calls and deals afterwards.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="grid grid-cols-1 gap-4 py-2 sm:grid-cols-2"
          >
            <FormField
              control={form.control}
              name="legal_name"
              render={({ field }) => (
                <FormItem className="sm:col-span-2">
                  <FormLabel>Legal name</FormLabel>
                  <FormControl>
                    <Input placeholder="Patel Convenience Stores Ltd" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="trading_as"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Trading as</FormLabel>
                  <FormControl>
                    <Input placeholder="Patel Stores" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="business_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Business type</FormLabel>
                  <FormControl>
                    {/* 2026-05-14 audit fix: backend customers_routes
                        accepts only the 4 Literal values below — a
                        free-text "Retail" input was returning 422 on
                        every submit. Forced to a <select> tied to the
                        canonical enum. */}
                    <select
                      {...field}
                      value={field.value ?? ""}
                      className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors"
                    >
                      <option value="">—</option>
                      <option value="sole_trader">Sole trader</option>
                      <option value="limited">Limited</option>
                      <option value="partnership">Partnership</option>
                      <option value="charity">Charity</option>
                    </select>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="address_postcode"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Postcode</FormLabel>
                  <FormControl>
                    <Input placeholder="SW1A 1AA" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="company_number"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Company number</FormLabel>
                  <FormControl>
                    <Input placeholder="12345678" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="charity_number"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Charity number</FormLabel>
                  <FormControl>
                    <Input placeholder="optional" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="contact_email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Contact email</FormLabel>
                  <FormControl>
                    <Input type="email" placeholder="ops@example.co.uk" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="vulnerable_customer_flag"
              render={({ field }) => (
                <FormItem className="sm:col-span-2">
                  <label className="flex cursor-pointer items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-3 py-2.5 text-[13px]">
                    <input
                      type="checkbox"
                      checked={field.value}
                      onChange={(e) => field.onChange(e.target.checked)}
                    />
                    <span>Vulnerable customer flag</span>
                  </label>
                </FormItem>
              )}
            />
            <DialogFooter className="sm:col-span-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={add.isPending}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={add.isPending}
                data-testid="add-customer-submit"
              >
                {add.isPending ? "Adding…" : "Add customer"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
