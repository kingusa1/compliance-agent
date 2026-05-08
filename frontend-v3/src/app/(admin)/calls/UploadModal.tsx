"use client";

import { useRouter } from "next/navigation";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { L7Form, type L7FormPrefill } from "@/components/intake/L7Form";

/**
 * UploadModal — wraps L7Form in a shadcn Dialog. On success the form
 * fires `onSuccess(callId)` which closes the modal + redirects to
 * /calls/{id} so the reviewer can watch the pipeline progress.
 *
 * Used by:
 *   - /calls (admin) IntakeBar
 *   - /customers/[slug] UploadToCustomerButton (passes prefill + slug to
 *     lock the customer section)
 */
export interface UploadModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  prefill?: L7FormPrefill;
  customerSlug?: string;
  /** Override the default redirect-to-/calls/{id} behavior. */
  onSuccess?: (callId: string | undefined) => void;
}

export function UploadModal({
  open,
  onOpenChange,
  prefill,
  customerSlug,
  onSuccess,
}: UploadModalProps) {
  const router = useRouter();
  const customerName = prefill?.customer?.name;

  const handleSuccess = (callId: string | undefined) => {
    onOpenChange(false);
    if (onSuccess) {
      onSuccess(callId);
      return;
    }
    if (callId) router.push(`/calls/${callId}`);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="upload-modal"
        className="!max-w-3xl !w-[min(95vw,820px)] max-h-[90vh] overflow-y-auto"
      >
        <DialogHeader>
          <DialogTitle>
            {customerName ? `Upload call to ${customerName}` : "Upload & process call"}
          </DialogTitle>
          <DialogDescription>
            L7 metadata form · 22 fields across 3 sections
          </DialogDescription>
        </DialogHeader>
        <L7Form
          prefill={prefill}
          customerSlug={customerSlug}
          onSuccess={handleSuccess}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
