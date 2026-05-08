"use client";

/**
 * /scripts/[id] — script detail editor with three tabs:
 *
 *   1. Editor    — edit supplier_name / script_name / version / mode +
 *                  checkpoints inline (CheckpointEditor). Save calls
 *                  PUT /api/scripts/{id}; Delete calls DELETE.
 *   2. Markdown  — pretty preview of /api/scripts/{id}/markdown (text/plain
 *                  fetched via fetchScriptMarkdown).
 *   3. Versions  — historical snapshots from /api/scripts/{id}/versions.
 *
 * The Editor tab loads its initial form values from useScriptQuery(id)
 * once. After Save the mutation invalidates the script-detail cache so
 * the form re-syncs to whatever the backend persisted.
 */
import { use, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Pill } from "@/components/design/Pill";
import {
  useScriptQuery,
  useScriptMarkdownQuery,
  useScriptVersionsQuery,
  parseCheckpoints,
  type Checkpoint,
} from "@/lib/queries/scripts";
import { useUpdateScript, useDeleteScript } from "@/lib/mutations/scripts";
import {
  scriptSchema,
  MODE_VALUES,
  type CheckpointFormValues,
  type ScriptFormValues,
} from "@/lib/schemas/script";

import {
  CheckpointEditor,
  AddCheckpointButton,
  blankCheckpoint,
} from "../CheckpointEditor";

type EditorState = {
  supplier_name: string;
  script_name: string;
  version: string;
  mode: string;
  checkpoints: CheckpointFormValues[];
};

function toEditorState(
  data: {
    supplier_name?: string | null;
    script_name?: string | null;
    name?: string | null;
    version?: string | number | null;
    mode?: string | null;
    checkpoints?: string | null;
  },
): EditorState {
  return {
    supplier_name: data.supplier_name ?? "",
    script_name: data.script_name ?? data.name ?? "",
    version: data.version != null ? String(data.version) : "",
    mode: data.mode ?? "meaning_for_meaning",
    checkpoints: parseCheckpoints(data.checkpoints).map((cp, i) => ({
      section: cp.section ?? i + 1,
      name: cp.name,
      required: cp.required,
      key_phrases: cp.key_phrases,
      customer_response_required: cp.customer_response_required ?? false,
      strictness: cp.strictness,
    })),
  };
}

export default function ScriptDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const script = useScriptQuery(id);
  const markdown = useScriptMarkdownQuery(id);
  const versions = useScriptVersionsQuery(id);

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-6">
        <Link
          href="/scripts"
          className="text-[12px] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
        >
          ← Back to scripts
        </Link>
        <h1 className="mt-2 text-[24px] font-semibold tracking-tight">
          {script.data?.script_name ?? script.data?.name ?? id}
        </h1>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          {script.data?.supplier_name ?? "—"}
          {script.data?.version ? ` · v${script.data.version}` : ""}
        </p>
      </header>

      <Tabs defaultValue="editor">
        <TabsList variant="line">
          <TabsTrigger value="editor">Editor</TabsTrigger>
          <TabsTrigger value="markdown">Markdown</TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
        </TabsList>

        <TabsContent value="editor">
          {script.isLoading && <Skeleton className="mt-4 h-64 w-full" />}
          {script.isError && (
            <div className="mt-4 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5 text-[13px] text-[var(--text-muted)]">
              Could not load script.
            </div>
          )}
          {script.data && (
            <EditorTab
              id={id}
              initial={toEditorState(script.data)}
              onDeleted={() => router.push("/scripts")}
            />
          )}
        </TabsContent>

        <TabsContent value="markdown">
          {markdown.isLoading && <Skeleton className="mt-4 h-64 w-full" />}
          {markdown.data && (
            <pre className="mt-4 max-h-[70vh] overflow-auto rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5 font-mono text-[12px] leading-[1.6] text-[var(--text-primary)] whitespace-pre-wrap">
              {markdown.data.markdown}
            </pre>
          )}
          {markdown.isError && (
            <div className="mt-4 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5 text-[13px] text-[var(--text-muted)]">
              Could not load markdown.
            </div>
          )}
        </TabsContent>

        <TabsContent value="versions">
          {versions.isLoading && <Skeleton className="mt-4 h-32 w-full" />}
          {versions.data && (
            <ul className="mt-4 divide-y divide-[var(--border-subtle)] rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
              {versions.data.versions.length === 0 && (
                <li className="px-4 py-3 text-[13px] text-[var(--text-muted)]">
                  No prior versions.
                </li>
              )}
              {versions.data.versions.map((v, i) => {
                const snap: Checkpoint[] = parseCheckpoints(v.checkpoints_snapshot);
                return (
                  <li
                    key={`${v.version_number ?? v.version ?? i}`}
                    className="flex items-baseline justify-between px-4 py-3"
                  >
                    <span className="font-mono text-[13px] text-[var(--text-primary)]">
                      v{v.version_number ?? v.version ?? "—"}
                    </span>
                    <div className="flex items-center gap-3 text-[12px] text-[var(--text-muted)]">
                      {v.mode_snapshot && <Pill tone="neutral">{v.mode_snapshot}</Pill>}
                      <span>
                        {snap.length} checkpoint{snap.length === 1 ? "" : "s"}
                      </span>
                      <span>
                        {v.created_at
                          ? new Date(String(v.created_at)).toLocaleDateString()
                          : "—"}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {versions.isError && (
            <div className="mt-4 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5 text-[13px] text-[var(--text-muted)]">
              Could not load versions.
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function EditorTab({
  id,
  initial,
  onDeleted,
}: {
  id: string;
  initial: EditorState;
  onDeleted: () => void;
}) {
  const [state, setState] = useState<EditorState>(initial);
  const [validationError, setValidationError] = useState<string | null>(null);

  // Re-sync local state if the underlying script changes (e.g. after
  // mutation invalidation refetches with new data).
  useEffect(() => {
    setState(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial.supplier_name, initial.script_name, initial.version, initial.mode, initial.checkpoints.length]);

  const update = useUpdateScript();
  const del = useDeleteScript();

  const setCheckpoint = (i: number, cp: CheckpointFormValues) => {
    const next = state.checkpoints.slice();
    next[i] = cp;
    setState({ ...state, checkpoints: next });
  };

  const removeCheckpoint = (i: number) => {
    setState({
      ...state,
      checkpoints: state.checkpoints.filter((_, idx) => idx !== i),
    });
  };

  const move = (i: number, dir: -1 | 1) => {
    const next = state.checkpoints.slice();
    const j = i + dir;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    next.forEach((c, k) => (c.section = k + 1));
    setState({ ...state, checkpoints: next });
  };

  const addCheckpoint = () => {
    setState({
      ...state,
      checkpoints: [
        ...state.checkpoints,
        blankCheckpoint(state.checkpoints.length + 1),
      ],
    });
  };

  async function handleSave() {
    setValidationError(null);
    const parsed = scriptSchema.safeParse(state as unknown as ScriptFormValues);
    if (!parsed.success) {
      setValidationError(parsed.error.issues[0]?.message ?? "Validation failed");
      return;
    }
    try {
      await update.mutateAsync({
        id,
        payload: {
          supplier_name: parsed.data.supplier_name,
          script_name: parsed.data.script_name,
          version: parsed.data.version || null,
          mode: parsed.data.mode,
          checkpoints: parsed.data.checkpoints,
        },
      });
      toast.success("Script updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function handleDelete() {
    if (!confirm("Remove this script? It will be hidden from the list.")) return;
    try {
      await del.mutateAsync(id);
      toast.success("Script removed");
      onDeleted();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  const isDirty = useMemo(() => {
    return JSON.stringify(state) !== JSON.stringify(initial);
  }, [state, initial]);

  return (
    <div className="mt-4 space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Supplier name
          </label>
          <Input
            value={state.supplier_name}
            onChange={(e) => setState({ ...state, supplier_name: e.target.value })}
            data-testid="editor-supplier"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Script name
          </label>
          <Input
            value={state.script_name}
            onChange={(e) => setState({ ...state, script_name: e.target.value })}
            data-testid="editor-script-name"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Version
          </label>
          <Input
            value={state.version}
            onChange={(e) => setState({ ...state, version: e.target.value })}
            data-testid="editor-version"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Mode
          </label>
          <Select value={state.mode} onValueChange={(v) => setState({ ...state, mode: v ?? "" })}>
            <SelectTrigger data-testid="editor-mode">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MODE_VALUES.map((m) => (
                <SelectItem key={m} value={m}>
                  {m.replace(/_/g, " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <h3 className="text-[12px] font-semibold uppercase tracking-wider text-[var(--text-dim)]">
          {state.checkpoints.length} checkpoint
          {state.checkpoints.length === 1 ? "" : "s"}
        </h3>
        <AddCheckpointButton onAdd={addCheckpoint} />
      </div>

      <div className="space-y-2">
        {state.checkpoints.map((cp, i) => (
          <CheckpointEditor
            key={i}
            index={i}
            total={state.checkpoints.length}
            value={cp}
            onChange={(next) => setCheckpoint(i, next)}
            onMoveUp={() => move(i, -1)}
            onMoveDown={() => move(i, 1)}
            onRemove={() => removeCheckpoint(i)}
          />
        ))}
        {state.checkpoints.length === 0 && (
          <div className="rounded-md border border-dashed border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-center text-[13px] text-[var(--text-muted)]">
            No checkpoints. Add one to start.
          </div>
        )}
      </div>

      {validationError && (
        <div className="rounded-md border border-[var(--red-border)] bg-[var(--red-bg)] px-3 py-2 text-[12px] text-[var(--red)]">
          {validationError}
        </div>
      )}

      <div className="sticky bottom-0 -mx-6 flex items-center justify-between border-t border-[var(--border-subtle)] bg-[var(--bg-base)] px-6 py-3">
        <Button
          type="button"
          variant="outline"
          onClick={handleDelete}
          disabled={del.isPending || update.isPending}
          className="text-[var(--red)]"
          data-testid="editor-delete"
        >
          <Trash2 size={14} className="mr-1" />
          Delete script
        </Button>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => setState(initial)}
            disabled={!isDirty || update.isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleSave}
            disabled={update.isPending || !isDirty}
            data-testid="editor-save"
          >
            {update.isPending ? (
              <>
                <Loader2 size={14} className="mr-1 animate-spin" />
                Saving…
              </>
            ) : (
              "Save changes"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
