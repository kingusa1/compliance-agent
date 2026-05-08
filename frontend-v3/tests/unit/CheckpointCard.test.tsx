import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";

import {
  CheckpointCard,
  parseCheckpointResults,
  type CheckpointVerdict,
} from "@/app/(reviewer)/calls/[id]/CheckpointCard";
import type { ScriptCheckpoint } from "@/lib/queries/reviewer";

/**
 * CheckpointCard unit tests.
 *
 * Covers:
 *  - parseCheckpointResults parses backend JSON blob into typed verdicts
 *  - 3 sections (script rule, what was said, AI verdict) render
 *  - Play button fires onPlay with the right startSec
 *  - Pass/Fail/Partial badge maps from status
 *  - Missing verdict renders "Not yet scored" placeholder
 */

const sampleScript: ScriptCheckpoint = {
  section: 1,
  name: "Recording Disclosure",
  required: "Inform customer that calls are recorded for monitoring purposes",
  key_phrases: ["recorded", "monitoring"],
  customer_response_required: false,
  strictness: "mandatory",
};

const sampleVerdict: CheckpointVerdict = {
  section: 1,
  name: "Recording Disclosure",
  status: "pass",
  evidence: "calls are recorded for monitoring purposes",
  notes: "Agent stated recording disclosure with key phrase 'recorded' matched.",
  confidence: "high",
  needs_review: false,
  start_ms: 8000,
  end_ms: 12000,
};

describe("parseCheckpointResults", () => {
  it("parses a JSON blob into typed CheckpointVerdict[]", () => {
    const blob = JSON.stringify([
      {
        section: 1,
        name: "Recording Disclosure",
        status: "pass",
        evidence: "calls are recorded",
        notes: "Matched.",
        confidence: "high",
        needs_review: false,
        start_ms: 8000,
        end_ms: 12000,
      },
    ]);
    const out = parseCheckpointResults(blob);
    expect(out).toHaveLength(1);
    expect(out[0].name).toBe("Recording Disclosure");
    expect(out[0].status).toBe("pass");
    expect(out[0].start_ms).toBe(8000);
  });

  it("returns [] on null/empty/malformed input", () => {
    expect(parseCheckpointResults(null)).toEqual([]);
    expect(parseCheckpointResults("")).toEqual([]);
    expect(parseCheckpointResults("not json")).toEqual([]);
    expect(parseCheckpointResults("{}")).toEqual([]); // object, not array
  });
});

describe("CheckpointCard", () => {
  it("renders the 3 section headers + script text + reasoning", () => {
    const { getByText, container } = render(
      <CheckpointCard
        index={0}
        script={sampleScript}
        verdict={sampleVerdict}
        startSec={8}
        isActive={false}
        onPlay={() => {}}
      />,
    );
    // CP id pill
    expect(getByText("CP01")).toBeInTheDocument();
    // Name in header
    expect(getByText("Recording Disclosure")).toBeInTheDocument();
    // Section 1 — Script
    expect(getByText("Script")).toBeInTheDocument();
    expect(
      getByText(/Inform customer that calls are recorded for monitoring purposes/i),
    ).toBeInTheDocument();
    // Section 2 — AI Verdict (reasoning)
    expect(getByText("AI Verdict")).toBeInTheDocument();
    expect(getByText(/Agent stated recording disclosure/)).toBeInTheDocument();
    // Section 3 — Actual Call (evidence quote)
    expect(getByText(/^Actual Call/)).toBeInTheDocument();
    expect(container.textContent).toContain("calls are recorded for monitoring purposes");
  });

  it("calls onPlay with the provided startSec when Play is clicked", () => {
    const onPlay = vi.fn();
    const { getByLabelText } = render(
      <CheckpointCard
        index={0}
        script={sampleScript}
        verdict={sampleVerdict}
        startSec={8}
        isActive={false}
        onPlay={onPlay}
      />,
    );
    // Component renders two play affordances: a card-wide wrapper
     // (aria-label "Play from checkpoint: …") and a Section-3 button
     // (aria-label "Play from 0:08"). The latter is the explicit Play
     // button — match its time-only label to disambiguate.
    const playBtn = getByLabelText(/^Play from \d+:\d{2}$/);
    fireEvent.click(playBtn);
    expect(onPlay).toHaveBeenCalledWith(8);
  });

  it("renders 'Not yet scored' placeholder when verdict is missing", () => {
    const { getAllByText, getByText } = render(
      <CheckpointCard
        index={2}
        script={sampleScript}
        verdict={undefined}
        startSec={null}
        isActive={false}
        onPlay={() => {}}
      />,
    );
    // Appears in both the header badge and the AI verdict body
    expect(getAllByText(/Not yet scored/i).length).toBeGreaterThanOrEqual(1);
    expect(getByText("CP03")).toBeInTheDocument();
  });

  it("shows PARTIAL badge for partial status", () => {
    const partial: CheckpointVerdict = { ...sampleVerdict, status: "partial" };
    const { getAllByText, container } = render(
      <CheckpointCard
        index={0}
        script={sampleScript}
        verdict={partial}
        startSec={8}
        isActive={false}
        onPlay={() => {}}
      />,
    );
    // Header badge renders displayStateLabel("partial") = "Partial".
    // CSS uppercases visually but DOM text is mixed-case.
    expect(getAllByText("Partial").length).toBeGreaterThanOrEqual(1);
    // Section 3 header reflects the partial state too.
    expect(container.textContent).toMatch(/partial match/i);
  });

  it("disables play (renders dash) when startSec is null", () => {
    // Verdict-level start_ms must also be null — the component falls back
    // to verdict.start_ms when prop startSec is null.
    const noTimestamp: CheckpointVerdict = { ...sampleVerdict, start_ms: null };
    const { container, queryByRole } = render(
      <CheckpointCard
        index={0}
        script={sampleScript}
        verdict={noTimestamp}
        startSec={null}
        isActive={false}
        onPlay={() => {}}
      />,
    );
    // No explicit Play <button> renders when there's no timestamp.
    // (The card-wrapper still has an aria-label, but role=button buttons
    //  is what we care about — there are no real <button>s.)
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(0);
    expect(queryByRole("button", { name: /^Play from \d+:\d{2}$/ })).toBeNull();
    // Header timestamp is suppressed when there's no verdict timestamp;
    // approximate-marker dash ("~") is the no-timestamp signal.
    expect(container.textContent).toMatch(/~|—/);
  });
});
