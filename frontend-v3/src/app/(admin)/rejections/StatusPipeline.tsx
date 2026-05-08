"use client";

/**
 * StatusPipeline — horizontal stepper for the rejection-status ladder.
 * Ported from design/handoff-bundle/project/hifi/rejections-hifi.jsx
 * (StatusPipeline + PIPELINE).
 *
 *   NOT_STARTED → IN_PROGRESS → FIXED → BATCHED_TO_PORTAL
 *                            → SUBMITTED_TO_PORTAL → FIXED_AND_APPROVED
 *
 * DEAD bypasses the ladder entirely — we render the "Dead — pipeline halted"
 * red callout instead, matching the prototype.
 */
import { Ban, Check } from "lucide-react";
import { Fragment } from "react";

import {
  PIPELINE_ORDER,
  REJECTION_STATUS_LABELS,
  type RejectionStatus,
} from "@/lib/schemas/rejections";

export type StatusPipelineProps = {
  current: RejectionStatus | string;
  isDead?: boolean;
};

export function StatusPipeline({ current, isDead = false }: StatusPipelineProps) {
  if (isDead || current === "DEAD") {
    return (
      <div
        data-slot="status-pipeline"
        data-mode="dead"
        style={{
          padding: "12px 14px",
          background: "var(--red-bg)",
          border: "1px solid var(--red-border)",
          borderRadius: 6,
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span style={{ color: "var(--red)", display: "flex" }}>
          <Ban size={16} strokeWidth={1.75} />
        </span>
        <div style={{ flex: 1 }}>
          <div
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: "var(--red)",
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            Dead — pipeline halted
          </div>
          <div
            style={{
              fontSize: 11.5,
              color: "rgba(239,68,68,0.7)",
              marginTop: 2,
            }}
          >
            Cannot recover. See dead-reason below.
          </div>
        </div>
      </div>
    );
  }

  const idx = PIPELINE_ORDER.findIndex((p) => p === (current as RejectionStatus));
  return (
    <div
      data-slot="status-pipeline"
      data-current={current}
      style={{ display: "flex", alignItems: "center", gap: 0 }}
    >
      {PIPELINE_ORDER.map((p, i) => {
        const isPast = i < idx;
        const isCurrent = i === idx;
        const dot = isPast
          ? "var(--emerald)"
          : isCurrent
            ? "var(--amber)"
            : "var(--bg-elev3)";
        const ring = isCurrent ? "0 0 0 3px var(--amber-bg)" : "none";
        const ringColor = isPast
          ? "var(--emerald)"
          : isCurrent
            ? "var(--amber)"
            : "var(--border-strong)";
        return (
          <Fragment key={p}>
            <div
              data-slot="pipeline-step"
              data-step={p}
              data-state={isPast ? "past" : isCurrent ? "current" : "future"}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 6,
                minWidth: 0,
                flex: "0 0 auto",
              }}
            >
              <div
                style={{
                  width: 18,
                  height: 18,
                  borderRadius: "50%",
                  background: dot,
                  border: `1.5px solid ${ringColor}`,
                  boxShadow: ring,
                  display: "grid",
                  placeItems: "center",
                  color: "#04201a",
                }}
              >
                {isPast && <Check size={10} strokeWidth={3.5} />}
              </div>
              <div
                style={{
                  fontSize: 9.5,
                  fontWeight: isCurrent ? 600 : 500,
                  color: isCurrent
                    ? "var(--amber)"
                    : isPast
                      ? "var(--text-primary)"
                      : "var(--text-dim)",
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  textAlign: "center",
                  whiteSpace: "nowrap",
                }}
              >
                {REJECTION_STATUS_LABELS[p]}
              </div>
            </div>
            {i < PIPELINE_ORDER.length - 1 && (
              <div
                style={{
                  flex: 1,
                  height: 1.5,
                  background:
                    i < idx ? "var(--emerald)" : "var(--border-strong)",
                  margin: "0 4px",
                  marginTop: -22,
                }}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
