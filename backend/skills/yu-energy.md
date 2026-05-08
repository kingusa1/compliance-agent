# Yu Energy — Supplier-Specific Compliance Addenda

Inherits all rules from `_general.md`.

## Strictness overrides

- **Yu credit-check fallback.** Yu Energy frequently rejects on credit
  grounds — if the call mentions BACS denial or DD failure, classify as
  `FAILED_CREDIT_CHECK` with `DD_MANDATE` remediation rather than
  `PROCESS_FAILURE`.

## Wording exceptions

- "Yu" and "Yu Energy Retail Ltd" are accepted interchangeably on the
  agent-introduction checkpoint.

## Common rejection patterns

- "BACS has been denied please obtain Direct Debit details" → `FAILED_CREDIT_CHECK`
- "DocuSign sent to wrong signer" → `DOCUSIGN_ERROR`

## Remediation preferences

- Yu accepts `RESELL_TO_OTHER_SUPPLIER` when credit check fails twice.
