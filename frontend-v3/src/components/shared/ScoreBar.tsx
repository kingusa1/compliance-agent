"use client";

import { cn } from "@/lib/utils";
import { gaugeColorFor } from "./ScoreGauge";

/**
 * ScoreBar — mini horizontal score bar for table cells. Same threshold
 * palette as ScoreGauge (>=80 emerald / >=60 amber / <60 red) but
 * rendered inline so reviewers can scan a list and spot the
 * REVIEW/FAIL rows without reading the percent number.
 */

export type ScoreBarProps = {
  /** 0..100. Pass null when the call hasn't scored yet. */
  value: number | null | undefined;
  /** Optional explicit width. Default fills the cell (100%). */
  width?: number | string;
  /** Bar thickness. Default 6px. */
  height?: number;
  /** Show the trailing "75%" text. Default true. */
  showLabel?: boolean;
  className?: string;
};

export function ScoreBar({
  value,
  width = "100%",
  height = 6,
  showLabel = true,
  className,
}: ScoreBarProps) {
  if (value == null || !Number.isFinite(value)) {
    return (
      <div
        className={cn(
          "inline-flex items-center gap-2 text-[12px] text-[var(--text-dim)]",
          className,
        )}
      >
        —
      </div>
    );
  }
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  const color = gaugeColorFor(clamped);

  return (
    <div
      className={cn("inline-flex items-center gap-2", className)}
      role="img"
      aria-label={`Score ${clamped} percent`}
    >
      <div
        style={{
          width,
          height,
          background: "var(--bg-elev2)",
          borderRadius: height / 2,
          overflow: "hidden",
          minWidth: 60,
        }}
      >
        <div
          style={{
            width: `${clamped}%`,
            height: "100%",
            background: color,
            borderRadius: height / 2,
            transition: "width 0.4s ease",
          }}
        />
      </div>
      {showLabel && (
        <span
          className="font-mono tabular-nums text-[12px] text-[var(--text-primary)]"
          style={{ minWidth: 36, textAlign: "right" }}
        >
          {clamped}%
        </span>
      )}
    </div>
  );
}
