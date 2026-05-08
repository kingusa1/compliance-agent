/**
 * Tracker AI/HUMAN provenance badge.
 *
 *  AI_PENDING       — amber, clock SVG, "AI · pending"
 *  HUMAN_CONFIRMED  — emerald, check SVG, "HUMAN ✓"
 *  HUMAN_OVERRIDDEN — blue, edit-pencil SVG, "HUMAN ✎"
 *
 * Compliant/non-compliant pages exclude AI_PENDING — only confirmed or
 * overridden rows count toward those totals. The amber badge in the
 * Tracker is a reviewer call-to-action.
 */

type VerdictState = "AI_PENDING" | "HUMAN_CONFIRMED" | "HUMAN_OVERRIDDEN";

const STYLES: Record<
  VerdictState,
  { label: string; classes: string; icon: React.ReactNode }
> = {
  AI_PENDING: {
    label: "AI · pending",
    classes: "border-amber-300 bg-amber-50 text-amber-900",
    icon: (
      <svg
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4l3 3" />
      </svg>
    ),
  },
  HUMAN_CONFIRMED: {
    label: "HUMAN ✓",
    classes: "border-emerald-300 bg-emerald-50 text-emerald-900",
    icon: (
      <svg
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M20 6L9 17l-5-5" />
      </svg>
    ),
  },
  HUMAN_OVERRIDDEN: {
    label: "HUMAN ✎",
    classes: "border-blue-300 bg-blue-50 text-blue-900",
    icon: (
      <svg
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4z" />
      </svg>
    ),
  },
};

export function VerdictBadge({
  state,
}: {
  state: VerdictState | null | undefined;
}) {
  // Pre-migration rows or rows missing the field default to AI_PENDING so
  // reviewers see the amber CTA on legacy data instead of a blank cell.
  const s = STYLES[state ?? "AI_PENDING"];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${s.classes}`}
      title={s.label}
    >
      {s.icon}
      {s.label}
    </span>
  );
}
