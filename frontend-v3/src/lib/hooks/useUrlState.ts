"use client";

/**
 * useUrlState — small wrapper around Next 16's useSearchParams +
 * router.replace that exposes a get/set pair for one query param at
 * a time. Used by /queue (filter chips), /customers + /deals
 * (search + offset), /compliant + /non-compliant (offset),
 * /findings (severity).
 *
 * - get(key) returns the current value (or "" if absent), so callers
 *   can default in one line: `const filter = get("filter") || "all"`.
 * - set(key, value) preserves all other params; passing null/""/undefined
 *   removes the key. Uses router.replace() with scroll:false so the page
 *   doesn't jump on filter changes — back/forward + refresh still
 *   restore the URL faithfully.
 *
 * NOTE: useSearchParams is a Client API in Next 16. Pages that use it
 * either need to be fully client components (the case here — every
 * /admin and /reviewer page already starts with "use client") or wrap
 * the inner consumer in <Suspense>. We don't add Suspense here because
 * every consumer is a client tree behind AuthGuard already.
 */
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useCallback } from "react";

export function useUrlState() {
  const router = useRouter();
  const params = useSearchParams();
  const pathname = usePathname();

  const get = useCallback(
    (key: string): string => params?.get(key) ?? "",
    [params],
  );

  const set = useCallback(
    (key: string, value: string | number | null | undefined) => {
      const sp = new URLSearchParams(params?.toString() ?? "");
      if (value === null || value === undefined || value === "") {
        sp.delete(key);
      } else {
        sp.set(key, String(value));
      }
      const qs = sp.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [params, router, pathname],
  );

  /**
   * setMany — update several params atomically (one router.replace).
   * Useful when changing the filter should also reset offset to 0.
   */
  const setMany = useCallback(
    (updates: Record<string, string | number | null | undefined>) => {
      const sp = new URLSearchParams(params?.toString() ?? "");
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === undefined || value === "") {
          sp.delete(key);
        } else {
          sp.set(key, String(value));
        }
      }
      const qs = sp.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [params, router, pathname],
  );

  return { get, set, setMany };
}
