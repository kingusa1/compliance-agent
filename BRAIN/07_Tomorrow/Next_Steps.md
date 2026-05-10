---
created: 2026-05-10
updated: 2026-05-10
tags: [roadmap, next]
---

# Next steps (post-handover)

## Immediate (next 1-2 sessions)

### 1. Playwright MCP page-by-page audit
Once the user restarts Claude Code, the Playwright MCP comes online. First job:
- Navigate every route on `compliance-agent-mu.vercel.app`
- Screenshot each at 1280×720 and 375×667 (mobile)
- Check the accessibility tree for missing alt-text, no-label inputs, etc.
- Click through real flows (Upload, Claim, Override, Delete) and screenshot any issues
- File a structured visual-bug report

### 2. Multi-agent orchestrator
The user's big architectural ask: agents collaborate via a shared `CallContext`.

Step 1: Create `backend/app/agents/` directory
Step 2: Move `quality_agent.py` → `backend/app/agents/quality.py`
Step 3: Create specialist agents:
  - `call_type.py` — single-call classifier (lead_gen / closer / loa / amendment / c_call)
  - `decision_maker.py` — S5 (Consent & Authority) compliance gate
  - `customer_intent.py` — agreed / declined / uncertain
  - `verdict_reviewer.py` — meta-quality on per-checkpoint verdicts
  - `data_enricher.py` — postcode → DNO, MPAN check-digit, etc.
Step 4: Create `coordinator.py` with system prompt that knows each specialist's cost + accuracy
Step 5: Replace per-call detect_names / detect_supplier in `pipeline.py` with a `coordinator.run(call)` call

Pattern:
```python
class CallContext(BaseModel):
    call_id: str
    transcript: str
    extracted: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    agent_log: list[dict] = Field(default_factory=list)

# Each agent reads ctx, writes to ctx.extracted[<field>] and ctx.confidence[<field>],
# appends to ctx.agent_log. Coordinator routes based on which fields are filled.
```

### 3. Empty-checkpoints script seed
Run `backend/scripts/seed_compliance_data.py --apply` after dropping the markdown extracts at `.planning/phase2-docs/`. This populates the 15 scripts with real checkpoint definitions so the analyzer can score N/M instead of universal 3/3.

### 4. Failed call cleanup
The early `42a89a59-…` Crosby call is still showing as `(auto-detect pending …)`. Either:
- Delete via the new trash button in `/calls`
- OR retry it (now that the OpenRouter key works) and let auto-resolve clean it up

## Medium-term

### 5. Re-transcription on retry (when explicitly requested)
Add a query param `?retranscribe=true` to `/api/calls/{id}/retry` that clears `Call.transcript` + `Call.word_data` before re-running. Otherwise retries reuse cached transcripts and don't pick up the speaker-label fix on old calls.

### 6. Customer-name specialist agent
Currently `detect_business_name` is a single-call LLM and produces inconsistent names. Replace with:
- A specialist that takes `(transcript, call.customer_name human, agent_name, supplier)` and returns canonical business name with confidence
- Used at upload time AND by the Quality Agent

### 7. Sentry / Honeybadger
Already mentioned in earlier sessions but not wired. Backend (`SENTRY_DSN_BACKEND`) + frontend (`SENTRY_DSN_FRONTEND`) → real error tracking instead of stdout-only logs.

### 8. Inngest enable
Flip `USE_INNGEST_PIPELINE=true`. Already gated behind env. Test that durable workflows fire on `CALL_UPLOADED` event and produce the same outcomes as the asyncio path.

## Long-term

### 9. Multi-supplier portal-batch upload
Watt operates by bundling fixed rejections into supplier-portal CSVs. Build the export pipeline.

### 10. Reviewer training playbook
The /guide page is comprehensive but text-only. Add screen-recordings of common reviewer workflows.

### 11. Compliance heatmap
A `/observability/heatmap` view showing per-agent / per-supplier / per-month compliance rates with drill-down.

## Don't bother yet
- Mobile reviewer flow (desktop is the only target)
- Multi-tenant (Watt-only product)
- Real-time call recording / SIP integration (out of scope)
