import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";

import { CursorPagination } from "@/components/shared/CursorPagination";

/**
 * CursorPagination unit tests (W5 G32).
 *
 * Covers:
 *   - "Showing X–Y of N" format
 *   - Next advances offset by limit
 *   - Prev decrements offset by limit (clamped to 0)
 *   - Disabled boundary states (first page → Prev disabled,
 *     last page → Next disabled)
 *   - `disabled` prop disables both buttons during fetches
 */
describe("CursorPagination", () => {
  it("formats 'Showing 1–50 of 200' on the first page", () => {
    const { getByTestId } = render(
      <CursorPagination offset={0} limit={50} total={200} onChange={() => {}} />,
    );
    const pager = getByTestId("cursor-pagination");
    // strip whitespace (numbers are wrapped in <span>) before asserting
    expect(pager.textContent?.replace(/\s+/g, " ")).toContain("Showing 1–50 of 200");
  });

  it("Next advances offset by limit", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(
      <CursorPagination offset={0} limit={50} total={200} onChange={onChange} />,
    );
    fireEvent.click(getByTestId("cursor-next"));
    expect(onChange).toHaveBeenCalledWith(50);
  });

  it("Prev decrements offset by limit", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(
      <CursorPagination offset={100} limit={50} total={200} onChange={onChange} />,
    );
    fireEvent.click(getByTestId("cursor-prev"));
    expect(onChange).toHaveBeenCalledWith(50);
  });

  it("Prev clamps at 0 when offset < limit", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(
      <CursorPagination offset={20} limit={50} total={200} onChange={onChange} />,
    );
    fireEvent.click(getByTestId("cursor-prev"));
    // offset(20) - limit(50) = -30 → clamped to 0
    expect(onChange).toHaveBeenCalledWith(0);
  });

  it("disables Prev on the first page and Next on the last page", () => {
    const { getByTestId, rerender } = render(
      <CursorPagination offset={0} limit={50} total={200} onChange={() => {}} />,
    );
    expect(getByTestId("cursor-prev")).toBeDisabled();
    expect(getByTestId("cursor-next")).not.toBeDisabled();

    // Last page: offset 150, limit 50, total 200 → end = 200 → no more
    rerender(<CursorPagination offset={150} limit={50} total={200} onChange={() => {}} />);
    expect(getByTestId("cursor-prev")).not.toBeDisabled();
    expect(getByTestId("cursor-next")).toBeDisabled();
  });

  it("disabled prop disables both buttons during fetches", () => {
    const { getByTestId } = render(
      <CursorPagination
        offset={50}
        limit={50}
        total={200}
        onChange={() => {}}
        disabled
      />,
    );
    expect(getByTestId("cursor-prev")).toBeDisabled();
    expect(getByTestId("cursor-next")).toBeDisabled();
  });

  it("shows 'No results' when total is 0", () => {
    const { getByTestId } = render(
      <CursorPagination offset={0} limit={50} total={0} onChange={() => {}} />,
    );
    expect(getByTestId("cursor-pagination").textContent).toContain("No results");
  });
});
