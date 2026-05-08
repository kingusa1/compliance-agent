"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { useEditTrackerRow } from "@/lib/mutations/tracker";
import type { TrackerFieldSource } from "@/lib/queries/tracker";

import { SourceBadge } from "./SourceBadge";

type InlineEditCellProps = {
  rejectionId: string;
  field: string;
  value: string | null;
  source: TrackerFieldSource;
  options?: ReadonlyArray<string>;
  /** When true, hide the source badge (used for read-only columns where the
   *  badge would be redundant alongside the parent's own indicator). */
  hideBadge?: boolean;
  /** Optional custom display renderer for non-editing mode. Used by cells
   *  that wrap a chip (CategoryChip, StatusPipelinePill) so the chip
   *  renders in display mode and a plain <select> appears only while
   *  editing. */
  renderDisplay?: (value: string | null) => ReactNode;
};

export function InlineEditCell({
  rejectionId,
  field,
  value,
  source,
  options,
  hideBadge,
  renderDisplay,
}: InlineEditCellProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(value ?? "");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const selectRef = useRef<HTMLSelectElement | null>(null);
  const edit = useEditTrackerRow();

  useEffect(() => {
    setDraft(value ?? "");
  }, [value]);

  useEffect(() => {
    if (editing) {
      if (options && selectRef.current) selectRef.current.focus();
      else if (!options && inputRef.current) inputRef.current.focus();
    }
  }, [editing, options]);

  const commit = () => {
    if (draft !== (value ?? "")) {
      edit.mutate({ rejectionId, fields: { [field]: draft || null } });
    }
    setEditing(false);
  };

  if (editing) {
    if (options) {
      return (
        <select
          ref={selectRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setEditing(false);
          }}
          className="bg-[var(--bg-elev2)] border border-[var(--border-subtle)] rounded px-1 py-0.5 text-[12px]"
        >
          <option value="">—</option>
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      );
    }
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
        className="bg-[var(--bg-elev2)] border border-[var(--border-subtle)] rounded px-1 py-0.5 text-[12px] w-full"
      />
    );
  }

  return (
    <span
      onClick={() => setEditing(true)}
      className="cursor-text inline-flex items-center hover:bg-[var(--bg-elev2)] rounded px-1"
    >
      {renderDisplay ? renderDisplay(value) : <span>{value ?? "—"}</span>}
      {!hideBadge && <SourceBadge source={source} />}
    </span>
  );
}
