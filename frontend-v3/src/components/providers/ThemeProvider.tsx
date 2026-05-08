"use client";

/**
 * Dark-only stub per UX-D05. We force `class="dark"` on <html> so
 * Tailwind's class-based dark mode variants apply globally without
 * a theme toggle. v3.1 will swap this for next-themes when the
 * light mode design system is ready.
 */
import { useEffect } from "react";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);
  return <>{children}</>;
}
