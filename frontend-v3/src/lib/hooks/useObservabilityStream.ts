"use client";

/**
 * useObservabilityStream — subscribe to the backend's SSE feed and
 * invalidate the runs query on each event.
 *
 * Backend emits `event: run.started|run.finished` with a JSON payload, and
 * `:keep-alive` comments every 5s. This hook only invalidates the cache;
 * it does not parse the payload. Reconnects with exponential backoff (3s
 * → 30s max) on error so transient blips don't stall the UI.
 *
 * EventSource cannot inject Authorization headers; the SSE endpoint is
 * intentionally unauthenticated on the backend. If a 401 ever appears
 * we fall back to refetchInterval polling on the runs query, which is
 * already configured at staleTime: 5_000.
 */
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { obsKeys } from "@/lib/queries/observability";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export function useObservabilityStream(enabled = true) {
  const qc = useQueryClient();

  useEffect(() => {
    if (!enabled) return;

    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let cancelled = false;

    function invalidate() {
      qc.invalidateQueries({ queryKey: ["obs", "runs"] });
      qc.invalidateQueries({ queryKey: obsKeys.stuck() });
    }

    function connect() {
      if (cancelled) return;
      try {
        es = new EventSource(`${API_URL}/api/observability/stream`);
      } catch {
        scheduleReconnect();
        return;
      }

      es.onopen = () => {
        attempt = 0;
      };

      // Generic message + named events both invalidate the runs cache.
      es.onmessage = invalidate;
      es.addEventListener("run.started", invalidate);
      es.addEventListener("run.finished", invalidate);

      es.onerror = () => {
        es?.close();
        es = null;
        scheduleReconnect();
      };
    }

    function scheduleReconnect() {
      if (cancelled) return;
      attempt += 1;
      const wait = Math.min(30_000, 3_000 * Math.pow(1.5, attempt - 1));
      retryTimer = setTimeout(connect, wait);
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
      es = null;
    };
  }, [enabled, qc]);
}
