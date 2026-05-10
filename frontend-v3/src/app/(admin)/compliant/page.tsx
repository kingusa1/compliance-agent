"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { getCompliantQuery } from "@/lib/queries/aggregator";
import { useUrlState } from "@/lib/hooks/useUrlState";
import { CursorPagination } from "@/components/shared/CursorPagination";
import { CallPreviewPanel } from "@/components/shared/CallPreviewPanel";

const COMPLIANT_PAGE_LIMIT = 50;

/**
 * /compliant — calls signed-off compliant. Mirrors /non-compliant +
 * /queue (master-detail 60/40). Selection is URL-persisted via ?call=ID.
 */
export default function CompliantPage() {
  const { get, set } = useUrlState();
  const offset = Math.max(0, parseInt(get("offset") || "0", 10) || 0);
  const selectedId = get("call");
  const { data, isLoading, isError, isFetching } = useQuery(
    getCompliantQuery({ limit: COMPLIANT_PAGE_LIMIT, offset }),
  );

  const calls = data?.calls ?? [];
  const effectiveSelected =
    selectedId && calls.some((c) => c.id === selectedId)
      ? selectedId
      : (calls[0]?.id ?? null);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex-shrink-0 border-b border-[var(--border-subtle)] px-6 py-5">
        <h1 className="text-[24px] font-semibold tracking-tight">Compliant</h1>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Calls signed off as compliant — clean audit trail.
        </p>
      </header>

      {isError ? (
        <div className="m-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-[13px] text-[var(--text-muted)]">
          Could not load.
        </div>
      ) : null}

      {!isError ? (
        <div className="grid min-h-0 flex-1 grid-cols-[60%_40%] overflow-hidden">
          <div className="flex min-w-0 flex-col overflow-hidden border-r border-[var(--border-subtle)]">
            <div className="ca-scroll flex-1 overflow-y-auto">
              {isLoading ? (
                <div className="space-y-2 p-6">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : calls.length === 0 ? (
                <div className="m-6 rounded-lg border border-emerald-300/30 bg-emerald-500/5 p-4 text-[13px] text-emerald-300">
                  <p className="font-medium">No compliant calls yet.</p>
                  <p className="mt-1 text-emerald-200/80">
                    Calls reach this list once a reviewer signs them off as compliant. Upload a call from the
                    {" "}
                    <Link href="/calls" className="underline">Calls</Link>
                    {" "}page or the
                    {" "}
                    <Link href="/tracker" className="underline">Tracker</Link>
                    .
                  </p>
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">Call</TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">Customer</TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">Agent</TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">Score</TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">Created</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {calls.map((c) => {
                      const isSelected = c.id === effectiveSelected;
                      return (
                        <TableRow
                          key={c.id}
                          onClick={() => set("call", c.id)}
                          aria-selected={isSelected}
                          className={`cursor-pointer border-[var(--border-subtle)] ${
                            isSelected ? "bg-[var(--bg-elev2)]" : "hover:bg-[var(--bg-elev1)]"
                          }`}
                          style={{
                            borderLeft: `2px solid ${isSelected ? "var(--emerald)" : "transparent"}`,
                          }}
                        >
                          <TableCell className="font-mono text-[12px]">
                            <Link
                              href={`/calls/${c.id}`}
                              onClick={(e) => e.stopPropagation()}
                              className="text-[var(--blue-coaching)] hover:underline"
                            >
                              {c.filename ?? c.id.slice(0, 12)}
                            </Link>
                          </TableCell>
                          <TableCell className="text-[13px] text-[var(--text-primary)]">
                            {c.customer_name ?? "—"}
                          </TableCell>
                          <TableCell className="text-[13px] text-[var(--text-muted)]">
                            {c.agent_name ?? "—"}
                          </TableCell>
                          <TableCell className="text-[13px] text-emerald-300">
                            {c.score ?? "—"}
                          </TableCell>
                          <TableCell className="text-[12px] text-[var(--text-muted)]">
                            {c.created_at ? new Date(c.created_at).toLocaleDateString() : "—"}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </div>
            {data && calls.length > 0 ? (
              <div className="flex-shrink-0 border-t border-[var(--border-subtle)]">
                <CursorPagination
                  offset={offset}
                  limit={COMPLIANT_PAGE_LIMIT}
                  total={data.total}
                  disabled={isFetching}
                  onChange={(next) => set("offset", next === 0 ? null : next)}
                />
              </div>
            ) : null}
          </div>

          <div className="flex min-w-0 flex-col overflow-hidden bg-[var(--bg-elev1)]">
            <CallPreviewPanel callId={effectiveSelected} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
