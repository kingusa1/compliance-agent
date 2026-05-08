# Total Gas & Power / TotalEnergies — Supplier-Specific Compliance Addenda

Inherits all rules from `_general.md`.

## Strictness overrides

- (none recorded yet)

## Wording exceptions

- "TotalEnergies" and "Total Gas and Power" are accepted interchangeably
  on the agent-introduction checkpoint.

## Common rejection patterns

- "Out-of-matrix pricing not confirmed by supplier" → `PRICING_ISSUE`
- "Customer never gave verbal consent to switch" → `COMPLIANCE_ERROR`

## Remediation preferences

- Total prefers `MANUAL_ADMIN_SUBMISSION` over portal flows when the
  contract is out-of-matrix.
