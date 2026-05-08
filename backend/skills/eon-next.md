# E.ON Next Compliance Playbook

## Known aliases in transcripts
- E.ON Next, EON, Emix (mishearing of "E.ON Next")
- Wat Utilities / What Utilities — this is the BROKER name, not the supplier

## Supplier-specific rules

1. **Meaning-for-meaning is the norm.** If the agent conveys the
   requirement in different words, mark `pass`. Only mark `partial`
   if a specific required component is missing.

2. **Key phrases are guides, not templates.** Example equivalents:
   - "calls are taped" = "calls are recorded" = `pass`
   - "TPI" = "third party intermediary" = `pass`
   - "emix" = "E.ON Next" = `pass`

3. **Price format variations are all equivalent.**
   - "30p", "30 pence", "thirty pence", "thirty pence a day" are identical.
   - "£0.30/day" = `pass` for same content.

4. **VAT clause is satisfied by any mention of:**
   - VAT
   - climate change levy
   - green deal charges
   - "plus VAT" / "exclusive of VAT" / "before VAT"

5. **Cooling-off period for verbal contracts is 1 day, not 14.**
   If the agent says "one day to cancel" or "24 hours to cancel", that IS
   compliant for E.ON verbal contracts. 14-day cooling-off applies only
   to written contracts. Do NOT mark fail because you expected 14 days.

## Customer confirmation (customer_yes strictness)

E.ON customers typically confirm with: "yeah", "yep", "that's fine",
"go ahead", "no worries", "okay", "mmhmm".

The agent often asks "Is that okay?" or "Are you happy with that?"
Look for the customer response IMMEDIATELY after these prompts.

If the agent rushes past without waiting for confirmation, mark
`partial` even if there's a faint "mm".

What is NOT valid confirmation:
- silence
- agent continuing without pause
- customer asking a question ("what?", "sorry?")
- trailing "mm" with no following word
