import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone bundle so production Docker image stays small + VPS
  // doesn't need npm at runtime. Outputs server.js + minimal
  // node_modules at .next/standalone.
  output: "standalone",
  // Reverse-proxy /api/* to the backend so reviewer browsers only
  // open one HTTP connection (port 3004). Avoids the flaky direct
  // connection to backend port 8001 on the VPS (firewall/routing
  // packet drops external requests intermittently).
  async rewrites() {
    const backend = process.env.BACKEND_INTERNAL_URL ?? "http://compliance-backend:8001";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

// Wave 2 / T10 — Sentry build-time wrapper.
// `disableServerWebpackPlugin` / `disableClientWebpackPlugin` flag-gate the
// source-map upload step on `SENTRY_AUTH_TOKEN` so local dev and CI without a
// GlitchTip token still build cleanly.
export default withSentryConfig(nextConfig, {
  silent: true,
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  disableServerWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
  disableClientWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
});
