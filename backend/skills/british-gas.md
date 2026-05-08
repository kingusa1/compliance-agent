# British Gas Compliance Playbook

## Known aliases
- British Gas, BG, BGL, "British Gas Lite", "British Gas Business"

## Supplier-specific rules (calibrated from 15 benchmark tests)

1. **Product name precision matters.**
   - "Zero Carbon" = renewable + nuclear mix.
   - "100% Natural Renewable" = renewable only (no nuclear).
   - If the script requires "100% Natural Renewable" and the agent says
     "mix of renewable and nuclear", that is a `fail`, not `partial`.

2. **Pricing checkpoint requires ALL THREE components:**
   - standing charge (daily)
   - unit rate (per kWh)
   - contract end date
   If ANY one is missing, mark `partial` (not `pass`).

3. **Renewal terms need specifics.**
   - "We'll be in touch before renewal" → `partial`.
   - "Your contract ends 20 May 2029, we'll write 60 days before with your
     renewal offer" → `pass`.

4. **Deemed rates warning.**
   If the script mentions deemed/out-of-contract rates, the agent MUST
   have explicitly warned that rates go up after contract ends. Implicit
   mention is `partial`.

5. **Key phrases are guides, not templates.** Meaning match is fine for
   `mandatory` checkpoints.

## Customer confirmation

Standard list applies: "yes", "yeah", "yep", "okay", "sure", "that's fine",
"go ahead", "happy with that".
