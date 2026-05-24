"use client";

/**
 * /settings — H1 + 4 tabs (Model | Transcription | Density | Account).
 *
 * Each tab renders its own self-contained component (ModelSettings,
 * TranscriptionSettings, DensityToggle, AccountTab).
 */
import { useState } from "react";

import { ModelSettings } from "./ModelSettings";
import { TranscriptionSettings } from "./TranscriptionSettings";
import { ObservabilityTab } from "./ObservabilityTab";
import { DensityToggle } from "./DensityToggle";
// 2026-05-24 wiring audit HIGH — render the real Account tab instead of
// a placeholder. AccountTab.tsx has been on disk with a working sign-out
// button; the prior PlaceholderCard was a stale stub.
import { AccountTab } from "./AccountTab";

type Tab = "model" | "transcription" | "density" | "account" | "observability";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("model");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      <div
        style={{
          padding: "16px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            fontSize: 19,
            fontWeight: 600,
            letterSpacing: "-0.018em",
            margin: 0,
            color: "var(--text-primary)",
          }}
        >
          Settings
        </h1>
      </div>

      {/* Tab strip */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--border-subtle)",
          paddingLeft: 16,
          flexShrink: 0,
        }}
      >
        {(
          [
            { key: "model", label: "Model" },
            { key: "transcription", label: "Transcription" },
            { key: "observability", label: "Observability" },
            { key: "density", label: "Density" },
            { key: "account", label: "Account" },
          ] as { key: Tab; label: string }[]
        ).map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              data-testid={`settings-tab-${t.key}`}
              style={{
                padding: "12px 14px",
                fontSize: 13,
                fontWeight: 500,
                color: active ? "var(--text-primary)" : "var(--text-muted)",
                borderBottom: `2px solid ${active ? "var(--emerald)" : "transparent"}`,
                marginBottom: -1,
                cursor: "pointer",
                background: "transparent",
                border: "none",
                fontFamily: "inherit",
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 32 }} className="ca-scroll">
        <div style={{ maxWidth: 720 }}>
          {tab === "model" && (
            <div className="space-y-6">
              <div>
                <h2 className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">
                  Compliance LLM
                </h2>
                <p className="mt-1 text-[13px] text-[var(--text-muted)]">
                  The model used to score checkpoints and generate reviewer feedback.
                </p>
              </div>
              <ModelSettings />
            </div>
          )}
          {tab === "transcription" && (
            <div className="space-y-6">
              <div>
                <h2 className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">
                  Transcription
                </h2>
                <p className="mt-1 text-[13px] text-[var(--text-muted)]">
                  Speech-to-text providers powering call ingestion.
                </p>
              </div>
              <TranscriptionSettings />
            </div>
          )}
          {tab === "observability" && <ObservabilityTab />}
          {tab === "density" && <DensityToggle />}
          {tab === "account" && <AccountTab />}
        </div>
      </div>
    </div>
  );
}

