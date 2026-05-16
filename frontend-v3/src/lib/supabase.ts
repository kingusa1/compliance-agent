import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Pass-through lock: gotrue's default Web Locks implementation rejects the
// loser of any race ("Lock stolen" / "Lock not released within 5000ms").
// We're a single-tab single-reviewer SPA — no concurrent token refreshes
// across tabs — so the lock buys us nothing and just spams the console.
const passThroughLock = async <T>(_name: string, _acquireTimeout: number, fn: () => Promise<T>): Promise<T> => fn();

// Lazy singleton — we defer createClient() until first use so the module
// can be imported in the SSR bundle (for type-checking) without throwing
// "supabaseUrl is required" when NEXT_PUBLIC_* vars are unavailable to the
// Turbopack static-generation worker at build time.
let _supabaseInstance: SupabaseClient | null = null;
let _purgingStaleSession = false;

function getSupabaseClient(): SupabaseClient {
  if (_supabaseInstance) return _supabaseInstance;

  _supabaseInstance = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { auth: { lock: passThroughLock } },
  );

  // One-shot purge of the local refresh token when Supabase tells us it's
  // invalid. Without this, getSession() / autoRefreshToken loops on the
  // dead refresh token, spamming the console with `AuthApiError: Invalid
  // Refresh Token` on every page navigation. After purge we redirect to
  // /login so the reviewer signs in cleanly.
  if (typeof window !== "undefined") {
    _supabaseInstance.auth.onAuthStateChange((event) => {
      if (event === "TOKEN_REFRESHED" || event === "SIGNED_IN") {
        _purgingStaleSession = false;
      }
    });
    // Wrap getSession so the first stale-token error triggers a clean reset.
    const origGetSession = _supabaseInstance.auth.getSession.bind(_supabaseInstance.auth);
    _supabaseInstance.auth.getSession = (async () => {
      const result = await origGetSession();
      const errMsg = result.error?.message ?? "";
      if (
        !_purgingStaleSession &&
        /refresh token|not found/i.test(errMsg)
      ) {
        _purgingStaleSession = true;
        try {
          await _supabaseInstance!.auth.signOut();
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
    }) as typeof _supabaseInstance.auth.getSession;
  }

  return _supabaseInstance;
}

// Browser-safe client using the anon/publishable key.
// Server-side routes should use a separate admin client (service_role) and
// must never send that key to the browser.
//
// supabase is a Proxy that lazily initialises the real SupabaseClient on first
// property access.  This means the module can be statically imported at build
// time without createClient() throwing "supabaseUrl is required" when
// NEXT_PUBLIC_* vars are absent from the Turbopack SSR worker environment.
export const supabase = new Proxy({} as SupabaseClient, {
  get(_target, prop) {
    return (getSupabaseClient() as unknown as Record<string | symbol, unknown>)[prop];
  },
});

export async function getAccessToken(): Promise<string | null> {
  const { data } = await getSupabaseClient().auth.getSession();
  return data.session?.access_token ?? null;
}

export async function getCurrentUser() {
  try {
    const { data } = await getSupabaseClient().auth.getUser();
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
  await getSupabaseClient().auth.signOut();
}
