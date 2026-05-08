import * as Sentry from "@sentry/nextjs";

/**
 * Wave 2 / T10 — Node server-side Sentry init.
 *
 * Env-gated: only attaches the SDK when `SENTRY_DSN` is set. Used by the
 * Next.js Node runtime (route handlers, server components, middleware on Node).
 */
const dsn = process.env.SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 0.1,
  });
}
