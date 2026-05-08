"use client";

/**
 * CheckpointEditor — collapsible per-checkpoint editor used by both the
 * upload preview (PreviewPanel) and the saved-script editor on
 * /scripts/[id]. Renders five inputs:
 *
 *   1. Name (text)
 *   2. Required text (textarea)
 *   3. Key phrases (tag input — Enter to add, × to remove)
 *   4. Strictness select (mandatory / customer_yes / meaning_for_meaning)
 *   5. Customer-response-required toggle
 *
 * Plus reorder controls (up/down arrows) + delete. We deliberately use
 * simple integer-index reordering rather than @dnd-kit to keep this
 * commit narrow. The header (collapsed view) shows the section number,
 * name, strictness pill, and a one-line preview of `required`.
 */
import { useState, type KeyboardEvent } from "react";
import { ChevronDown, ChevronUp, Trash2, X, Plus } from "lucide-react";

import { Pill } from "@/components/design/Pill";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  STRICTNESS_VALUES,
  STRICTNESS_LABEL,
  STRICTNESS_TONE,
  type CheckpointFormValues,
} from "@/lib/schemas/script";

export type CheckpointEditorProps = {
  index: number;
  total: number;
  value: CheckpointFormValues;
  onChange: (next: CheckpointFormValues) => void;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
  onRemove?: () => void;
  /** When true the editor mounts already expanded (e.g. brand-new row). */
  defaultExpanded?: boolean;
};

export function CheckpointEditor({
  index,
  total,
  value,
  onChange,
  onMoveUp,
  onMoveDown,
  onRemove,
  defaultExpanded = false,
}: CheckpointEditorProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [phraseDraft, setPhraseDraft] = useState("");

  const setField = <K extends keyof CheckpointFormValues>(
    key: K,
    next: CheckpointFormValues[K],
  ) => onChange({ ...value, [key]: next });

  function commitPhrase() {
    const trimmed = phraseDraft.trim();
    if (!trimmed) return;
    if (value.key_phrases.includes(trimmed)) {
      setPhraseDraft("");
      return;
    }
    setField("key_phrases", [...value.key_phrases, trimmed]);
    setPhraseDraft("");
  }

  function onPhraseKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitPhrase();
    } else if (e.key === "Backspace" && !phraseDraft && value.key_phrases.length) {
      setField("key_phrases", value.key_phrases.slice(0, -1));
    }
  }

  function removePhrase(i: number) {
    setField(
      "key_phrases",
      value.key_phrases.filter((_, idx) => idx !== i),
    );
  }

  const sectionLabel = value.section ?? index + 1;

  return (
    <div
      data-testid={`checkpoint-${index}`}
      className="overflow-hidden rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)]"
    >
      {/* Header — always visible, click to toggle */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-[var(--bg-elev2)]"
      >
        <span
          className="font-mono text-[11px] text-[var(--text-dim)]"
          style={{ minWidth: 24 }}
        >
          {String(sectionLabel).padStart(2, "0")}
        </span>
        <span className="flex-1 truncate text-[13px] font-medium text-[var(--text-primary)]">
          {value.name || <em className="text-[var(--text-muted)]">Unnamed checkpoint</em>}
        </span>
        <Pill tone={STRICTNESS_TONE[value.strictness]} dot>
          {STRICTNESS_LABEL[value.strictness]}
        </Pill>
        {expanded ? (
          <ChevronUp size={14} className="text-[var(--text-dim)]" />
        ) : (
          <ChevronDown size={14} className="text-[var(--text-dim)]" />
        )}
      </button>

      {/* Body — expanded form */}
      {expanded && (
        <div className="border-t border-[var(--border-subtle)] px-3 py-3 space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_180px]">
            <div>
              <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
                Name
              </label>
              <Input
                value={value.name}
                onChange={(e) => setField("name", e.target.value)}
                placeholder="Recording Disclosure"
                data-testid={`cp-name-${index}`}
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
                Strictness
              </label>
              <Select
                value={value.strictness}
                onValueChange={(v) =>
                  v && setField("strictness", v as CheckpointFormValues["strictness"])
                }
              >
                <SelectTrigger data-testid={`cp-strictness-${index}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STRICTNESS_VALUES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {STRICTNESS_LABEL[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
              Required text
            </label>
            <Textarea
              value={value.required}
              onChange={(e) => setField("required", e.target.value)}
              rows={3}
              placeholder="Agent must state their name, that they are an independent intermediary, and that calls are recorded for compliance."
              data-testid={`cp-required-${index}`}
            />
          </div>

          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-wider text-[var(--text-dim)]">
              Key phrases ({value.key_phrases.length})
            </label>
            <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-2 py-1.5">
              {value.key_phrases.map((p, i) => (
                <span
                  key={`${p}-${i}`}
                  className="inline-flex items-center gap-1 rounded bg-[var(--bg-elev3)] px-1.5 py-0.5 text-[12px] text-[var(--text-primary)]"
                >
                  {p}
                  <button
                    type="button"
                    aria-label={`Remove phrase ${p}`}
                    onClick={() => removePhrase(i)}
                    className="text-[var(--text-dim)] hover:text-[var(--red)]"
                  >
                    <X size={10} />
                  </button>
                </span>
              ))}
              <input
                value={phraseDraft}
                onChange={(e) => setPhraseDraft(e.target.value)}
                onKeyDown={onPhraseKeyDown}
                onBlur={commitPhrase}
                placeholder="Type and press Enter…"
                className="min-w-[140px] flex-1 bg-transparent text-[12px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-dim)]"
                data-testid={`cp-phrase-input-${index}`}
              />
            </div>
          </div>

          <label className="flex cursor-pointer items-center gap-2 text-[12px] text-[var(--text-muted)]">
            <input
              type="checkbox"
              checked={value.customer_response_required}
              onChange={(e) => setField("customer_response_required", e.target.checked)}
              data-testid={`cp-customer-${index}`}
            />
            <span>Customer must respond (e.g. say &quot;yes / okay&quot;)</span>
          </label>

          {/* Footer controls: reorder + remove */}
          <div className="flex items-center justify-between border-t border-[var(--border-subtle)] pt-3">
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!onMoveUp || index === 0}
                onClick={onMoveUp}
                aria-label="Move up"
              >
                <ChevronUp size={14} />
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!onMoveDown || index === total - 1}
                onClick={onMoveDown}
                aria-label="Move down"
              >
                <ChevronDown size={14} />
              </Button>
            </div>
            {onRemove && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={onRemove}
                data-testid={`cp-remove-${index}`}
                className="text-[var(--red)] hover:text-[var(--red)]"
              >
                <Trash2 size={14} className="mr-1" />
                Remove
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Helper used by both forms to insert a blank checkpoint at end of list. */
export function blankCheckpoint(section: number): CheckpointFormValues {
  return {
    section,
    name: "",
    required: "",
    key_phrases: [],
    customer_response_required: false,
    strictness: "meaning_for_meaning",
  };
}

/** Tiny inline button "+ Add checkpoint" used by both pages. */
export function AddCheckpointButton({ onAdd }: { onAdd: () => void }) {
  return (
    <Button type="button" variant="outline" size="sm" onClick={onAdd} data-testid="add-checkpoint">
      <Plus size={14} className="mr-1" />
      Add checkpoint
    </Button>
  );
}
