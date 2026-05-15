"use client";

/**
 * ScreenFrame — main content frame with the global Sidebar already mounted.
 * Designed to wrap any (reviewer)/(admin) page so the sidebar is consistent.
 */
import { type ReactNode } from "react";

import { Sidebar } from "@/components/Sidebar";
import { useCallEvents } from "@/lib/hooks/useCallEvents";

export function ScreenFrame({ children }: { children: ReactNode }) {
  // 2026-05-16: subscribe to the global SSE feed once at the layout level
  // so every list page (queue / tracker / calls / dashboard / customers /
  // deals / intelligence) refreshes within a frame of a backend event,
  // without per-page polling. Single EventSource per browser session.
  useCallEvents("*");

  return (
    <div
      style={{
        display: "flex",
        minHeight: "100vh",
        background: "var(--bg-canvas)",
        color: "var(--text-primary)",
        fontFamily: "var(--font-sans)",
        fontSize: 14,
        lineHeight: 1.5,
        letterSpacing: "-0.005em",
      }}
    >
      <Sidebar />
      <main
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          background: "var(--bg-elev1)",
          position: "relative",
          minHeight: "100vh",
        }}
      >
        {children}
      </main>
    </div>
  );
}
