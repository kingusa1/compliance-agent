"use client";

/**
 * UploadScriptDialog — file dropzone → useUploadScript() → PreviewPanel
 * → useSaveScript() → invalidate ["scripts"] → toast → close.
 *
 * The backend parser (POST /api/scripts/upload) only extracts a flat
 * list of checkpoints from the source PDF/DOCX/MD. The user supplies
 * supplier_name / script_name / mode / version manually before hitting
 * Save.
 */
import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Upload, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useUploadScript, useSaveScript } from "@/lib/mutations/scripts";
import { scriptSchema, type ScriptFormValues } from "@/lib/schemas/script";

import { PreviewPanel, type PreviewState } from "./PreviewPanel";

const ACCEPT = ".pdf,.docx,.doc,.md,.markdown,.txt";

export type UploadScriptDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const EMPTY_PREVIEW: PreviewState = {
  supplier_name: "",
  script_name: "",
  version: "",
  mode: "meaning_for_meaning",
  checkpoints: [],
};

export function UploadScriptDialog({ open, onOpenChange }: UploadScriptDialogProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const upload = useUploadScript();
  const save = useSaveScript();

  function reset() {
    setPreview(null);
    setValidationError(null);
    upload.reset();
    save.reset();
    if (fileRef.current) fileRef.current.value = "";
  }

  function handleClose(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  async function ingestFile(file: File) {
    setValidationError(null);
    try {
      const res = await upload.mutateAsync(file);
      // Seed preview state from parsed checkpoints. Backend parser does
      // NOT extract names — we leave those blank for the user.
      setPreview({
        supplier_name: "",
        script_name: file.name.replace(/\.[^.]+$/, ""),
        version: "",
        mode: "meaning_for_meaning",
        checkpoints: res.checkpoints.map((cp, i) => ({
          section: cp.section ?? i + 1,
          name: cp.name ?? "",
          required: cp.required ?? "",
          key_phrases: cp.key_phrases ?? [],
          customer_response_required: cp.customer_response_required ?? false,
          strictness: cp.strictness ?? "meaning_for_meaning",
        })),
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      toast.error(msg);
    }
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) void ingestFile(f);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void ingestFile(f);
  }

  async function handleSave() {
    if (!preview) return;
    setValidationError(null);
    const parsed = scriptSchema.safeParse(preview as unknown as ScriptFormValues);
    if (!parsed.success) {
      setValidationError(parsed.error.issues[0]?.message ?? "Validation failed");
      return;
    }
    try {
      await save.mutateAsync({
        supplier_name: parsed.data.supplier_name,
        script_name: parsed.data.script_name,
        version: parsed.data.version || null,
        mode: parsed.data.mode,
        checkpoints: parsed.data.checkpoints,
      });
      toast.success("Script saved");
      handleClose(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Save failed";
      toast.error(msg);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="!max-w-3xl !w-[min(95vw,900px)] max-h-[90vh] overflow-y-auto"
        data-testid="upload-script-dialog"
      >
        <DialogHeader>
          <DialogTitle>Upload script</DialogTitle>
          <DialogDescription>
            Upload a PDF, DOCX, or Markdown compliance script. The parser extracts
            checkpoints; review and edit them before saving.
          </DialogDescription>
        </DialogHeader>

        {/* Step 1: Dropzone (only visible until preview exists) */}
        {!preview && (
          <div
            data-testid="upload-dropzone"
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed p-10 text-center transition-colors ${
              isDragging
                ? "border-[var(--emerald)] bg-[var(--emerald-bg)]"
                : "border-[var(--border-subtle)] bg-[var(--bg-elev1)] hover:bg-[var(--bg-elev2)]"
            }`}
          >
            {upload.isPending ? (
              <>
                <Loader2 size={32} className="mb-3 animate-spin text-[var(--text-muted)]" />
                <div className="text-[13px] text-[var(--text-primary)]">Parsing script…</div>
                <div className="mt-1 text-[12px] text-[var(--text-muted)]">
                  This can take 10–30s for long PDFs.
                </div>
              </>
            ) : (
              <>
                <Upload size={32} className="mb-3 text-[var(--text-muted)]" />
                <div className="text-[14px] font-medium text-[var(--text-primary)]">
                  Drop file here or click to browse
                </div>
                <div className="mt-1 text-[12px] text-[var(--text-muted)]">
                  PDF · DOCX · MD · TXT
                </div>
              </>
            )}
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              onChange={onFileChange}
              className="hidden"
              data-testid="upload-input"
            />
          </div>
        )}

        {/* Step 2: Preview + edit */}
        {preview && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-3 py-2 text-[12px] text-[var(--text-muted)]">
              <FileText size={14} />
              <span>
                {preview.checkpoints.length} checkpoint
                {preview.checkpoints.length === 1 ? "" : "s"} extracted. Review and
                save.
              </span>
            </div>
            <PreviewPanel value={preview} onChange={setPreview} />
            {validationError && (
              <div className="rounded-md border border-[var(--red-border)] bg-[var(--red-bg)] px-3 py-2 text-[12px] text-[var(--red)]">
                {validationError}
              </div>
            )}
          </div>
        )}

        <DialogFooter className="flex-row sm:justify-between">
          <Button
            type="button"
            variant="outline"
            onClick={() => (preview ? reset() : handleClose(false))}
            disabled={upload.isPending || save.isPending}
          >
            {preview ? "Discard & re-upload" : "Cancel"}
          </Button>
          {preview && (
            <Button
              type="button"
              onClick={handleSave}
              disabled={save.isPending}
              data-testid="upload-save"
            >
              {save.isPending ? (
                <>
                  <Loader2 size={14} className="mr-1 animate-spin" />
                  Saving…
                </>
              ) : (
                "Save script"
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
