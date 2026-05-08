// Shared score helpers.
//
// The pipeline persists the per-call score as a "passed/total" fraction
// string (e.g. "0/3", "2/3"). UI displays it as a rounded percentage out
// of 100 ("0%", "67%") — the fraction stays in the DB so anything that
// needs the raw counts can still parse it.

export type ParsedScore = { num: number; den: number };

export function parseScore(score: string | number | null | undefined): ParsedScore | null {
  if (score == null) return null;
  if (typeof score === "number") {
    if (!Number.isFinite(score)) return null;
    // Already a percentage in 0-100; treat as N/100.
    return { num: Math.round(score), den: 100 };
  }
  const m = score.match(/^(\d+)\s*\/\s*(\d+)$/);
  if (!m) return null;
  const num = parseInt(m[1], 10);
  const den = parseInt(m[2], 10);
  if (!Number.isFinite(num) || !Number.isFinite(den)) return null;
  return { num, den };
}

export function formatScorePercent(
  score: string | number | null | undefined,
  fallback = "—",
): string {
  const p = parseScore(score);
  if (!p) return fallback;
  if (p.den <= 0) return "0%";
  return `${Math.round((p.num / p.den) * 100)}%`;
}
