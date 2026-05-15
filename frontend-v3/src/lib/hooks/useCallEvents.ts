"use client";

/**
 * useCallEvents — subscribe to the backend's per-call (or global) SSE feed
 * and invalidate the relevant React Query keys whenever a pipeline event
 * fires (`queued`, `transcribe_done`, `segments_detected`, `score_ready`,
 * `finalized`, `step_started`, `step_ok`, `step_err`, `failed`).
 *
 * Replaces the aggressive `refetchInterval: 3_000` polling that was on
 * `useCallDetailQuery` (and which caused the 2026-05-16 audio-reset bug).
 *
 * Two modes:
 *
 *   useCallEvents("*")       — global feed. Used by list pages (queue,
 *                              tracker, all-calls, dashboard) so any new
 *                              upload or status change refreshes the list
 *                              within a frame, without polling.
 *
 *   useCallEvents(callId)    — per-call feed. Used by the call detail page
 *                              so each pipeline step transition refreshes
 *                              the call/checkpoints/flags queries.
 *
 * Backend endpoints:
 *   GET /api/calls/events
 *   GET /api/calls/{call_id}/events
 *
 * Reconnects with exponential backoff (1s → 30s max) on transient errors.
 * EventSource cannot inject Authorization headers, so the SSE routes are
 * unauthenticated server-side (same as /api/observability/stream). If a
 * 401 ever appears we fall back to whatever poll the underlying query
 * already has.
 */
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type EventPayload = {
  event_type: string;
  call_id: string;
  ts: string;
  payload: Record<string, unknown>;
};

export function useCallEvents(scope: string | null | undefined, enabled = true): void {
  const qc = useQueryClient();

  useEffect(() => {
    if (!enabled || !scope) return;
    const url = scope === "*"
      ? `${API_URL}/api/calls/events`
      : `${API_URL}/api/calls/${encodeURIComponent(scope)}/events`;

    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let cancelled = false;

    function invalidate(callId: string, eventType: string): void {
      if (scope === "*") {
        // List pages: queue (reviewer), tracker, calls (admin), customers,
        // deals, dashboard, intelligence. Cache keys span TWO naming
        // conventions: the reviewer keys (top-level "queue" / "calls" /
        // etc.) and the admin keys ("admin" prefix from lib/queries/admin.ts).
        qc.invalidateQueries({ queryKey: ["queue"] });
        qc.invalidateQueries({ queryKey: ["calls"] });
        qc.invalidateQueries({ queryKey: ["tracker"] });
        qc.invalidateQueries({ queryKey: ["dashboard"] });
        qc.invalidateQueries({ queryKey: ["intelligence"] });
        qc.invalidateQueries({ queryKey: ["customers"] });
        qc.invalidateQueries({ queryKey: ["deals"] });
        qc.invalidateQueries({ queryKey: ["admin"] });
        if (callId) {
          qc.invalidateQueries({ queryKey: ["call", callId] });
        }
      } else {
        qc.invalidateQueries({ queryKey: ["call", scope] });
        if (eventType === "finalized" || eventType === "score_ready") {
          qc.invalidateQueries({ queryKey: ["queue"] });
          qc.invalidateQueries({ queryKey: ["tracker"] });
          qc.invalidateQueries({ queryKey: ["intelligence"] });
          qc.invalidateQueries({ queryKey: ["admin"] });
        }
      }
    }

    function handle(raw: MessageEvent<string>): void {
      let parsed: EventPayload | null = null;
      try {
        parsed = JSON.parse(raw.data) as EventPayload;
      } catch {
        return;
      }
      if (!parsed) return;
      invalidate(parsed.call_id, parsed.event_type);
    }

    function connect(): void {
      if (cancelled) return;
      try {
        es = new EventSource(url);
      } catch {
        scheduleReconnect();
        return;
      }

      es.onopen = () => {
        attempt = 0;
      };

      // Listen for every named event type we publish from the backend.
      const named = [
        "queued",
        "step_started",
        "step_ok",
        "step_err",
        "transcribe_done",
        "detect_metadata_done",
        "segments_detected",
        "checkpoints_scored",
        "score_ready",
        "finalized",
        "failed",
      ] as const;
      for (const evt of named) {
        es.addEventListener(evt, handle as EventListener);
      }
      es.onmessage = handle;

      es.onerror = () => {
        es?.close();
        es = null;
        scheduleReconnect();
      };
    }

    function scheduleReconnect(): void {
      if (cancelled) return;
      attempt += 1;
      const wait = Math.min(30_000, 1_000 * Math.pow(1.6, attempt - 1));
      retryTimer = setTimeout(connect, wait);
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
      es = null;
    };
  }, [scope, enabled, qc]);
}
