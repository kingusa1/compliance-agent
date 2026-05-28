/**
 * Regression: formatErrorDetail must extract a human-readable message
 * from every FastAPI error body shape so the toast never says
 * "[object Object]" again.
 *
 * Wave-40 (2026-05-28) — owner-reported: manual-mode upload showed a
 * literal "[object Object]" toast. Root cause: uploadMultipart called
 * `new Error(err.detail)` where detail was an ARRAY of Pydantic
 * validation objects on 422. Array → string coerces to "[object Object]".
 */
import { describe, it, expect } from "vitest";

import { formatErrorDetail } from "@/lib/mutations";

describe("formatErrorDetail", () => {
  it("returns plain string detail verbatim", () => {
    expect(formatErrorDetail({ detail: "Call already exists" }, "fb")).toBe(
      "Call already exists",
    );
  });

  it("uses fallback when detail is missing", () => {
    expect(formatErrorDetail({}, "Upload failed: 500")).toBe(
      "Upload failed: 500",
    );
  });

  it("uses fallback when body is not an object", () => {
    expect(formatErrorDetail(null, "Upload failed: 500")).toBe(
      "Upload failed: 500",
    );
    expect(formatErrorDetail("oops", "Upload failed: 500")).toBe(
      "Upload failed: 500",
    );
  });

  it("formats FastAPI 422 single-error array", () => {
    const body = {
      detail: [
        {
          type: "missing",
          loc: ["body", "deal", "supplier"],
          msg: "Field required",
          input: null,
        },
      ],
    };
    expect(formatErrorDetail(body, "fb")).toBe("deal.supplier: Field required");
  });

  it("formats FastAPI 422 multi-error array with '+N more'", () => {
    const body = {
      detail: [
        { type: "missing", loc: ["body", "phone"], msg: "Field required" },
        { type: "missing", loc: ["body", "notes"], msg: "Field required" },
        { type: "missing", loc: ["body", "deal", "value"], msg: "Field required" },
      ],
    };
    expect(formatErrorDetail(body, "fb")).toBe(
      "phone: Field required (+2 more)",
    );
  });

  it("falls back to type when msg is missing", () => {
    const body = {
      detail: [{ type: "value_error", loc: ["body", "x"] }],
    };
    expect(formatErrorDetail(body, "fb")).toBe("x: value_error");
  });

  it("handles empty loc array", () => {
    const body = {
      detail: [{ msg: "validation failed", loc: [] }],
    };
    expect(formatErrorDetail(body, "fb")).toBe("validation failed");
  });

  it("serializes object-shaped detail (e.g. METADATA_MISMATCH domain code)", () => {
    const body = {
      detail: { code: "METADATA_MISMATCH", manual: "EON", auto: "Pozitive" },
    };
    const out = formatErrorDetail(body, "fb");
    // Keeps the code string so admin.ts:uploadCall's regex can match.
    expect(out).toContain("METADATA_MISMATCH");
    expect(out).toContain("manual");
    expect(out).toContain("auto");
  });

  it("prefers detail.message when present (Watt {code,message} shape)", () => {
    const body = {
      detail: {
        code: "meter_required",
        message: "Provide MPAN (electricity) and/or MPRN (gas)",
      },
    };
    expect(formatErrorDetail(body, "fb")).toBe(
      "Provide MPAN (electricity) and/or MPRN (gas)",
    );
  });

  it("prefers detail.detail when nested string is the only signal", () => {
    const body = {
      detail: { detail: "Upload conflict on this file" },
    };
    expect(formatErrorDetail(body, "fb")).toBe("Upload conflict on this file");
  });

  it("NEVER returns '[object Object]' (the bug this regression locks)", () => {
    const arrayDetail = {
      detail: [{ loc: ["body", "x"], msg: "bad" }],
    };
    expect(formatErrorDetail(arrayDetail, "fb")).not.toBe("[object Object]");

    const objectDetail = { detail: { weird: { nested: "shape" } } };
    expect(formatErrorDetail(objectDetail, "fb")).not.toBe("[object Object]");

    const emptyArrayDetail = { detail: [] };
    // Empty array falls through to fallback (correct behaviour).
    expect(formatErrorDetail(emptyArrayDetail, "fb")).toBe("fb");
  });
});
