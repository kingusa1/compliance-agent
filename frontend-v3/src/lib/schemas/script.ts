/**
 * zod schemas for the script-management forms (upload preview + detail
 * editor). These are the single source of truth for what the v3 UI
 * sends to POST /api/scripts and PUT /api/scripts/{id}.
 *
 * Backend pydantic model `ScriptCreate` accepts:
 *   supplier_name: str
 *   script_name: str
 *   version: Optional[str]
 *   mode: Optional[str]
 *   checkpoints: List[ScriptCheckpoint]
 *
 * ScriptCheckpoint:
 *   section: Optional[int]
 *   name: str
 *   required: str
 *   key_phrases: List[str] = []
 *   customer_response_required: bool = False
 *   strictness: str = "mandatory"   (we restrict to the 3 documented)
 */
import { z } from "zod";

export const STRICTNESS_VALUES = [
  "mandatory",
  "customer_yes",
  "meaning_for_meaning",
] as const;

export const MODE_VALUES = [
  "meaning_for_meaning",
  "word_for_word",
  "mandatory",
  "customer_yes",
] as const;

export const checkpointSchema = z.object({
  section: z.number().int().nullable().optional(),
  name: z.string().min(1, "Name is required"),
  required: z.string().min(1, "Required text is required"),
  key_phrases: z.array(z.string().min(1)).default([]),
  customer_response_required: z.boolean().default(false),
  strictness: z.enum(STRICTNESS_VALUES),
});

export type CheckpointFormValues = z.infer<typeof checkpointSchema>;

export const scriptSchema = z.object({
  supplier_name: z.string().min(1, "Supplier is required"),
  script_name: z.string().min(1, "Script name is required"),
  version: z.string().optional().or(z.literal("")),
  mode: z.string().min(1, "Mode is required"),
  checkpoints: z.array(checkpointSchema).min(1, "At least one checkpoint required"),
});

export type ScriptFormValues = z.infer<typeof scriptSchema>;

/** Human-friendly strictness labels used by pills + selects. */
export const STRICTNESS_LABEL: Record<(typeof STRICTNESS_VALUES)[number], string> = {
  mandatory: "Mandatory",
  customer_yes: "Customer ✓",
  meaning_for_meaning: "Meaning",
};

/** Pill tone (matches v3 Pill component) per strictness. */
export const STRICTNESS_TONE: Record<
  (typeof STRICTNESS_VALUES)[number],
  "amber" | "blue" | "emerald"
> = {
  mandatory: "amber",
  customer_yes: "blue",
  meaning_for_meaning: "emerald",
};
