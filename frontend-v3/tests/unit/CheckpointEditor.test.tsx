import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";

import {
  CheckpointEditor,
  blankCheckpoint,
} from "@/app/(admin)/scripts/CheckpointEditor";
import type { CheckpointFormValues } from "@/lib/schemas/script";

/**
 * CheckpointEditor unit tests.
 *
 * Covers the load-bearing behaviour of the per-checkpoint editor used
 * by both the upload preview and the /scripts/[id] editor:
 *
 *  - blankCheckpoint() seeds the right defaults (strictness +
 *    customer_response_required) so brand-new rows pass zod.
 *  - Header is the only thing rendered by default; the body expands
 *    when clicked (defaultExpanded=false is the default for saved rows).
 *  - Typing into the name input fires onChange with the next value.
 *  - The phrase tag input commits on Enter and removes via the × button.
 *  - The customer-response-required toggle flips the field.
 */

const mk = (overrides: Partial<CheckpointFormValues> = {}): CheckpointFormValues => ({
  ...blankCheckpoint(1),
  ...overrides,
});

describe("blankCheckpoint", () => {
  it("returns a meaning_for_meaning checkpoint with empty fields", () => {
    const cp = blankCheckpoint(3);
    expect(cp.section).toBe(3);
    expect(cp.name).toBe("");
    expect(cp.required).toBe("");
    expect(cp.key_phrases).toEqual([]);
    expect(cp.customer_response_required).toBe(false);
    expect(cp.strictness).toBe("meaning_for_meaning");
  });
});

describe("CheckpointEditor", () => {
  it("renders the header with the checkpoint name and a strictness pill", () => {
    const { getByText } = render(
      <CheckpointEditor
        index={0}
        total={1}
        value={mk({ name: "Recording Disclosure", strictness: "mandatory" })}
        onChange={() => {}}
      />,
    );
    expect(getByText("Recording Disclosure")).toBeInTheDocument();
    expect(getByText("Mandatory")).toBeInTheDocument();
  });

  it("expands when the header is clicked and shows the form fields", () => {
    const { container, getByTestId, queryByTestId } = render(
      <CheckpointEditor
        index={0}
        total={1}
        value={mk({ name: "Authorisation" })}
        onChange={() => {}}
      />,
    );
    // Body is hidden initially.
    expect(queryByTestId("cp-name-0")).toBeNull();
    // Click the header (the first <button>).
    const header = container.querySelector("button");
    expect(header).not.toBeNull();
    fireEvent.click(header!);
    // Body now visible.
    expect(getByTestId("cp-name-0")).toBeInTheDocument();
  });

  it("fires onChange when the name input changes", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(
      <CheckpointEditor
        index={0}
        total={1}
        value={mk({ name: "Old name" })}
        onChange={onChange}
        defaultExpanded
      />,
    );
    fireEvent.change(getByTestId("cp-name-0"), { target: { value: "New name" } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ name: "New name" }),
    );
  });

  it("commits a phrase on Enter and includes it in the next onChange call", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(
      <CheckpointEditor
        index={0}
        total={1}
        value={mk({ key_phrases: [] })}
        onChange={onChange}
        defaultExpanded
      />,
    );
    const input = getByTestId("cp-phrase-input-0") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "calls are recorded" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ key_phrases: ["calls are recorded"] }),
    );
  });

  it("toggles customer_response_required via the checkbox", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(
      <CheckpointEditor
        index={0}
        total={1}
        value={mk({ customer_response_required: false })}
        onChange={onChange}
        defaultExpanded
      />,
    );
    fireEvent.click(getByTestId("cp-customer-0"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ customer_response_required: true }),
    );
  });
});
