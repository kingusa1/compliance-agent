"use client";

/**
 * Pill — semantic dot + label badge.
 * Ported from design/handoff-bundle/project/shared.jsx (Pill) and
 * design/handoff-bundle/project/hifi/tokens-hifi.jsx (HFPill).
 */
import { type CSSProperties, type ReactNode } from "react";

export type PillTone =
  | "neutral"
  | "emerald"
  | "red"
  | "amber"
  | "blue"
  | "violet"
  | "solid";

const TONE: Record<PillTone, { bg: string; fg: string; border: string; dot: string }> = {
  neutral: { bg: "var(--bg-elev3)",     fg: "var(--text-muted)",  border: "var(--border-subtle)",  dot: "var(--text-dim)" },
  emerald: { bg: "var(--emerald-bg)",   fg: "var(--emerald-400)", border: "var(--emerald-border)", dot: "var(--emerald)" },
  red:     { bg: "var(--red-bg)",       fg: "var(--red)",         border: "var(--red-border)",     dot: "var(--red)" },
  amber:   { bg: "var(--amber-bg)",     fg: "var(--amber-400)",   border: "var(--amber-border)",   dot: "var(--amber)" },
  blue:    { bg: "var(--blue-bg)",      fg: "var(--blue)",        border: "var(--blue-border)",    dot: "var(--blue)" },
  violet:  { bg: "var(--violet-bg)",    fg: "var(--violet)",      border: "var(--violet-border)",  dot: "var(--violet)" },
  solid:   { bg: "var(--text-primary)", fg: "#0a0a0b",            border: "var(--text-primary)",   dot: "#0a0a0b" },
};

export function Pill({
  children,
  tone = "neutral",
  dot = false,
  mono = false,
  style,
}: {
  children: ReactNode;
  tone?: PillTone;
  dot?: boolean;
  mono?: boolean;
  style?: CSSProperties;
}) {
  const t = TONE[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "2px 8px",
        fontSize: 11,
        fontWeight: 500,
        borderRadius: "var(--radius-sm)",
        background: t.bg,
        color: t.fg,
        border: `1px solid ${t.border}`,
        whiteSpace: "nowrap",
        lineHeight: 1.5,
        letterSpacing: "0.01em",
        fontFamily: mono ? "var(--font-mono)" : "inherit",
        fontVariantNumeric: mono ? "tabular-nums" : undefined,
        ...style,
      }}
    >
      {dot && (
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: t.dot,
            flexShrink: 0,
          }}
        />
      )}
      {children}
    </span>
  );
}
