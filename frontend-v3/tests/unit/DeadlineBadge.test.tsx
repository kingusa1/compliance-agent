import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import {
  DeadlineBadge,
  formatDeadlineLabel,
} from "@/app/(admin)/rejections/DeadlineBadge";

/**
 * DeadlineBadge — auto-deadline (rejected_at + 2d) is the first time Watt's
 * 2-day SLA gets enforced anywhere in the product. The colour bands directly
 * gate reviewer urgency; drift here = silent SLA misses.
 *
 *   > 48h        — gray
 *   24-48h       — yellow
 *   < 24h        — amber
 *   overdue      — red (with alert icon)
 *   terminal     — gray em-dash
 */
describe("formatDeadlineLabel", () => {
  const NOW = new Date("2026-05-03T12:00:00Z");

  it("classifies a 3-day buffer as gray", () => {
    const dl = new Date("2026-05-06T12:00:00Z");
    const r = formatDeadlineLabel(dl, NOW);
    expect(r.tone).toBe("gray");
    expect(r.overdue).toBe(false);
    expect(r.label).toMatch(/3d/);
  });

  it("classifies a 36h buffer as yellow", () => {
    const dl = new Date("2026-05-05T00:00:00Z"); // +36h
    const r = formatDeadlineLabel(dl, NOW);
    expect(r.tone).toBe("yellow");
    expect(r.overdue).toBe(false);
  });

  it("classifies a 12h buffer as amber", () => {
    const dl = new Date("2026-05-04T00:00:00Z"); // +12h
    const r = formatDeadlineLabel(dl, NOW);
    expect(r.tone).toBe("amber");
    expect(r.overdue).toBe(false);
    expect(r.label).toMatch(/12h/);
  });

  it("classifies an overdue 2h slip as red", () => {
    const dl = new Date("2026-05-03T10:00:00Z"); // -2h
    const r = formatDeadlineLabel(dl, NOW);
    expect(r.tone).toBe("red");
    expect(r.overdue).toBe(true);
    expect(r.label).toMatch(/overdue 2h/);
  });

  it("classifies a < 1h buffer with minute-precision label", () => {
    const dl = new Date("2026-05-03T12:45:00Z"); // +45m
    const r = formatDeadlineLabel(dl, NOW);
    expect(r.tone).toBe("amber");
    expect(r.label).toMatch(/45m left/);
  });

  it("classifies a < 1h overdue with minute-precision label", () => {
    const dl = new Date("2026-05-03T11:30:00Z"); // -30m
    const r = formatDeadlineLabel(dl, NOW);
    expect(r.tone).toBe("red");
    expect(r.label).toMatch(/overdue 30m/);
  });
});

describe("DeadlineBadge", () => {
  it("renders an em-dash when status is FIXED_AND_APPROVED", () => {
    const { container } = render(
      <DeadlineBadge
        deadline="2026-05-04T00:00:00Z"
        status="FIXED_AND_APPROVED"
      />,
    );
    const el = container.querySelector("[data-slot=deadline-badge]");
    expect(el?.getAttribute("data-tone")).toBe("terminal");
    expect(el?.textContent).toBe("—");
  });

  it("renders an em-dash when status is DEAD", () => {
    const { container } = render(
      <DeadlineBadge deadline="2026-05-04T00:00:00Z" status="DEAD" />,
    );
    expect(
      container.querySelector("[data-slot=deadline-badge]")?.getAttribute("data-tone"),
    ).toBe("terminal");
  });

  it("flags overdue rows with the red tone + overdue attribute", () => {
    const past = new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(); // 5h ago
    const { container } = render(<DeadlineBadge deadline={past} />);
    const el = container.querySelector("[data-slot=deadline-badge]");
    expect(el?.getAttribute("data-tone")).toBe("red");
    expect(el?.getAttribute("data-overdue")).toBe("1");
  });
});
