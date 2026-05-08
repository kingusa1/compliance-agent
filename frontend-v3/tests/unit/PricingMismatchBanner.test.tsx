import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";

import { PricingMismatchBanner } from "@/app/(reviewer)/calls/[id]/PricingMismatchBanner";
import type { Flag } from "@/lib/queries/reviewer";

/**
 * PricingMismatchBanner unit tests.
 *
 * Covers:
 *  - renders nothing when no PRICING_MISMATCH flags exist
 *  - renders one row per pricing flag with the reason text
 *  - clicking a row fires onSeek with that flag
 *  - non-pricing flags are filtered out
 */

const pricingFlag = (overrides: Partial<Flag> = {}): Flag => ({
  id: "f-pr-1",
  call_id: "call-123",
  rule_id: "PRICING_MISMATCH",
  severity: "HIGH",
  reason: "Pricing mismatch — agent quoted 11p/kWh, script says 10p/kWh",
  word_start: 142,
  word_end: 145,
  evidence: "eleven pence per kWh",
  risk_tag: "mis-selling",
  ...overrides,
});

describe("PricingMismatchBanner", () => {
  it("renders nothing when there are no pricing-mismatch flags", () => {
    const onSeek = vi.fn();
    const { container } = render(<PricingMismatchBanner flags={[]} onSeek={onSeek} />);
    expect(container.firstChild).toBeNull();
  });

  it("filters out non-pricing flags", () => {
    const onSeek = vi.fn();
    const { container } = render(
      <PricingMismatchBanner
        flags={[
          { ...pricingFlag(), id: "f-other", rule_id: "MISSING_DISCLOSURE" },
        ]}
        onSeek={onSeek}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one row per pricing flag with the reason text", () => {
    const onSeek = vi.fn();
    render(
      <PricingMismatchBanner
        flags={[
          pricingFlag(),
          pricingFlag({
            id: "f-pr-2",
            reason: "Pricing mismatch — agent quoted 40p/day standing charge, script says 30p/day",
          }),
        ]}
        onSeek={onSeek}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/agent quoted 11p\/kWh/)).toBeInTheDocument();
    expect(screen.getByText(/standing charge/)).toBeInTheDocument();
    expect(screen.getByText(/2 issues/)).toBeInTheDocument();
  });

  it("fires onSeek with the clicked flag", () => {
    const onSeek = vi.fn();
    const f = pricingFlag();
    render(<PricingMismatchBanner flags={[f]} onSeek={onSeek} />);

    fireEvent.click(screen.getByTestId(`pricing-mismatch-row-${f.id}`));
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenCalledWith(f);
  });
});
