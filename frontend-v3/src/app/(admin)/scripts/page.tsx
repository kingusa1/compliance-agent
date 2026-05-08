"use client";

/**
 * /scripts — list of compliance scripts grouped by supplier, with the
 * "+ Upload script" entry-point that opens UploadScriptDialog.
 *
 * Each supplier card is collapsible. Each row shows: script_name +
 * version + mode pill + checkpoint count + uploaded date + Edit + Delete.
 * Delete is soft (backend sets active=false); we still invalidate
 * ["scripts"] so the row drops out of the inactive view.
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Pill } from "@/components/design/Pill";
import { EmptyState } from "@/components/design/EmptyState";
import {
  useScriptsListQuery,
  parseCheckpoints,
  type Script,
} from "@/lib/queries/scripts";
import { useDeleteScript } from "@/lib/mutations/scripts";

import { UploadScriptDialog } from "./UploadScriptDialog";

export default function ScriptsPage() {
  const { data, isLoading, isError } = useScriptsListQuery();
  const [uploadOpen, setUploadOpen] = useState(false);

  const scripts = data?.scripts ?? [];
  const visible = useMemo(
    () => scripts.filter((s) => s.active !== false),
    [scripts],
  );

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">Scripts</h1>
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">
            {visible.length} compliance script{visible.length === 1 ? "" : "s"}, grouped
            by supplier.
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)} data-testid="open-upload">
          <Plus size={14} className="mr-1" />
          Upload script
        </Button>
      </header>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      )}

      {isError && (
        <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-[13px] text-[var(--text-muted)]">
          Could not load scripts.
        </div>
      )}

      {data && visible.length === 0 && (
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
            <span>
              {cps.length} checkpoint{cps.length === 1 ? "" : "s"}
            </span>
            <span>·</span>
            <span>{created}</span>
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
