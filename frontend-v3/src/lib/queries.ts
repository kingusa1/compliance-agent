/**
 * TanStack Query keys + queryFns for the most-used endpoints.
 *
 * Lane-specific queries (reviewer, admin, observability) live alongside
 * their pages in `src/lib/queries/<lane>.ts`. This root file holds the
 * cross-cutting essentials so the reference page and AuthGuard can
 * boot without pulling in lane-specific code.
 */
import {
  apiFetch,
  getMe,
  getCalls,
  getCall,
  getCustomers,
  getCustomer,
  getDeals,
  getDeal,
  getAgents,
  getQueue,
  type CallsListParams,
  type Me,
  type CallListResponse,
  type Call,
  type CustomerListResponse,
  type DealListResponse,
  type AgentRow,
  type QueueResponse,
} from "@/lib/api";

export const queryKeys = {
  me: ["me"] as const,
  calls: (params?: CallsListParams) => ["calls", params ?? {}] as const,
  call: (id: string) => ["call", id] as const,
  customers: (params?: { q?: string; limit?: number; offset?: number }) =>
    ["customers", params ?? {}] as const,
  customer: (slug: string) => ["customer", slug] as const,
  deals: () => ["deals"] as const,
  deal: (id: string) => ["deal", id] as const,
  agents: () => ["agents"] as const,
  queue: (filter: string = "all") => ["queue", filter] as const,
};

export function getMeQuery() {
  return {
    queryKey: queryKeys.me,
    queryFn: (): Promise<Me> => getMe(),
  };
}

export function getCallsQuery(params: CallsListParams = {}) {
  return {
    queryKey: queryKeys.calls(params),
    queryFn: (): Promise<CallListResponse> => getCalls(params),
  };
}

export function getCallQuery(id: string) {
  return {
    queryKey: queryKeys.call(id),
    queryFn: (): Promise<Call> => getCall(id),
  };
}

export function getCustomersQuery(params: { q?: string; limit?: number; offset?: number } = {}) {
  return {
    queryKey: queryKeys.customers(params),
    queryFn: (): Promise<CustomerListResponse> => getCustomers(params),
  };
}

export function getCustomerQuery(slug: string) {
  return {
    queryKey: queryKeys.customer(slug),
    queryFn: () => getCustomer(slug),
  };
}

export function getDealsQuery() {
  return {
    queryKey: queryKeys.deals(),
    queryFn: (): Promise<DealListResponse> => getDeals(),
  };
}

export function getDealQuery(id: string) {
  return {
    queryKey: queryKeys.deal(id),
    queryFn: () => getDeal(id),
  };
}

export function getAgentsQuery() {
  return {
    queryKey: queryKeys.agents(),
    queryFn: (): Promise<{ agents: AgentRow[] }> => getAgents(),
  };
}

export function getQueueQuery(filter: string = "all") {
  return {
    queryKey: queryKeys.queue(filter),
    queryFn: (): Promise<QueueResponse> => getQueue(filter),
  };
}

// Re-export apiFetch so lane queries can build on it without a separate import.
export { apiFetch };
