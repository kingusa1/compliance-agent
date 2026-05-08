"use client";

/**
 * FilterChip — toggleable filter pill from screens/queue.jsx.
 */
import { type ReactNode } from "react";

export function FilterChip({
  active,
  count,
  onClick,
  children,
}: {
  active?: boolean;
  count?: number | null;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        height: 26,
        padding: "0 10px",
        fontSize: 12,
        fontWeight: 500,
        borderRadius: 6,
        background: active ? "var(--bg-elev3)" : "transparent",
        color: active ? "var(--text-primary)" : "var(--text-muted)",
        border: `1px solid ${active ? "var(--border-strong)" : "transparent"}`,
        cursor: "pointer",
        whiteSpace: "nowrap",
        fontFamily: "inherit",
      }}
    >
      {children}
      {count != null && (
        <span
          style={{
            fontSize: 11,
            color: "var(--text-dim)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {count}
        </span>
      )}
    </button>
  );
}
