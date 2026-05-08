import { describe, it, expect } from "vitest";

import {
  buildEmailPreview,
  suggestAggregate,
  type PerCpAction,
} from "@/app/(reviewer)/calls/[id]/email-preview";

/**
 * email-preview unit tests — pure builder, no React, no fetch.
 *
 * Covers:
 *  - suggestAggregate priority order (recall_redo → FAIL, supp → REVIEW, coach → COACHING, else PASS)
 *  - buildEmailPreview subject + key sections (issues, follow-up counter, reviewer notes)
 *  - empty perCpActions → "None — all checkpoints passed."
 *  - score + customer + agent name interpolation
 */

describe("suggestAggregate", () => {
  it("returns null when no actions are set", () => {
    expect(suggestAggregate(new Map())).toBeNull();
  });

  it("returns PASS when every action is no_action / ignore", () => {
    const m = new Map<string, PerCpAction>([
      ["a", "no_action"],
      ["b", "ignore"],
    ]);
    expect(suggestAggregate(m)).toBe("PASS");
  });

  it("recall_redo wins over coach + reask_supp → FAIL", () => {
    const m = new Map<string, PerCpAction>([
      ["a", "coach"],
      ["b", "reask_supp"],
      ["c", "recall_redo"],
    ]);
    expect(suggestAggregate(m)).toBe("FAIL");
  });

  it("supplementary or schedule_another → REVIEW", () => {
    const m1 = new Map<string, PerCpAction>([
      ["a", "no_action"],
      ["b", "reask_supp"],
    ]);
    expect(suggestAggregate(m1)).toBe("REVIEW");

    const m2 = new Map<string, PerCpAction>([
      ["a", "schedule_another"],
    ]);
    expect(suggestAggregate(m2)).toBe("REVIEW");
  });

  it("coach (alone) → COACHING", () => {
    const m = new Map<string, PerCpAction>([
      ["a", "no_action"],
      ["b", "coach"],
    ]);
    expect(suggestAggregate(m)).toBe("COACHING");
  });
});

describe("buildEmailPreview", () => {
  const baseCheckpoints = [
    { key: "credit-checks", name: "Credit Checks", status: "fail" },
    { key: "marketing", name: "Marketing Consent", status: "partial" },
    { key: "recording", name: "Recording Disclosure", status: "pass" },
  ];

  it("builds subject + body for a typical mixed verdict", () => {
    const actions = new Map<string, PerCpAction>([
      ["credit-checks", "recall_redo"],
      ["marketing", "coach"],
      ["recording", "no_action"],
    ]);
    const comments = new Map<string, string>([
      ["credit-checks", "No credit check evidence found in transcript."],
      ["marketing", "Consent was implied but not explicit."],
    ]);

    const out = buildEmailPreview({
      callId: "abc-123",
      agentName: "Sarah",
      customerName: "John Smith",
      filename: "call-001.wav",
      score: "22/25",
      aggregate: "FAIL",
      overallReason: "2 checkpoints failed.",
      reviewerEmail: "compliance@xaia.ae",
      perCpActions: actions,
      perCpComments: comments,
      checkpoints: baseCheckpoints,
    });

    expect(out.subject).toBe("Compliance review — call-001.wav — FAIL");
    expect(out.body).toContain("Hi Sarah,");
    expect(out.body).toContain("Your call (call-001.wav for John Smith)");
    expect(out.body).toContain("Score: 22/25.");
    expect(out.body).toContain("ISSUES IDENTIFIED:");
    expect(out.body).toContain("• Credit Checks — FAIL");
    expect(out.body).toContain("Issue: No credit check evidence found in transcript.");
    expect(out.body).toContain("Action: Recall + redo whole call");
    expect(out.body).toContain("• Marketing Consent — PARTIAL");
    expect(out.body).toContain("Action: Coach agent");
    // pass + no_action checkpoint must NOT appear
    expect(out.body).not.toContain("Recording Disclosure");
    // overall reason
    expect(out.body).toContain("REVIEWER NOTES:");
    expect(out.body).toContain("2 checkpoints failed.");
    // reviewer signature
    expect(out.body).toContain("Reviewer: compliance@xaia.ae");
  });

  it("shows follow-up counter when reask_supp / schedule_another picked", () => {
    const actions = new Map<string, PerCpAction>([
      ["credit-checks", "schedule_another"],
      ["marketing", "reask_supp"],
    ]);
    const comments = new Map<string, string>([
      ["credit-checks", "Need clarification."],
      ["marketing", "Re-ask consent."],
    ]);

    const out = buildEmailPreview({
      callId: "abc-123",
      agentName: null,
      customerName: null,
      filename: null,
      score: null,
      aggregate: "REVIEW",
      overallReason: "",
      reviewerEmail: null,
      perCpActions: actions,
      perCpComments: comments,
      checkpoints: baseCheckpoints,
    });

    expect(out.body).toContain("REQUIRED FOLLOW-UP CALLS:");
    expect(out.body).toContain("— 2 supplementary calls needed.");
    expect(out.body).toContain("Hi team,"); // null agentName fallback
    expect(out.body).toContain("Your call (abc-123 for customer)"); // null fallbacks
    expect(out.subject).toBe("Compliance review — abc-123 — REVIEW");
    // null reviewerEmail falls back to the default contact
    expect(out.body).toContain("Reviewer: compliance@xaia.ae");
  });

  it("emits 'None — all checkpoints passed.' when no per-CP issues", () => {
    const out = buildEmailPreview({
      callId: "abc-123",
      agentName: "Mo",
      customerName: "Layla",
      filename: "call.wav",
      score: "25/25",
      aggregate: "PASS",
      overallReason: "Clean call.",
      reviewerEmail: "compliance@xaia.ae",
      perCpActions: new Map(),
      perCpComments: new Map(),
      checkpoints: baseCheckpoints,
    });

    expect(out.body).toContain("ISSUES IDENTIFIED:");
    expect(out.body).toContain("None — all checkpoints passed.");
    expect(out.body).not.toContain("REQUIRED FOLLOW-UP CALLS:");
  });
});
