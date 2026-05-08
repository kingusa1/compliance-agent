"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * TanStack Query provider. Defaults are tuned for a compliance-review
 * SPA: 30s stale window (queue moves slowly, calls are immutable once
 * scored), retry once (network hiccup ≠ permanent), no retry on 401/403
 * (those bubble through to AuthGuard).
 */
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
