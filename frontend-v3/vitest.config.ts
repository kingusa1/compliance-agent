/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Vitest config for component-level unit tests under `tests/unit/`.
 *
 * - jsdom environment so React Testing Library can mount components.
 * - `@/...` alias matches the Next/TypeScript path mapping.
 * - `tests/setup.ts` wires `@testing-library/jest-dom`.
 * - We exclude `tests/e2e/` (those run under Playwright).
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/unit/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["tests/e2e/**", "node_modules/**", ".next/**"],
    css: false,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
