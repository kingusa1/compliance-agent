"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Inbox, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { useFindingsQuery, type FindingsParams } from "@/lib/queries/reviewer";
import { ApiError } from "@/lib/api";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { useUrlState } from "@/lib/hooks/useUrlState";
import { FlagBadge } from "@/components/reviewer/FlagBadge";

/**
 * /findings — cross-call flag search.
 *
 * Filters: severity (HIGH/MEDIUM/LOW/all). Future: rule_id, agent. Cursor
 * pagination via the backend's `next_cursor` field (we keep a stack of
 * cursors so Back works).
 */
export default function FindingsPage() {
  const router = useRouter();
  const { get, set } = useUrlState();
  const severity = get("severity") || "all";
  const [search, setSearch] = useState(() => get("q"));
  const debouncedSearch = useDebouncedValue(search, 300);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;

  const params: FindingsParams = {
    cursor,
    limit: 25,
  };
  if (severity !== "all") params.severity = severity;

  const findings = useFindingsQuery(params);
  // Backend `/api/findings` doesn't accept ?q (verified via curl) — we
  // filter rule_id + agent_name + reason client-side on the current page.
  const rows = useMemo(() => {
    const all = findings.data?.findings ?? [];
    if (!debouncedSearch.trim()) return all;
    const q = debouncedSearch.toLowerCase();
    return all.filter(
      (f) =>
        f.rule_id.toLowerCase().includes(q) ||
        (f.agent_name ?? "").toLowerCase().includes(q) ||
        (f.reason ?? "").toLowerCase().includes(q),
    );
  }, [findings.data?.findings, debouncedSearch]);

  // Mirror debounced search into URL ?q (after settle, not on every keystroke).
  useEffect(() => {
    if (get("q") === debouncedSearch) return;
    set("q", debouncedSearch || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  return (
    <div className="mx-auto flex h-screen max-w-[1100px] flex-col px-6 py-6">
      <header className="mb-5 flex items-baseline justify-between">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">Findings</h1>
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">
            All flagged moments across calls — filter by severity to triage.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-[var(--text-dim)]" />
            <Input
              placeholder="Search rule, agent, reason…"
              className="h-8 w-72 pl-8 text-[13px]"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              data-testid="findings-search"
            />
          </div>
          <Select
            value={severity}
            onValueChange={(v) => {
              const next = v ?? "all";
              set("severity", next === "all" ? null : next);
              setCursorStack([]);
            }}
          >
            <SelectTrigger className="h-8 w-[160px] text-[13px]">
              <SelectValue placeholder="Severity" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All severities</SelectItem>
              <SelectItem value="HIGH">High only</SelectItem>
              <SelectItem value="MEDIUM">Medium only</SelectItem>
              <SelectItem value="LOW">Low only</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </header>

      {findings.isLoading ? (
        <FindingsSkeleton />
      ) : findings.isError ? (
        <ErrorBanner error={findings.error} onRetry={() => findings.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
          <Table>
            <TableHeader>
              <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
                <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                  When
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                  Severity
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                  Rule
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                  Call
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                  Agent
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
                  Status
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((f) => (
                <TableRow
                  key={f.id}
                  onClick={() => router.push(`/calls/${f.call_id}`)}
                  className="cursor-pointer border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)]"
                >
                  <TableCell className="whitespace-nowrap text-[13px] text-[var(--text-muted)] tabular-nums">
                    {formatWhen(f.created_at)}
                  </TableCell>
                  <TableCell>
                    <FlagBadge severity={f.severity} />
                  </TableCell>
                  <TableCell className="font-mono text-[12px] text-[var(--text-primary)]">
                    {f.rule_id}
                  </TableCell>
                  <TableCell className="text-[13px] text-[var(--text-primary)]">
                    {f.call_filename ?? f.call_id.slice(0, 8)}
                  </TableCell>
                  <TableCell className="text-[13px] text-[var(--text-muted)]">
                    {f.agent_name ?? "—"}
                  </TableCell>
                  <TableCell className="text-[13px] text-[var(--text-muted)]">
                    {f.status ?? "open"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-[12px] text-[var(--text-dim)]">
        <span>
          {findings.data?.total != null
            ? `Total ${findings.data.total}`
            : `Showing ${rows.length}`}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={cursorStack.length === 0}
            onClick={() => setCursorStack((s) => s.slice(0, -1))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!findings.data?.next_cursor}
            onClick={() => {
              const next = findings.data?.next_cursor;
              if (next) setCursorStack((s) => [...s, next]);
            }}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}

function FindingsSkeleton() {
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      {Array.from({ length: 8 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 border-b border-[var(--border-subtle)] px-4 py-3 last:border-b-0"
        >
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="ml-auto h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-6 py-16 text-center">
      <Inbox className="h-8 w-8 text-[var(--text-dim)]" />
      <div className="text-[15px] font-medium text-[var(--text-primary)]">
        No findings yet
      </div>
      <div className="max-w-sm text-[13px] text-[var(--text-muted)]">
        Findings appear here when reviewers flag moments inside calls.
      </div>
    </div>
  );
}

function ErrorBanner({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status} ${error.body || error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-[13px] text-[var(--red-fail)]"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex-1">
        <div className="font-medium">Couldn’t load findings</div>
        <div className="mt-1 text-[12px] text-red-400/75">{message}</div>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function formatWhen(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
