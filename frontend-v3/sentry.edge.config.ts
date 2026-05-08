import * as Sentry from "@sentry/nextjs";

/**
 * Wave 2 / T10 — Edge runtime Sentry init.
 *
 * Env-gated: only attaches the SDK when `SENTRY_DSN` is set. Used by the
 * Next.js Edge runtime (middleware on Edge, edge route handlers).
 */
const dsn = process.env.SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 0.1,
  });
}
