import * as Sentry from "@sentry/nextjs";

/**
 * Wave 2 / T10 — Browser Sentry init.
 *
 * Env-gated: when `NEXT_PUBLIC_SENTRY_DSN` is unset (dev / CI / local docker),
 * this module loads as a no-op so the bundle still compiles without GlitchTip
 * credentials. The plan deliberately keeps this in the older
 * `sentry.client.config.ts` file (vs. `instrumentation-client.ts`) because env
 * gating is straightforward here and works against Next.js 13 → 16.
 */
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment:
      process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
  });
}
