# Affect Energy — Supplier-Specific Compliance Addenda

Inherits all rules from `_general.md`. Apply these supplier-specific
overrides where they differ from the generic playbook.

## Strictness overrides

- **DPA wording.** Affect requires the agent to read the DPA verbatim
  before any contract term is discussed. Paraphrasing is `partial`, not
  `pass`, on the `DPA confirmation read` checkpoint.

## Wording exceptions

- (none recorded yet — refine as ops data accumulates)

## Common rejection patterns

- "Customer not asked to confirm direct debit details" → `PROCESS_FAILURE`
- "Rate quoted differs from supplier locked-in rate" → `PRICING_ERROR`

## Remediation preferences

- Affect typically accepts `AMENDMENT_CALL` for verbal-rate corrections
  without re-issuing a DocuSign envelope.
