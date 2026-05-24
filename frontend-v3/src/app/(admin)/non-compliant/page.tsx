"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { shortFilename } from "@/lib/filename";
import { formatCustomerName } from "@/lib/customer";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { getNonCompliantQuery } from "@/lib/queries/aggregator";
import { useUrlState } from "@/lib/hooks/useUrlState";
import { CursorPagination } from "@/components/shared/CursorPagination";
import { CallPreviewPanel } from "@/components/shared/CallPreviewPanel";

const NON_COMPLIANT_PAGE_LIMIT = 50;

/**
 * /non-compliant — calls flagged non-compliant. Mirrors /compliant +
 * /queue (master-detail 60/40). The rejection_category is highlighted in
 * the master row; the right rail shows the same CallPreviewPanel as
 * /queue. Selection is URL-persisted via ?call=ID.
 */
export default function NonCompliantPage() {
  const { get, set } = useUrlState();
  const offset = Math.max(0, parseInt(get("offset") || "0", 10) || 0);
  const selectedId = get("call");
  const { data, isLoading, isError, isFetching } = useQuery(
    getNonCompliantQuery({ limit: NON_COMPLIANT_PAGE_LIMIT, offset }),
  );

  const calls = data?.calls ?? [];
  const effectiveSelected =
    selectedId && calls.some((c) => c.id === selectedId)
      ? selectedId
      : (calls[0]?.id ?? null);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex-shrink-0 border-b border-[var(--border-subtle)] px-6 py-5">
        <h1 className="text-[24px] font-semibold tracking-tight">Non-compliant</h1>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Calls flagged non-compliant — triage and escalate.
        </p>
      </header>

      {isError ? (
        <div className="m-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-[13px] text-[var(--text-muted)]">
          Could not load.
        </div>
      ) : null}

      {!isError ? (
        <div className="grid min-h-0 flex-1 grid-cols-[60%_40%] overflow-hidden">
          {/* Master pane */}
          <div className="flex min-w-0 flex-col overflow-hidden border-r border-[var(--border-subtle)]">
            <div className="ca-scroll flex-1 overflow-y-auto">
              {isLoading ? (
                <div className="space-y-2 p-6">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : calls.length === 0 ? (
                <div className="m-6 rounded-lg border border-amber-300 bg-amber-50 p-4 text-[13px] text-amber-900">
                  <p className="font-medium">No human-reviewed non-compliant calls yet.</p>
                  <p className="mt-1 text-amber-800">
                    Calls AI-categorized as non-compliant are held in the
                    Tracker's "Awaiting review" tab until a reviewer confirms.{" "}
                    <Link href="/tracker?tab=awaiting_review" className="underline">
                      Go review them →
                    </Link>
                  </p>
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                        Call
                      </TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                        Customer
                      </TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                        Agent
                      </TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                        Reason
                      </TableHead>
                      <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                        Created
                      </TableHead>
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
                            isSelected
                              ? "bg-[var(--bg-elev2)]"
                              : "hover:bg-[var(--bg-elev1)]"
                          }`}
                          style={{
                            borderLeft: `2px solid ${
                              isSelected ? "var(--red)" : "transparent"
                            }`,
                          }}
                        >
                          <TableCell
                            className="font-mono text-[12px]"
                            style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                            title={c.filename ?? c.id}
                          >
                            <Link
                              href={`/calls/${c.id}`}
                              onClick={(e) => e.stopPropagation()}
                              className="text-[var(--blue-coaching)] hover:underline"
                            >
                              {shortFilename(c.filename ?? c.id.slice(0, 12))}
                            </Link>
                          </TableCell>
                          <TableCell className="text-[13px] text-[var(--text-primary)]">
                            {formatCustomerName(c.customer_name)}
                          </TableCell>
                          <TableCell className="text-[13px] text-[var(--text-muted)]">
                            {c.agent_name ?? "—"}
                          </TableCell>
                          <TableCell
                            className="text-[13px] text-red-400"
                            style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                            title={c.reason ?? undefined}
                          >
                            {c.reason ?? "—"}
                          </TableCell>
                          <TableCell className="text-[12px] text-[var(--text-muted)]">
                            {c.created_at
                              ? new Date(c.created_at).toLocaleDateString()
                              : "—"}
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
                  limit={NON_COMPLIANT_PAGE_LIMIT}
                  total={data.total}
                  disabled={isFetching}
                  onChange={(next) => set("offset", next === 0 ? null : next)}
                />
              </div>
            ) : null}
          </div>

          {/* Detail pane */}
          <div className="flex min-w-0 flex-col overflow-hidden bg-[var(--bg-elev1)]">
            <CallPreviewPanel callId={effectiveSelected} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
