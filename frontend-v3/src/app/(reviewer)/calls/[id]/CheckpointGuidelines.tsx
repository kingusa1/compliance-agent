"use client";

// "How to judge this" expander rendered inside CheckpointCard's reviewer
// row. Lazily fetches the per-checkpoint guidelines payload on first
// open — strictness mode + the last 5 human-reviewer decisions for the
// same checkpoint+supplier — so the reviewer sees prior context before
// committing their own verdict.
//
// Ported from `frontend/src/components/CheckpointGuidelines.tsx` (main
// branch). The lifetime guard (clear cached `data` whenever the
// {callId, checkpointName} pair changes) is preserved verbatim because
// it fixes a real cross-contamination bug (VAT showing past Recording
// decisions) when React reuses component instances after a list reorder.

import { useState, useEffect } from "react";
import {
  getCheckpointGuidelines,
  type CheckpointGuidelinesResponse,
} from "@/lib/api";

export function CheckpointGuidelines({
  callId,
  checkpointName,
}: {
  callId: string;
  checkpointName: string;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<CheckpointGuidelinesResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setData(null);
  }, [callId, checkpointName]);

  useEffect(() => {
    if (!open || data) return;
    setLoading(true);
    getCheckpointGuidelines(callId, checkpointName)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, data, callId, checkpointName]);

  return (
    <details
      style={{
        fontSize: 11,
        borderLeft: "2px solid #2a2926",
        paddingLeft: 8,
        marginTop: 4,
        marginBottom: 2,
      }}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary
        style={{
          cursor: "pointer",
          color: "#6a655e",
          fontSize: 10,
          fontWeight: 500,
          userSelect: "none",
        }}
      >
        How to judge this
      </summary>

      {loading && (
        <p style={{ color: "#524f4a", fontStyle: "italic", margin: "6px 0 0" }}>
          Loading...
        </p>
      )}

      {data && (
        <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
          <div>
            <span style={{ fontWeight: 600, color: "#8a857e" }}>Strictness: </span>
            <span
              style={{
                fontSize: 9,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                padding: "1px 5px",
                borderRadius: 3,
                background:
                  data.strictness === "verbatim"
                    ? "rgba(249,115,22,0.15)"
                    : data.strictness === "customer_yes"
                      ? "rgba(45,212,191,0.15)"
                      : "rgba(148,163,184,0.1)",
                color:
                  data.strictness === "verbatim"
                    ? "#f97316"
                    : data.strictness === "customer_yes"
                      ? "#2dd4bf"
                      : "#8a857e",
              }}
            >
              {data.strictness === "verbatim"
                ? "Word for Word"
                : data.strictness === "customer_yes"
                  ? "+ Customer Yes"
                  : "Meaning"}
            </span>
          </div>

          {data.examples.length > 0 && (
            <div>
              <span style={{ fontWeight: 600, color: "#8a857e" }}>
                Past reviewer decisions ({data.examples.length}):
              </span>
              <ul
                style={{
                  margin: "4px 0 0",
                  paddingLeft: 16,
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                {data.examples.map((ex, i) => (
                  <li key={i} style={{ color: "#c8c3bc", lineHeight: 1.4 }}>
                    <span style={{ color: "#8a857e" }}>{ex.pattern}</span>
                    {" → "}
                    <span
                      style={{
                        fontWeight: 600,
                        color:
                          ex.human_verdict === "pass"
                            ? "#22c55e"
                            : ex.human_verdict === "fail"
                              ? "#ef4444"
                              : "#f59e0b",
                      }}
                    >
                      {ex.human_verdict}
                    </span>
                    {ex.agent_verdict !== ex.human_verdict && (
                      <span style={{ color: "#524f4a", fontSize: 10 }}>
                        {" "}(AI said {ex.agent_verdict})
                      </span>
                    )}
                    {ex.lesson && (
                      <div style={{ fontSize: 10, color: "#6a655e", marginTop: 1 }}>
                        {ex.lesson}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.examples.length === 0 && (
            <p style={{ color: "#524f4a", fontStyle: "italic", margin: 0 }}>
              No past reviewer decisions for this checkpoint yet.
            </p>
          )}
        </div>
      )}
    </details>
  );
}
