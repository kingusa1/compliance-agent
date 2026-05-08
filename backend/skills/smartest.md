# Smartest Energy — Supplier-Specific Compliance Addenda

Inherits all rules from `_general.md`.

## Strictness overrides

- **Verbatim contract length statement.** Smartest requires the agent
  to state the contract length as a number of months (e.g. "thirty-six
  months") rather than a years phrasing. Years-only is `partial`.

## Wording exceptions

- (none recorded yet)

## Common rejection patterns

- "Customer not informed about ToS" → `COMPLIANCE_ERROR`
- "Wrong unit rate stated on call" → `PRICING_ERROR`

## Remediation preferences

- Smartest accepts `CONFIRMATION_CALL` for contract-length disputes if
  the original recording exists and is clear.
