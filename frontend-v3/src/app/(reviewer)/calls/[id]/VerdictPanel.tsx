"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  GraduationCap,
  XCircle,
} from "lucide-react";
import type { LucideProps } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Textarea } from "@/components/ui/textarea";
import { useSubmitVerdict, useFeedbackEmail, type VerdictAction } from "@/lib/mutations/reviewer";

/**
 * VerdictPanel — UX-D15 chosen pattern: 5 colored buttons with icons in a
 * row, RHF-controlled reason textarea (zod min(10) chars), auto-email
 * toggle, Submit.
 *
 * Action → semantic tone mapping (matches tokens.css):
 *   PASS     → emerald  (CheckCircle2)
 *   REVIEW   → amber    (AlertTriangle)
 *   COACHING → blue     (GraduationCap)
 *   FAIL     → red      (XCircle)
 *   BLOCK    → violet   (Ban)
 *
 * On submit:
 *   1. POST /api/calls/{id}/verdict
 *   2. If sendEmail is true and we have an agent email, also POST
 *      /api/calls/{id}/feedback-email with a stub markdown body
 *   3. Optional `onSubmitted` callback so the parent can close a tab /
 *      switch to "committed" view
 */
export const verdictSchema = z.object({
  action: z.enum(["PASS", "REVIEW", "COACHING", "FAIL", "BLOCK"]),
  reason: z.string().min(10, "Reason must be at least 10 characters"),
  sendEmail: z.boolean(),
});
export type VerdictFormValues = z.infer<typeof verdictSchema>;

type ActionDef = {
  key: VerdictAction;
  label: string;
  desc: string;
  Icon: React.ComponentType<LucideProps>;
  /** css var name for the action's semantic colour */
  color: string;
};

const ACTIONS: ActionDef[] = [
  {
    key: "PASS",
    label: "PASS",
    desc: "Fully compliant — no further action",
    Icon: CheckCircle2,
    color: "var(--emerald-pass)",
  },
  {
    key: "REVIEW",
    label: "REVIEW",
    desc: "Minor issues — note for trends",
    Icon: AlertTriangle,
    color: "var(--amber-review)",
  },
  {
    key: "COACHING",
    label: "COACHING",
    desc: "Send agent feedback for training",
    Icon: GraduationCap,
    color: "var(--blue-coaching)",
  },
  {
    key: "FAIL",
    label: "FAIL",
    desc: "Material non-compliance",
    Icon: XCircle,
    color: "var(--red-fail)",
  },
  {
    key: "BLOCK",
    label: "BLOCK",
    desc: "Contract must be voided",
    Icon: Ban,
    color: "var(--violet-block)",
  },
];

export function VerdictPanel({
  callId,
  agentEmail,
  filename,
  onSubmitted,
}: {
  callId: string;
  agentEmail?: string | null;
  filename?: string | null;
  onSubmitted?: (action: VerdictAction) => void;
}) {
  const submitVerdict = useSubmitVerdict();
  const feedbackEmail = useFeedbackEmail();

  const form = useForm<VerdictFormValues>({
    resolver: zodResolver(verdictSchema),
    defaultValues: {
      action: "PASS" as VerdictAction,
      reason: "",
      sendEmail: !!agentEmail,
    },
  });

  // Reset email-toggle default when we learn the agent has no email.
  useEffect(() => {
    if (!agentEmail) form.setValue("sendEmail", false);
  }, [agentEmail, form]);

  const action = form.watch("action");
  const sendEmail = form.watch("sendEmail");
  const chosen = ACTIONS.find((a) => a.key === action) ?? ACTIONS[0];

  async function onSubmit(values: VerdictFormValues) {
    await submitVerdict.mutateAsync({
      callId,
      action: values.action,
      reason: values.reason,
    });
    if (values.sendEmail && agentEmail) {
      const subject = `Compliance review · ${filename ?? "call"} · ${values.action}`;
      try {
        await feedbackEmail.mutateAsync({
          callId,
          to_addr: agentEmail,
          subject,
          body_markdown: values.reason,
        });
      } catch {
        // toast already fired
      }
    }
    onSubmitted?.(values.action);
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        className="flex flex-col gap-4 p-5"
        data-testid="verdict-panel"
      >
        <FormField
          control={form.control}
          name="action"
          render={({ field }) => (
            <FormItem>
              <FormLabel className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                Verdict
              </FormLabel>
              <FormControl>
                <div className="grid grid-cols-5 gap-1.5" role="radiogroup" aria-label="Verdict">
                  {ACTIONS.map((a) => {
                    const isChosen = field.value === a.key;
                    return (
                      <button
                        key={a.key}
                        type="button"
                        role="radio"
                        aria-checked={isChosen}
                        data-testid={`verdict-action-${a.key}`}
                        onClick={() => field.onChange(a.key)}
                        className="flex flex-col items-center gap-1 rounded-md border px-2 py-2.5 text-[11px] font-semibold tracking-wider transition-colors"
                        style={{
                          background: isChosen
                            ? a.color
                            : `color-mix(in oklab, ${a.color} 6%, transparent)`,
                          borderColor: isChosen
                            ? a.color
                            : `color-mix(in oklab, ${a.color} 30%, transparent)`,
                          color: isChosen ? "#0a0a0b" : a.color,
                        }}
                      >
                        <a.Icon className="h-3.5 w-3.5" />
                        {a.label}
                      </button>
                    );
                  })}
                </div>
              </FormControl>
              <p className="text-[12px] text-[var(--text-muted)]">{chosen.desc}</p>
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="reason"
          render={({ field }) => (
            <FormItem>
              <FormLabel className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                Reason
              </FormLabel>
              <FormControl>
                <Textarea
                  {...field}
                  placeholder="Add a short reason for your verdict (min 10 chars)…"
                  className="min-h-[110px] text-[13px]"
                  data-testid="verdict-reason"
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="sendEmail"
          render={({ field }) => (
            <FormItem>
              <label className="flex cursor-pointer items-center gap-3 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-3 py-2.5">
                <FormControl>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={field.value}
                    aria-label="Send feedback email"
                    data-testid="verdict-send-email"
                    onClick={() => field.onChange(!field.value)}
                    disabled={!agentEmail}
                    className="relative h-4 w-7 shrink-0 rounded-full transition-colors"
                    style={{
                      background: field.value ? "var(--emerald-pass)" : "var(--border-strong)",
                    }}
                  >
                    <span
                      className="absolute top-[2px] h-3 w-3 rounded-full bg-white transition-all"
                      style={{ left: field.value ? 14 : 2 }}
                    />
                  </button>
                </FormControl>
                <div className="flex flex-1 items-center justify-between gap-2 text-[13px]">
                  <span>Send feedback email to agent</span>
                  <span className="text-[12px] text-[var(--text-muted)]">
                    {agentEmail ?? "no email on file"}
                  </span>
                </div>
              </label>
            </FormItem>
          )}
        />

        <div className="flex gap-2 pt-1">
          <Button
            type="button"
            variant="outline"
            className="flex-1"
            onClick={() => form.reset()}
            disabled={submitVerdict.isPending}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            className="flex-[2]"
            disabled={submitVerdict.isPending}
            data-testid="verdict-submit"
          >
            {submitVerdict.isPending
              ? "Submitting…"
              : sendEmail && agentEmail
                ? "Submit verdict + email"
                : "Submit verdict"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
