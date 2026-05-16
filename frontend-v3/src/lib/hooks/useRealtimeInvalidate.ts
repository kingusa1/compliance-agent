"use client";

/**
 * Supabase Realtime → TanStack Query invalidation glue.
 *
 * Path 3 / Wave 2 of the 2026-05-16 realtime overhaul.
 *
 * The 2026-05-16 audit caught that backend mutations (verdict submit,
 * claim/release, etc.) were going through an in-memory SSE pub/sub that
 * Tab B couldn't see. This hook closes the gap by subscribing to
 * Postgres CDC events via Supabase Realtime and invalidating the
 * matching TanStack Query keys whenever any subscriber sees a change.
 *
 * **Feature flag:** the hook is a NO-OP unless
 * ``NEXT_PUBLIC_USE_REALTIME === "1"``. This lets us ship the migration
 * (RLS + publication) without flipping the UI semantics — we can A/B
 * the realtime path against the existing SSE path before cutover.
 *
 * Usage:
 *   useRealtimeInvalidate("calls", [["queue"], ["admin", "tracker"]]);
 *   useRealtimeInvalidate("rejections", [["rejections"]], {
 *     filter: `confirmed_by=eq.${userId}`,
 *   });
 *
 * Auto-cleans: removes the channel on unmount.
 *
 * Multiple components can mount the same `(table, filter)` pair safely
 * — Supabase JS pool/dedupes channels by name, and each hook owns its
 * own removeChannel() call. Channel name is namespaced with the filter
 * so different filters get different channels.
 */

import { useEffect } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { supabase } from "@/lib/supabase";

export type RealtimeInvalidateOptions = {
  /**
   * Postgres-changes filter — server-side reduces the broadcast traffic.
   * Example: `"call_id=eq.abc-123"` or `"user_id=eq.42"`.
   *
   * Filters are validated against the table's columns by Supabase
   * Realtime — invalid filters silently never deliver events, so test
   * each new filter before relying on it.
   */
  filter?: string;
  /**
   * Which events to listen for. Default `"*"` (INSERT + UPDATE + DELETE).
   * Pass a narrower event when you know e.g. only INSERTs matter for the
   * key being invalidated.
   */
  event?: "*" | "INSERT" | "UPDATE" | "DELETE";
  /**
   * Schema name. Defaults to "public". The compliance-agent app uses the
   * public schema for everything.
   */
  schema?: string;
};

function isRealtimeEnabled(): boolean {
  return process.env.NEXT_PUBLIC_USE_REALTIME === "1";
}

export function useRealtimeInvalidate(
  table: string,
  invalidateKeys: readonly (readonly unknown[])[],
  options: RealtimeInvalidateOptions = {},
): void {
  const qc = useQueryClient();
  const { filter, event = "*", schema = "public" } = options;

  useEffect(() => {
    if (!isRealtimeEnabled()) return;
    if (typeof window === "undefined") return; // SSR safety

    // Channel name is namespaced with table + filter so two pages
    // listening to the same table with different filters don't collide.
    const channelName = `realtime:${schema}:${table}${filter ? `:${filter}` : ""}`;

    let cancelled = false;
    const channel = supabase
      .channel(channelName)
      .on(
        // The eslint disable is because Supabase JS types `postgres_changes`
        // as a string literal but TypeScript sees it as too narrow when used
        // as a generic event name across our usage.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        "postgres_changes" as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { event, schema, table, ...(filter ? { filter } : {}) } as any,
        () => {
          if (cancelled) return;
          for (const key of invalidateKeys) {
            qc.invalidateQueries({ queryKey: key as unknown[] });
          }
        },
      )
      .subscribe((status) => {
        // The status callback fires for SUBSCRIBED, CHANNEL_ERROR,
        // TIMED_OUT, and CLOSED. In production we want to surface
        // CHANNEL_ERROR + TIMED_OUT to Sentry but silent in normal
        // operation; for now console.warn so issues are visible in
        // DevTools during the rollout.
        if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          // eslint-disable-next-line no-console
          console.warn(`[useRealtimeInvalidate] ${channelName} → ${status}`);
        }
      });

    return () => {
      cancelled = true;
      void supabase.removeChannel(channel);
    };
    // We deliberately stringify-compare `invalidateKeys` via JSON to keep
    // the effect stable across renders that pass a fresh array literal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    table,
    filter,
    event,
    schema,
    JSON.stringify(invalidateKeys),
  ]);
}
