/**
 * ScoreBar — mini horizontal score bar with threshold colour.
 *
 *   ≥ 85% → emerald (PASS)
 *   ≥ 70% → amber   (REVIEW)
 *   else  → red     (FAIL)
 *
 * Renders "<pct>% [=====    ]" with the bar tracking the same colour. Used
 * in the queue table master list and the queue preview panel.
 */
import { parseScore } from "@/lib/score";

export function ScoreBar({
  score,
  pct,
  className = "",
  width = 56,
}: {
  /** "21/24" style fraction string from the backend */
  score?: string | number | null;
  /** OR a precomputed percentage 0-100 */
  pct?: number | null;
  className?: string;
  width?: number;
}) {
  let percentage = pct;
  if (percentage == null && score != null) {
    const parsed = parseScore(score);
    percentage = parsed && parsed.den > 0 ? Math.round((parsed.num / parsed.den) * 100) : null;
  }
  if (percentage == null) {
    return <span className={`text-[var(--text-dim)] tabular-nums ${className}`}>—</span>;
  }

  const tone =
    percentage >= 85
      ? "var(--emerald-pass)"
      : percentage >= 70
        ? "var(--amber-review)"
        : "var(--red-fail)";

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <span
        className="min-w-[34px] font-mono text-[12px] tabular-nums"
        style={{ color: tone }}
      >
        {percentage}%
      </span>
      <div
        className="overflow-hidden rounded-[2px] bg-[var(--bg-elev2)]"
        style={{ width, height: 4 }}
      >
        <div
          style={{
            width: `${Math.max(0, Math.min(100, percentage))}%`,
            height: "100%",
            background: tone,
          }}
        />
      </div>
    </div>
  );
}
