---
created: 2026-05-10
updated: 2026-05-10
tags: [ai, agents, roadmap]
---

# Future Agents (multi-agent expansion)

## Already shipped
- [[03_AI_Pipeline/Quality_Agent|Quality Agent]] — cross-call identity resolver
- Per-checkpoint analyzer (`backend/app/checkpoint_analyzer.py`) — runs Opus on every script checkpoint
- Vulnerability detector (`backend/app/extraction/vulnerability.py`) — Ofgem TPI Code S2
- Pricing-mismatch detector (`backend/app/extraction/flags.py:derive_pricing_mismatch_flags`)
- Per-call name detector (`backend/app/analysis.py:detect_names`)
- Per-call business-name detector (`backend/app/business_detect.py`)
- Per-call supplier detector (`backend/app/analysis.py:detect_supplier`)
- Per-call script-variant picker (`backend/app/analysis.py:detect_script_variant`)

## Queued (next session)

### 1. Call-Type Classifier Agent (single-call)
Replaces filename heuristics + script-mode inference. Single-call decision: `lead_gen | closer | loa | amendment | c_call`. Strong system prompt with Watt-specific cues (decision-maker question = lead-gen marker, "agree to switch" = closer marker, etc.). Output: `{call_type, confidence, evidence_quote}`.

### 2. Decision-Maker Confirmation Agent
Compliance-critical: per Ofgem TPI Code, the broker MUST confirm the customer is the decision-maker before pricing/contracting. Single-purpose agent reads the transcript and returns:
```json
{ "confirmed": true|false, "confirmer_name": "...", "evidence_quote": "...", "passes_S5": true|false }
```
Feeds the S5 (Consent & Authority) standard.

### 3. Customer Intent Agent
Did the customer agree, decline, or stay uncertain? Three-class classifier with evidence quote. Critical for distinguishing "verbal contract complete" vs "lead only".

### 4. Verdict Reviewer Agent (meta)
Reads the per-checkpoint AI verdicts AND the call's pre-pass regex hits. Detects:
- Logically inconsistent verdicts (e.g. "third-party disclosed = pass" but evidence quote contradicts)
- Low-confidence patterns the analyzer should re-do
- Edge cases that need human review even if score > threshold

### 5. Data Enricher Agent
Post-finalize. Enriches the deal with derived data:
- Postcode → DNO region (for E.ON / EDF metering)
- MPAN/MPRN → check-digit validation
- Customer business name → SIC code via Companies House (if ever wired)
- £ value → sanity-check against quoted unit rate × estimated annual usage

### 6. Multi-agent Orchestrator (the user's big ask)
Agents share a `CallContext` object (Pydantic model). Each specialist writes its structured field; the orchestrator decides which agent to invoke next, when to retry with more context, when to defer to human review. Final `Verdict` is a consensus across agents.

Architecture sketch:
```
                ┌──────────────────────────────┐
                │  Coordinator                 │
                │  (reads CallContext, picks   │
                │   the next specialist)       │
                └─────┬────────────────────────┘
                      │
       ┌──────────────┼──────────────┬──────────────┐
       ▼              ▼              ▼              ▼
   Quality       Call-Type     Decision-Maker   Verdict
   Agent         Classifier    Confirmer        Reviewer
       │              │              │              │
       └──────────────┴──────────────┴──────────────┘
                      ▼
                CallContext (shared, append-only)
                      ▼
               Final Verdict
```

Pattern: Anthropic's "tools-as-API" model where each agent is a tool the coordinator can call. The coordinator's system prompt knows the cost and accuracy tradeoffs of each specialist and picks accordingly.

## Cost model
Each Opus 4.7 call ≈ $0.005-0.020 per call depending on transcript length. Quality Agent is 1 batched call (cheap relative to per-checkpoint analyzer which runs ~10 calls). Future agents should be designed as **single-purpose**, **single-call** so the orchestrator can choose minimum viable set per case.

## When to graduate to a new agent
Add an agent only when:
1. A class of error costs reviewer time (≥5min/case)
2. Heuristics give ≤80% accuracy
3. The decision is structured (vocabulary-validated JSON, not free text)
4. The same prompt would be ineffective on a single call

See [[04_Sessions/Decisions]] for the architectural decisions log.
