"use client";

/**
 * PreviewPanel — once useUploadScript() returns parsed checkpoints, the
 * dialog renders this panel for the user to fill in supplier_name /
 * script_name / mode and edit checkpoints inline before saving.
 *
 * State lives in the parent (UploadScriptDialog) so the parent owns the
 * Save / Discard mutation lifecycle and can reset on close. This panel
 * is purely presentational.
 */
import {
  CheckpointEditor,
  AddCheckpointButton,
  blankCheckpoint,
} from "./CheckpointEditor";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MODE_VALUES, type CheckpointFormValues } from "@/lib/schemas/script";

export type PreviewState = {
  supplier_name: string;
  script_name: string;
  version: string;
  mode: string;
  checkpoints: CheckpointFormValues[];
};

export type PreviewPanelProps = {
  value: PreviewState;
  onChange: (next: PreviewState) => void;
};

export function PreviewPanel({ value, onChange }: PreviewPanelProps) {
  const setField = <K extends keyof PreviewState>(key: K, next: PreviewState[K]) =>
    onChange({ ...value, [key]: next });

  const setCheckpoint = (i: number, cp: CheckpointFormValues) => {
    const next = value.checkpoints.slice();
    next[i] = cp;
    onChange({ ...value, checkpoints: next });
  };

  const removeCheckpoint = (i: number) => {
    onChange({
      ...value,
      checkpoints: value.checkpoints.filter((_, idx) => idx !== i),
    });
  };

  const move = (i: number, dir: -1 | 1) => {
    const next = value.checkpoints.slice();
    const j = i + dir;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    // Re-number `section` so the on-screen ordinals stay sequential.
    next.forEach((c, k) => (c.section = k + 1));
    onChange({ ...value, checkpoints: next });
  };

  const addCheckpoint = () => {
    onChange({
      ...value,
      checkpoints: [...value.checkpoints, blankCheckpoint(value.checkpoints.length + 1)],
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Supplier name
          </label>
          <Input
            value={value.supplier_name}
            onChange={(e) => setField("supplier_name", e.target.value)}
            placeholder="Scottish Power"
            data-testid="preview-supplier"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Script name
          </label>
          <Input
            value={value.script_name}
            onChange={(e) => setField("script_name", e.target.value)}
            placeholder="Acquisition TPI"
            data-testid="preview-script-name"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Version
          </label>
          <Input
            value={value.version}
            onChange={(e) => setField("version", e.target.value)}
            placeholder="Oct24"
            data-testid="preview-version"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
            Mode
          </label>
          <Select value={value.mode} onValueChange={(v) => setField("mode", v ?? "")}>
            <SelectTrigger data-testid="preview-mode">
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
          {value.checkpoints.length} checkpoint{value.checkpoints.length === 1 ? "" : "s"}
        </h3>
        <AddCheckpointButton onAdd={addCheckpoint} />
      </div>

      <div className="space-y-2">
        {value.checkpoints.map((cp, i) => (
          <CheckpointEditor
            key={i}
            index={i}
            total={value.checkpoints.length}
            value={cp}
            onChange={(next) => setCheckpoint(i, next)}
            onMoveUp={() => move(i, -1)}
            onMoveDown={() => move(i, 1)}
            onRemove={() => removeCheckpoint(i)}
          />
        ))}
        {value.checkpoints.length === 0 && (
          <div className="rounded-md border border-dashed border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-center text-[13px] text-[var(--text-muted)]">
            No checkpoints. Use &quot;Add checkpoint&quot; to start.
          </div>
        )}
      </div>
    </div>
  );
}
