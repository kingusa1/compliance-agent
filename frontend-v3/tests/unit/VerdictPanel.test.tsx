import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  VerdictPanel,
  verdictSchema,
} from "@/app/(reviewer)/calls/[id]/VerdictPanel";

// ── Mocks ────────────────────────────────────────────────────────
//
// VerdictPanel pulls in two TanStack mutations from
// `@/lib/mutations/reviewer`. We stub both so the unit test stays
// purely UI-level — no fetch, no Supabase, no toast network IO.
const submitVerdict = vi.fn();
const feedbackEmail = vi.fn();
vi.mock("@/lib/mutations/reviewer", () => ({
  useSubmitVerdict: () => ({
    mutateAsync: submitVerdict,
    mutate: submitVerdict,
    isPending: false,
  }),
  useFeedbackEmail: () => ({
    mutateAsync: feedbackEmail,
    mutate: feedbackEmail,
    isPending: false,
  }),
}));

// sonner toast — silenced; VerdictPanel doesn't call it directly but the
// mocked mutations would, were they real.
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  submitVerdict.mockReset().mockResolvedValue({ ok: true });
  feedbackEmail.mockReset().mockResolvedValue({ ok: true });
});

/**
 * VerdictPanel unit tests (W5 G32).
 *
 * Assertions
 *   1. All 5 verdict buttons render (PASS, REVIEW, COACHING, FAIL, BLOCK)
 *      with the documented semantic colours.
 *   2. zod schema rejects reasons shorter than 10 chars.
 *   3. Submit fires the mutation with {action, reason} (and optional email
 *      sent when sendEmail is true and an agentEmail is provided).
 */
describe("VerdictPanel", () => {
  it("renders all 5 verdict buttons with their semantic colours", () => {
    render(<VerdictPanel callId="call_abc" agentEmail={null} />, { wrapper });

    const expected: Array<[string, string]> = [
      ["PASS", "var(--emerald-pass)"],
      ["REVIEW", "var(--amber-review)"],
      ["COACHING", "var(--blue-coaching)"],
      ["FAIL", "var(--red-fail)"],
      ["BLOCK", "var(--violet-block)"],
    ];
    for (const [key, color] of expected) {
      const btn = screen.getByTestId(`verdict-action-${key}`);
      expect(btn).toBeInTheDocument();
      expect(btn).toHaveTextContent(key);
      // Background uses the colour token (either solid or 6% mix); either
      // is a valid signal that the colour is wired through.
      const style = btn.getAttribute("style") ?? "";
      expect(style).toContain(color);
    }
  });

  it("zod schema rejects reasons under 10 chars", () => {
    const tooShort = verdictSchema.safeParse({
      action: "PASS",
      reason: "short",
      sendEmail: false,
    });
    expect(tooShort.success).toBe(false);

    const ok = verdictSchema.safeParse({
      action: "PASS",
      reason: "Compliance verified after audit.",
      sendEmail: false,
    });
    expect(ok.success).toBe(true);
  });

  it("submit fires useSubmitVerdict with the correct payload", async () => {
    render(<VerdictPanel callId="call_xyz" agentEmail={null} />, { wrapper });

    // Default action is PASS — supply a valid (>=10 char) reason
    const reason = "Compliance verified after audit.";
    fireEvent.change(screen.getByTestId("verdict-reason"), { target: { value: reason } });

    fireEvent.click(screen.getByTestId("verdict-submit"));

    await waitFor(() => {
      expect(submitVerdict).toHaveBeenCalledTimes(1);
    });
    const call = submitVerdict.mock.calls[0][0];
    expect(call).toMatchObject({
      callId: "call_xyz",
      action: "PASS",
      reason,
    });
    // No agent email → feedback email path must not fire
    expect(feedbackEmail).not.toHaveBeenCalled();
  });

  it("also sends the feedback email when sendEmail is on and agentEmail is set", async () => {
    render(
      <VerdictPanel
        callId="call_qrs"
        agentEmail="agent@example.com"
        filename="rec-1.mp3"
      />,
      { wrapper },
    );

    fireEvent.change(screen.getByTestId("verdict-reason"), {
      target: { value: "Compliance verified after audit." },
    });
    // sendEmail defaults to true when agentEmail is provided
    fireEvent.click(screen.getByTestId("verdict-submit"));

    await waitFor(() => expect(submitVerdict).toHaveBeenCalled());
    await waitFor(() => expect(feedbackEmail).toHaveBeenCalled());
    const emailCall = feedbackEmail.mock.calls[0][0];
    expect(emailCall).toMatchObject({
      callId: "call_qrs",
      to_addr: "agent@example.com",
    });
    expect(emailCall.subject).toContain("rec-1.mp3");
    expect(emailCall.subject).toContain("PASS");
  });
});
