import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { StatusPipeline } from "@/app/(admin)/rejections/StatusPipeline";
import { PIPELINE_ORDER } from "@/lib/schemas/rejections";

/**
 * StatusPipeline — horizontal stepper visualisation. The pipeline ladder is
 * load-bearing: skipping a step drops a verdict-able transition and Watt's
 * audit trail can't replay the fix flow.
 */
describe("StatusPipeline", () => {
  it("ships the locked 6-step ladder in W2 order", () => {
    expect(PIPELINE_ORDER).toEqual([
      "NOT_STARTED",
      "IN_PROGRESS",
      "FIXED",
      "BATCHED_TO_PORTAL",
      "SUBMITTED_TO_PORTAL",
      "FIXED_AND_APPROVED",
    ]);
  });

  it("marks past steps as past, current as current, future as future", () => {
    const { container } = render(<StatusPipeline current="FIXED" />);
    const steps = container.querySelectorAll("[data-slot=pipeline-step]");
    expect(steps).toHaveLength(PIPELINE_ORDER.length);

    expect(steps[0].getAttribute("data-state")).toBe("past"); // NOT_STARTED
    expect(steps[1].getAttribute("data-state")).toBe("past"); // IN_PROGRESS
    expect(steps[2].getAttribute("data-state")).toBe("current"); // FIXED
    expect(steps[3].getAttribute("data-state")).toBe("future"); // BATCHED
    expect(steps[5].getAttribute("data-state")).toBe("future"); // APPROVED
  });

  it("renders the dead callout instead of the ladder when current is DEAD", () => {
    const { container, getByText } = render(<StatusPipeline current="DEAD" />);
    const root = container.querySelector("[data-slot=status-pipeline]");
    expect(root?.getAttribute("data-mode")).toBe("dead");
    expect(getByText(/Dead — pipeline halted/i)).toBeTruthy();
    // No pipeline steps in dead mode.
    expect(container.querySelectorAll("[data-slot=pipeline-step]")).toHaveLength(0);
  });

  it("respects the explicit isDead prop even when status is non-DEAD", () => {
    const { container } = render(<StatusPipeline current="IN_PROGRESS" isDead />);
    expect(
      container.querySelector("[data-slot=status-pipeline]")?.getAttribute("data-mode"),
    ).toBe("dead");
  });

  it("renders all 6 steps as future when status doesn't match the ladder", () => {
    const { container } = render(<StatusPipeline current="UNKNOWN_STATUS" />);
    const states = Array.from(
      container.querySelectorAll("[data-slot=pipeline-step]"),
    ).map((s) => s.getAttribute("data-state"));
    expect(states.every((s) => s === "future")).toBe(true);
  });
});
