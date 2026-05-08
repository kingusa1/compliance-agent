/**
 * FlagBadge — severity pill for findings + flagged transcript lines.
 *
 * Maps backend severity strings to a tone:
 *   HIGH   → red    (var(--red-fail))
 *   MEDIUM → amber  (var(--amber-review))
 *   LOW    → blue   (var(--blue-coaching))
 *   else   → muted neutral
 *
 * Lower-case input is normalised. Unknown severity falls back to neutral
 * so the UI never throws on a new backend severity bucket.
 */
import { Badge } from "@/components/ui/badge";

type Severity = "HIGH" | "MEDIUM" | "LOW" | string;

export function FlagBadge({ severity, className = "" }: { severity: Severity; className?: string }) {
  const s = String(severity || "").toUpperCase();
  if (s === "HIGH") {
    return (
      <Badge className={`border-red-500/30 bg-red-500/10 text-[var(--red-fail)] ${className}`}>
        High
      </Badge>
    );
  }
  if (s === "MEDIUM") {
    return (
      <Badge
        className={`border-amber-500/30 bg-amber-500/10 text-[var(--amber-review)] ${className}`}
      >
        Medium
      </Badge>
    );
  }
  if (s === "LOW") {
    return (
      <Badge
        className={`border-blue-500/30 bg-blue-500/10 text-[var(--blue-coaching)] ${className}`}
      >
        Low
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className={className}>
      {severity || "—"}
    </Badge>
  );
}
