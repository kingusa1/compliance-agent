# Cost optimisation runbook (Wave 4)

Two flags govern LLM cost on the analyse path:

| Flag | Default after Wave 4 | What it does |
|---|---|---|
| `use_agent_analyzer` | `True` | Run Gemini Flash first, escalate to Sonnet only on low-confidence checkpoints. |
| `embedding_prefilter_enabled` | `True` | Drop checkpoints whose top transcript-chunk cosine similarity is below `embedding_prefilter_threshold` (default 0.35) before the LLM fan-out. |

Either can be disabled in prod via env var without code change. Both are A/B-gated: parity ≥ 98 % vs. baseline on a 50-call sample is required before flipping defaults. Re-run the parity check after any data drift or model upgrade.

## Running the A/B parity harness

```bash
# 1. Seed the sample. Either pick the 50 most-recent calls automatically:
cd /Users/gomaa/Documents/Compliance/backend && \
  python -m scripts.ab_parity --sample-size 50 --out ab-50.json

# Or pin an explicit list:
cd /Users/gomaa/Documents/Compliance/backend && \
  python -m scripts.ab_parity --calls c1,c2,...,c50 --out ab-50.json
```

The script:
1. Picks N calls with non-null `transcript` and `script_id`.
2. Runs the `_step_analyze_checkpoints → _step_score → _step_finalize` pipeline twice per call — once with both flags off (baseline), once with both flags on (candidate).
3. Diffs the resulting `compliance_status`. Computes parity %.
4. Writes `ab-50.json` with sample_size, parity_pct, matches, mismatches, and a per-call diff list.
5. Exits 0 if parity ≥ `--threshold` (default 98.0), else 1.

Cost note: the harness performs **2N full analyse runs** against the live LLM provider. Budget accordingly. ~50 calls × 2 ≈ 100 analyse runs. Manual smoke; do not invoke from CI.

## Flag flip checklist

Before flipping the prod defaults in `app/config.py`:

- [ ] A/B run completed against ≥50 calls.
- [ ] Parity ≥ 98 %.
- [ ] All mismatches reviewed manually — none of them flip a `pass` to `fail` on a checkpoint that auditors would accept.
- [ ] Mean LLM cost-per-call drop verified ≥ 5× via the cost dashboard (Wave 2 LLM dashboard panel "calls/min" before vs after).
- [ ] Rollback plan: env vars `USE_AGENT_ANALYZER=false` and `EMBEDDING_PREFILTER_ENABLED=false` restore prior behaviour without redeploy.

## Rollback

If a regression appears after the flag flip:

```bash
# On the Contabo VPS
echo "USE_AGENT_ANALYZER=false" >> /opt/compliance/.env
echo "EMBEDDING_PREFILTER_ENABLED=false" >> /opt/compliance/.env
docker compose restart compliance-backend
```

No code change required. Inngest functions pick up the env override on the next event.

## Tuning `embedding_prefilter_threshold`

Default 0.35 was chosen to drop ~30 % of checkpoints on typical sales calls without losing recall on edge cases. To tune:

1. Run the parity harness at increasing thresholds: 0.30, 0.35, 0.40, 0.45.
2. Plot parity_pct vs cost_drop. Pick the highest threshold where parity stays ≥ 98 %.
3. Update `embedding_prefilter_threshold` in `.env` (no code change).

Threshold above ~0.55 starts losing legitimate checkpoints — stop tuning there.

## Observability

Wave 2 dashboards already cover the cost story:
- **LLM dashboard** → `llm_calls_total{escalated="true"}` rate (escalation rate after Wave 4 should be ≪ pre-Wave-4 baseline).
- **Pipeline dashboard** → `analyze_checkpoints` step duration p50/p95 should drop with the pre-filter on.
- Logs filtered by `PREFILTER kept=…` give per-call evidence the pre-filter ran.
