import "@testing-library/jest-dom/vitest";

// Stub Supabase env so `src/lib/supabase.ts` createClient() doesn't throw at
// import time. Real network never happens in unit tests — fetch is mocked
// per-test (see tests/unit/sentry-init.test.ts pattern).
process.env.NEXT_PUBLIC_SUPABASE_URL ??= "http://stub.invalid";
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??= "stub-anon-key";
