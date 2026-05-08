# Britannia Gas — Supplier-Specific Compliance Addenda

Inherits all rules from `_general.md`.

## Strictness overrides

- (none recorded yet)

## Wording exceptions

- (none recorded yet)

## Common rejection patterns

- "BACS denied — collect Direct Debit details" → `FAILED_CREDIT_CHECK`
  with `DD_MANDATE` remediation
- "Standing charge stated as zero" → `PRICING_ERROR`

## Remediation preferences

- Britannia is strict about `NEW_DOCUSIGN` — if any contract field
  changes, the envelope must be re-issued and re-signed.
