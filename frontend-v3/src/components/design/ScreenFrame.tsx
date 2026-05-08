"use client";

/**
 * ScreenFrame — main content frame with the global Sidebar already mounted.
 * Designed to wrap any (reviewer)/(admin) page so the sidebar is consistent.
 */
import { type ReactNode } from "react";

import { Sidebar } from "@/components/Sidebar";

export function ScreenFrame({ children }: { children: ReactNode }) {
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
