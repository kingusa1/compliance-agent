"use client";

/**
 * useDebouncedValue — defers updates to `value` until it has been
 * stable for `delayMs` ms. Used by search inputs so we don't fire a
 * query per keystroke. Caller still owns the unbounced state for the
 * <input> binding; the returned debounced value drives the query key.
 *
 * Example:
 *   const [q, setQ] = useState("");
 *   const debouncedQ = useDebouncedValue(q, 300);
 *   useAdminCustomersQuery({ q: debouncedQ || undefined });
 */
import { useEffect, useState } from "react";

export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
