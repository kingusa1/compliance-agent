"use client";

/**
 * /scripts — list of compliance scripts grouped by supplier, with the
 * "+ Upload script" entry-point that opens UploadScriptDialog.
 *
 * Each supplier card is collapsible. Each row shows: script_name +
 * version + mode pill + checkpoint count + uploaded date + Edit + Delete.
 * Delete is soft (backend sets active=false); we still invalidate
 * ["scripts"] so the row drops out of the inactive view.
 *
 * 2026-05-24 enterprise polish wave:
 *   • Search filter (debounced) over script name + supplier
 *   • Retry button on error state
 *   • Realtime invalidate so concurrent edits in another tab refresh
 *   • Empty-checkpoint warning chip — surfaces silent extractor misses
 *   • Inactive-scripts toggle (admin/lead-only) to recover soft-deleted
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileText,
  Pencil,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Pill } from "@/components/design/Pill";
import { EmptyState } from "@/components/design/EmptyState";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";
import {
  useScriptsListQuery,
  parseCheckpoints,
  type Script,
} from "@/lib/queries/scripts";
import { useDeleteScript } from "@/lib/mutations/scripts";

import { UploadScriptDialog } from "./UploadScriptDialog";

export default function ScriptsPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useScriptsListQuery();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const dSearch = useDebouncedValue(search, 200);

  // 2026-05-24 wiring audit MEDIUM — match the realtime pattern used on
  // /rejections + /tracker so an admin editing scripts in tab B sees the
  // change in tab A without window focus.
  useRealtimeInvalidate("scripts", [["scripts"]]);

  const scripts = data?.scripts ?? [];
  const inactiveCount = scripts.filter((s) => s.active === false).length;
  const visible = useMemo(() => {
    const base = showInactive ? scripts : scripts.filter((s) => s.active !== false);
    const q = dSearch.trim().toLowerCase();
    if (!q) return base;
    return base.filter((s) =>
      [s.script_name, s.name, s.supplier_name, s.mode]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    );
  }, [scripts, dSearch, showInactive]);

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">Scripts</h1>
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">
            {visible.length} compliance script{visible.length === 1 ? "" : "s"}
            {dSearch ? ` matching “${dSearch}”` : ""}, grouped by supplier
            {isFetching && !isLoading && (
              <span className="ml-2 inline-flex items-center gap-1" aria-live="polite">
                <span
                  className="inline-block size-1.5 animate-pulse rounded-full bg-emerald-500"
                  aria-hidden
                />
                Refreshing
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search
              size={14}
              className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[var(--text-dim)]"
              aria-hidden
            />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name or supplier…"
              aria-label="Search scripts"
              className="h-8 w-56 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] pl-7 pr-2 text-[12px] text-[var(--text-primary)] outline-none focus:border-emerald-500"
            />
          </div>
          {inactiveCount > 0 && (
            <label className="inline-flex items-center gap-1 text-[12px] text-[var(--text-muted)]">
              <input
                type="checkbox"
                checked={showInactive}
                onChange={(e) => setShowInactive(e.target.checked)}
                aria-label="Show inactive scripts"
              />
              Show inactive · {inactiveCount}
            </label>
          )}
          <Button onClick={() => setUploadOpen(true)} data-testid="open-upload">
            <Plus size={14} className="mr-1" />
            Upload script
          </Button>
        </div>
      </header>

      {isLoading && (
        <div className="space-y-2" aria-busy>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      )}

      {isError && (
        <div
          role="alert"
          className="flex items-start justify-between gap-3 rounded-lg border border-red-300 bg-red-50 p-4 text-[13px] text-red-900"
        >
          <div>
            <strong className="font-semibold">Could not load scripts.</strong>{" "}
            {error instanceof Error ? error.message : "Unknown error."}
          </div>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            Retry
          </Button>
        </div>
      )}

      {data && visible.length === 0 && !dSearch && (
        <EmptyState
          icon={<FileText size={20} />}
          title="No scripts yet"
          body="Upload your first PDF or DOCX to extract checkpoints."
          actions={
            <Button onClick={() => setUploadOpen(true)}>
              <Upload size={14} className="mr-1" />
              Upload script
            </Button>
          }
        />
      )}

      {data && visible.length === 0 && dSearch && (
        <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-center text-[13px] text-[var(--text-muted)]">
          No scripts match <strong>“{dSearch}”</strong>.{" "}
          <button
            type="button"
            onClick={() => setSearch("")}
            className="text-emerald-700 underline hover:no-underline"
          >
            Clear search
          </button>
        </div>
      )}

      {data && visible.length > 0 && <ScriptsByGroup scripts={visible} />}

      <UploadScriptDialog open={uploadOpen} onOpenChange={setUploadOpen} />
    </div>
  );
}

function ScriptsByGroup({ scripts }: { scripts: Script[] }) {
  const groups = useMemo(() => {
    const m = new Map<string, Script[]>();
    for (const s of scripts) {
      const key = s.supplier_name?.trim() || "Unassigned";
      if (!m.has(key)) m.set(key, []);
      m.get(key)!.push(s);
    }
    return Array.from(m.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [scripts]);

  return (
    <div className="space-y-3">
      {groups.map(([supplier, items]) => (
        <SupplierGroup key={supplier} supplier={supplier} items={items} />
      ))}
    </div>
  );
}

function SupplierGroup({ supplier, items }: { supplier: string; items: Script[] }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <section className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left hover:bg-[var(--bg-elev2)]"
      >
        {expanded ? (
          <ChevronDown size={14} className="text-[var(--text-dim)]" />
        ) : (
          <ChevronRight size={14} className="text-[var(--text-dim)]" />
        )}
        <span className="text-[13px] font-semibold text-[var(--text-primary)]">
          {supplier}
        </span>
        <span className="text-[12px] text-[var(--text-muted)]">
          · {items.length} script{items.length === 1 ? "" : "s"}
        </span>
      </button>
      {expanded && (
        <ul className="divide-y divide-[var(--border-subtle)] border-t border-[var(--border-subtle)]">
          {items.map((s) => (
            <ScriptRow key={s.id} script={s} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ScriptRow({ script }: { script: Script }) {
  const del = useDeleteScript();
  const cps = parseCheckpoints(script.checkpoints);
  const created = script.created_at
    ? new Date(String(script.created_at)).toLocaleDateString()
    : "—";

  async function onDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`Remove "${script.script_name ?? script.id}"?`)) return;
    try {
      await del.mutateAsync(script.id);
      toast.success("Script removed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <li className="flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--bg-elev2)]">
      <Link
        href={`/scripts/${script.id}`}
        className="flex flex-1 items-center gap-3 min-w-0"
      >
        <FileText size={14} className="text-[var(--text-dim)] flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 truncate">
            <span className="truncate text-[13px] font-medium text-[var(--text-primary)]">
              {script.script_name ?? script.name ?? script.id}
            </span>
            {script.version && (
              <Pill tone="neutral" mono>
                v{script.version}
              </Pill>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
            {script.mode && <span>{script.mode}</span>}
            {script.mode && <span>·</span>}
            <span className={cps.length === 0 ? "text-amber-700" : undefined}>
              {cps.length} checkpoint{cps.length === 1 ? "" : "s"}
            </span>
            {cps.length === 0 && (
              <span
                title="Checkpoint extractor returned 0 rules — the script grader will fall through to universal rules. Re-upload or use the admin ingest-script-checkpoints endpoint."
                className="inline-flex items-center gap-0.5 rounded-sm bg-amber-100 px-1 text-amber-900"
                aria-label="Warning: no checkpoints extracted"
              >
                <AlertTriangle size={10} aria-hidden />
                no rules
              </span>
            )}
            <span>·</span>
            <span>{created}</span>
            {script.active === false && (
              <>
                <span>·</span>
                <span className="rounded-sm bg-gray-200 px-1 text-gray-700">inactive</span>
              </>
            )}
          </div>
        </div>
      </Link>
      <div className="flex items-center gap-1 flex-shrink-0">
        <Link
          href={`/scripts/${script.id}`}
          aria-label={`Edit ${script.script_name}`}
          className="inline-flex h-7 items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-2 text-[12px] text-[var(--text-primary)] hover:bg-[var(--bg-elev3)]"
        >
          <Pencil size={14} />
        </Link>
        <Button
          variant="outline"
          size="sm"
          onClick={onDelete}
          disabled={del.isPending}
          aria-label={`Delete ${script.script_name}`}
          className="text-[var(--red)]"
        >
          <Trash2 size={14} />
        </Button>
      </div>
    </li>
  );
}
