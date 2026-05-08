"use client";

import { useEffect, useState } from "react";

type Density = "compact" | "comfortable" | "spacious";

const OPTIONS: { key: Density; label: string; subtitle: string; description: string }[] = [
  {
    key: "compact",
    label: "Compact",
    subtitle: "Tightest row height",
    description:
      "More rows on screen, dense data scan. Best for power users reviewing dozens of calls per session.",
  },
  {
    key: "comfortable",
    label: "Comfortable",
    subtitle: "Balanced (default)",
    description:
      "Readable spacing without sacrificing row count. Recommended starting point.",
  },
  {
    key: "spacious",
    label: "Spacious",
    subtitle: "Widest row height",
    description:
      "Easy scan, lower visual fatigue. Best on large monitors and during long-form review sessions.",
  },
];

function applyToDocument(d: Density) {
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-density", d);
  }
}

/**
 * DensityToggle — client-side preference for table row density.
 * Persists via localStorage, sets `data-density` on <html>, and
 * broadcasts a `v3:density-changed` CustomEvent.
 *
 * The actual row-spacing override lives in `globals.css` —
 * `[data-density="compact"]` etc. scale padding via CSS variables.
 */
export function DensityToggle() {
  const [density, setDensity] = useState<Density>("comfortable");

  useEffect(() => {
    const saved = (localStorage.getItem("v3:density") as Density | null) ?? "comfortable";
    setDensity(saved);
    applyToDocument(saved);
  }, []);

  function pick(d: Density) {
    setDensity(d);
    localStorage.setItem("v3:density", d);
    applyToDocument(d);
    window.dispatchEvent(new CustomEvent("v3:density-changed", { detail: d }));
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-[18px] font-semibold tracking-tight">Table density</h2>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Adjust row spacing across all tables. Persists in this browser.
        </p>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 12,
        }}
      >
        {OPTIONS.map((opt) => {
          const active = density === opt.key;
          return (
            <button
              key={opt.key}
              type="button"
              onClick={() => pick(opt.key)}
              style={{
                textAlign: "left",
                padding: 14,
                borderRadius: 8,
                border: `1px solid ${active ? "var(--emerald)" : "var(--border-subtle)"}`,
                background: active ? "var(--surface-2)" : "var(--surface-1)",
                cursor: "pointer",
                fontFamily: "inherit",
                color: "var(--text-primary)",
              }}
              aria-pressed={active}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>{opt.label}</span>
                {active && (
                  <span style={{ fontSize: 11, color: "var(--emerald)", fontWeight: 600 }}>SELECTED</span>
                )}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>{opt.subtitle}</div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0, lineHeight: 1.4 }}>
                {opt.description}
              </p>
            </button>
          );
        })}
      </div>
      <p className="text-[11px] text-[var(--text-muted)]">
        Saved in <code>localStorage[&quot;v3:density&quot;]</code>. Tables react via the
        <code> v3:density-changed</code> event + <code>data-density</code> attribute on
        <code>&lt;html&gt;</code>.
      </p>
    </div>
  );
}
