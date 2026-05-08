"use client";

/**
 * Avatar — initials in a tone-tinted circle. From hifi/tokens-hifi.jsx.
 */

export type AvatarTone = "neutral" | "emerald" | "blue" | "amber" | "violet";

const TONES: Record<AvatarTone, { bg: string; fg: string; border: string }> = {
  neutral: { bg: "var(--bg-elev3)",        fg: "var(--text-primary)", border: "var(--border-subtle)" },
  emerald: { bg: "var(--emerald-bg-strong)", fg: "var(--emerald-400)", border: "var(--emerald-border)" },
  blue:    { bg: "var(--blue-bg)",         fg: "var(--blue)",         border: "var(--blue-border)" },
  amber:   { bg: "var(--amber-bg)",        fg: "var(--amber-400)",    border: "var(--amber-border)" },
  violet:  { bg: "var(--violet-bg)",       fg: "var(--violet)",       border: "var(--violet-border)" },
};

export function Avatar({
  name = "S",
  size = 24,
  tone = "neutral",
}: {
  name?: string;
  size?: number;
  tone?: AvatarTone;
}) {
  const initials = name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  const t = TONES[tone];
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: t.bg,
        border: `1px solid ${t.border}`,
        color: t.fg,
        display: "grid",
        placeItems: "center",
        fontSize: size <= 24 ? 10 : size <= 32 ? 12 : 14,
        fontWeight: 600,
        letterSpacing: "0.02em",
        flexShrink: 0,
        fontFamily: "var(--font-sans)",
      }}
    >
      {initials}
    </div>
  );
}
