"use client";

/**
 * EmptyState — centered empty/done callout used by queue (empty state)
 * and customers (no rows) — see screens/queue.jsx + screens/customers.jsx.
 */
import { type ReactNode } from "react";

export function EmptyState({
  icon,
  title,
  body,
  actions,
  iconTone = "muted",
}: {
  icon?: ReactNode;
  title: string;
  body?: string;
  actions?: ReactNode;
  iconTone?: "emerald" | "muted";
}) {
  return (
    <div style={{ height: "100%", display: "grid", placeItems: "center", padding: 40 }}>
      <div style={{ textAlign: "center", maxWidth: 340 }}>
        {icon && (
          <div
            style={{
              width: 48,
              height: 48,
              margin: "0 auto 16px",
              borderRadius: 12,
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              display: "grid",
              placeItems: "center",
              color: iconTone === "emerald" ? "var(--emerald)" : "var(--text-muted)",
            }}
          >
            {icon}
          </div>
        )}
        <div
          style={{
            fontSize: 18,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          {title}
        </div>
        {body && (
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 6 }}>{body}</div>
        )}
        {actions && (
          <div style={{ marginTop: 16, display: "flex", justifyContent: "center", gap: 8 }}>
            {actions}
          </div>
        )}
      </div>
    </div>
  );
}
