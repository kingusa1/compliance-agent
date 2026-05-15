/**
 * GET /api/reviewers/active — list of active reviewer/lead/admin profiles
 * used by the tracker side-panel assignee dropdown.
 */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export type ActiveReviewer = {
  id: string;
  email: string;
  name: string;
  role: "reviewer" | "lead" | "admin";
};

export function useActiveReviewersQuery() {
  return useQuery<ActiveReviewer[], Error>({
    queryKey: ["admin", "reviewers", "active"],
    queryFn: async () => apiFetch<ActiveReviewer[]>(`/api/reviewers/active`),
    // The set rarely changes during a reviewer's session — cache for an hour.
    staleTime: 60 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
