"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";

import { supabase } from "@/lib/supabase";
import { getMeQuery } from "@/lib/queries";
import { ApiError } from "@/lib/api";

type AuthState = {
  session: Session | null;
  loading: boolean;
};

/**
 * Subscribes to Supabase auth changes; returns the current session +
 * a loading flag for the initial check. Components can branch on
 * `!loading && !session` to render an unauthed UI.
 */
export function useAuth(): AuthState {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    supabase.auth.getSession().then(({ data }) => {
      if (mounted) {
        setSession(data.session);
        setLoading(false);
      }
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_evt, sess) => {
      setSession(sess);
    });
    return () => {
      mounted = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  return { session, loading };
}

/**
 * Fetches /api/me when a session exists. The role field drives the
 * route-group landing decision (reviewer/lead → /queue, admin/tpi → /calls).
 */
export function useMe() {
  const { session, loading } = useAuth();
  return useQuery({
    ...getMeQuery(),
    enabled: !loading && !!session,
    retry: (count, err) => {
      // Don't retry on 401/403 — those mean "log in again", not "transient".
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) return false;
      return count < 1;
    },
    staleTime: 5 * 60 * 1000, // role rarely changes; cache 5min
  });
}

/**
 * <AuthGuard> — redirects to /login if no session is present.
 * Wraps any (reviewer)/(admin) layout. Accepts an `allowedRoles` array
 * that, when set, also enforces the user's /api/me role matches.
 *
 * Usage:
 *   <AuthGuard allowedRoles={["reviewer", "lead", "admin"]}>{children}</AuthGuard>
 */
export function AuthGuard({
  children,
  allowedRoles,
}: {
  children: React.ReactNode;
  allowedRoles?: Array<"reviewer" | "lead" | "admin">;
}) {
  const router = useRouter();
  const { session, loading } = useAuth();
  const me = useMe();

  useEffect(() => {
    if (loading) return;
    if (!session) {
      router.replace("/login");
      return;
    }
    if (allowedRoles && me.data && !allowedRoles.includes(me.data.role)) {
      // Role mismatch — drop to /calls (admin landing) rather than show empty state.
      router.replace("/calls");
    }
  }, [loading, session, me.data, allowedRoles, router]);

  if (loading) return null; // brief gap; layouts can render their own skeleton instead
  if (!session) return null;
  if (allowedRoles && me.data && !allowedRoles.includes(me.data.role)) return null;

  return <>{children}</>;
}
