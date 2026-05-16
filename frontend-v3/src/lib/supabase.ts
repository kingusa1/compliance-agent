import { createClient } from "@supabase/supabase-js";

// Pass-through lock: gotrue's default Web Locks implementation rejects the
// loser of any race ("Lock stolen" / "Lock not released within 5000ms").
// We're a single-tab single-reviewer SPA — no concurrent token refreshes
// across tabs — so the lock buys us nothing and just spams the console.
const passThroughLock = async <T>(_name: string, _acquireTimeout: number, fn: () => Promise<T>): Promise<T> => fn();

// Browser-safe client using the anon/publishable key.
// Server-side routes should use a separate admin client (service_role) and
// must never send that key to the browser.
//
// Guard against missing env vars during Next.js static pre-rendering — the
// build environment may not have NEXT_PUBLIC_* vars injected for SSR workers
// even when they're configured in the Vercel project.  A placeholder client
// is safe at build time because "use client" pages never run server-side.
const _supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "https://placeholder.supabase.co";
const _supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "placeholder-anon-key";
export const supabase = createClient(
  _supabaseUrl,
  _supabaseKey,
  { auth: { lock: passThroughLock } },
);

// One-shot purge of the local refresh token when Supabase tells us it's
// invalid. Without this, getSession() / autoRefreshToken loops on the
// dead refresh token, spamming the console with `AuthApiError: Invalid
// Refresh Token` on every page navigation. After purge we redirect to
// /login so the reviewer signs in cleanly.
let _purgingStaleSession = false;
if (typeof window !== "undefined") {
  supabase.auth.onAuthStateChange((event) => {
    if (event === "TOKEN_REFRESHED" || event === "SIGNED_IN") {
      _purgingStaleSession = false;
    }
  });
  // Wrap getSession so the first stale-token error triggers a clean reset.
  const origGetSession = supabase.auth.getSession.bind(supabase.auth);
  supabase.auth.getSession = (async () => {
    const result = await origGetSession();
    const errMsg = result.error?.message ?? "";
    if (
      !_purgingStaleSession &&
      /refresh token|not found/i.test(errMsg)
    ) {
      _purgingStaleSession = true;
      try {
        await supabase.auth.signOut();
      } catch {
        // ignore — we're about to wipe localStorage anyway
      }
      Object.keys(window.localStorage)
        .filter((k) => k.startsWith("sb-"))
        .forEach((k) => window.localStorage.removeItem(k));
      if (window.location.pathname !== "/login") {
        window.location.replace("/login");
      }
    }
    return result;
  }) as typeof supabase.auth.getSession;
}

export async function getAccessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function getCurrentUser() {
  try {
    const { data } = await supabase.auth.getUser();
    return data.user;
  } catch (e: unknown) {
    // React Strict Mode double-mounts fire parallel getUser() calls; whichever
    // loses the gotrue internal lock race rejects with NavigatorLockAcquireTimeoutError.
    // Session is still valid — just return null and let the caller refetch.
    if (e instanceof Error && /Lock|AcquireTimeout|stolen/i.test(e.message)) {
      return null;
    }
    throw e;
  }
}

export async function signOut() {
  await supabase.auth.signOut();
}
