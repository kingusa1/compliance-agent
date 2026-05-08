import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";

import { ScoreGauge, gaugeColorFor } from "@/components/shared/ScoreGauge";

/**
 * ScoreGauge unit tests (W5 G32).
 *
 * Covers:
 *   - Threshold color: emerald >= 80, amber 60..79, red < 60
 *   - SVG strokeDashoffset matches the formula 2πr × (1 - value/100)
 *   - Clamping for out-of-range inputs (Math.round → integer)
 */
describe("ScoreGauge", () => {
  it("emerald stroke when value >= 80 (83%)", () => {
    const { getByTestId } = render(<ScoreGauge value={83} size={250} />);
    const wrapper = getByTestId("score-gauge");
    expect(wrapper.dataset.color).toBe("var(--emerald-pass)");
    expect(wrapper.dataset.value).toBe("83");
  });

  it("amber stroke for 60..79 inclusive (75%)", () => {
    const { getByTestId } = render(<ScoreGauge value={75} size={250} />);
    expect(getByTestId("score-gauge").dataset.color).toBe("var(--amber-review)");
  });

  it("red stroke when value < 60 (50%)", () => {
    const { getByTestId } = render(<ScoreGauge value={50} size={250} />);
    expect(getByTestId("score-gauge").dataset.color).toBe("var(--red-fail)");
  });

  it("strokeDashoffset matches 2πr × (1 - value/100)", () => {
    const size = 250;
    const value = 75;
    const { container } = render(<ScoreGauge value={value} size={size} />);
    // Second <circle> is the value arc (first one is the track ring)
    const circles = container.querySelectorAll("circle");
    expect(circles.length).toBe(2);
    const arc = circles[1];
    const stroke = Math.max(8, Math.round(size / 22));
    const r = (size - stroke) / 2;
    const c = 2 * Math.PI * r;
    const expectedOffset = c * (1 - value / 100);
    const actualOffset = parseFloat(arc.getAttribute("stroke-dashoffset") ?? "NaN");
    expect(actualOffset).toBeCloseTo(expectedOffset, 4);
    // Also verify dasharray is set to the full circumference
    expect(parseFloat(arc.getAttribute("stroke-dasharray") ?? "NaN")).toBeCloseTo(c, 4);
  });

  it("gaugeColorFor exposes the threshold mapping", () => {
    expect(gaugeColorFor(100)).toBe("var(--emerald-pass)");
    expect(gaugeColorFor(80)).toBe("var(--emerald-pass)");
    expect(gaugeColorFor(79)).toBe("var(--amber-review)");
    expect(gaugeColorFor(60)).toBe("var(--amber-review)");
    expect(gaugeColorFor(59)).toBe("var(--red-fail)");
    expect(gaugeColorFor(0)).toBe("var(--red-fail)");
  });

  it("clamps out-of-range values to 0..100", () => {
    const { getByTestId, rerender } = render(<ScoreGauge value={-10} />);
    expect(getByTestId("score-gauge").dataset.value).toBe("0");
    rerender(<ScoreGauge value={150} />);
    expect(getByTestId("score-gauge").dataset.value).toBe("100");
  });
});
