"use client";

/**
 * ScoreGauge — radial composite-score gauge used by the deal verdict
 * aggregator (UX-D17). Tremor was dropped from the v3 stack due to a
 * React 19 peer-dep cap (R1 decision), so this is a pure-SVG gauge:
 * a single stroked circle with `strokeDasharray`/`strokeDashoffset`
 * giving the fill arc, percent text in the center, threshold colors
 * (>=80 emerald / >=60 amber / <60 red) for the rapid PASS/REVIEW/FAIL
 * read.
 */

export type ScoreGaugeProps = {
  /** 0..100. Values are clamped before rendering. */
  value: number;
  /** Outer SVG width / height in px. Default 250. */
  size?: number;
  /** Override stroke width. Default size/22 (≈ 12px at 250px). */
  strokeWidth?: number;
  /** When true, hides the center text — useful for nested rings. */
  hideLabel?: boolean;
  /** Override the visual color (skips threshold logic). */
  color?: string;
  /** Subtitle under the percent (e.g. "Composite"). */
  caption?: string;
  className?: string;
};

/** Pick the threshold-based stroke color for a given score. */
export function gaugeColorFor(value: number): string {
  if (value >= 80) return "var(--emerald-pass)";
  if (value >= 60) return "var(--amber-review)";
  return "var(--red-fail)";
}

export function ScoreGauge({
  value,
  size = 250,
  strokeWidth,
  hideLabel = false,
  color,
  caption,
  className,
}: ScoreGaugeProps) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  const stroke = strokeWidth ?? Math.max(8, Math.round(size / 22));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - clamped / 100);
  const strokeColor = color ?? gaugeColorFor(clamped);

  return (
    <div
      className={className}
      style={{ position: "relative", width: size, height: size }}
      role="img"
      aria-label={`Composite score ${clamped} percent`}
      data-testid="score-gauge"
      data-value={clamped}
      data-color={strokeColor}
    >
      <svg width={size} height={size} style={{ display: "block" }}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke="var(--bg-elev2)"
          strokeWidth={stroke}
          fill="none"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={strokeColor}
          strokeWidth={stroke}
          fill="none"
          strokeDasharray={c}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: "stroke-dashoffset 0.8s ease" }}
        />
      </svg>
      {!hideLabel && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
          }}
        >
          <div
            style={{
              fontSize: Math.round(size * 0.22),
              fontWeight: 600,
              color: "var(--text-primary)",
              fontVariantNumeric: "tabular-nums",
              letterSpacing: "-0.02em",
              lineHeight: 1,
            }}
          >
            {clamped}
            <span
              style={{
                fontSize: Math.round(size * 0.1),
                color: "var(--text-muted)",
                fontWeight: 500,
              }}
            >
              %
            </span>
          </div>
          {caption && (
            <div
              style={{
                fontSize: 11,
                color: "var(--text-dim)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginTop: 8,
              }}
            >
              {caption}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
