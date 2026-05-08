"use client";

/**
 * Smooth audio waveform — sine-based gradient bars.
 * Ported from design/handoff-bundle/project/hifi/tokens-hifi.jsx HFWaveform.
 */

export function Waveform({
  played = 30,
  total = 120,
  height = 40,
  flagsAt = [],
  showPlayhead = false,
  playedPct = 0,
}: {
  played?: number;
  total?: number;
  height?: number;
  flagsAt?: number[];
  showPlayhead?: boolean;
  playedPct?: number;
}) {
  return (
    <div
      style={{
        position: "relative",
        height,
        width: "100%",
        padding: "4px 0",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 1.5, height: "100%", width: "100%" }}>
        {Array.from({ length: total }).map((_, i) => {
          const env = Math.exp(-Math.pow((i - total / 2) / (total * 0.4), 2));
          const v =
            (Math.sin(i * 0.21) * 0.5 +
              Math.sin(i * 0.07 + 1.3) * 0.3 +
              Math.sin(i * 0.43) * 0.2) *
            env;
          const h = Math.max(2, Math.abs(v) * height * 0.95 + 3);
          const isPlayed = i < played;
          return (
            <div
              key={i}
              style={{
                flex: 1,
                height: h,
                background: isPlayed
                  ? "linear-gradient(180deg, var(--emerald-400), var(--emerald))"
                  : "var(--border-strong)",
                borderRadius: 1.5,
                minWidth: 1.5,
                opacity: isPlayed ? 1 : 0.85,
              }}
            />
          );
        })}
      </div>
      {showPlayhead && (
        <div
          style={{
            position: "absolute",
            left: `${playedPct}%`,
            top: 0,
            bottom: 0,
            width: 2,
            background: "var(--emerald)",
            boxShadow: "0 0 0 1px var(--bg-canvas), 0 0 8px rgba(16,185,129,0.6)",
            borderRadius: 1,
          }}
        >
          <div
            style={{
              position: "absolute",
              top: -2,
              left: "50%",
              transform: "translateX(-50%)",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--emerald)",
              boxShadow: "0 0 0 2px var(--bg-canvas)",
            }}
          />
        </div>
      )}
      {flagsAt.map((p, i) => (
        <div
          key={`flag-${i}`}
          style={{
            position: "absolute",
            left: `${p}%`,
            top: -2,
            width: 2,
            height: 8,
            background: "var(--red)",
            borderRadius: 1,
          }}
        />
      ))}
    </div>
  );
}
