---
created: 2026-05-10
updated: 2026-05-10
tags: [state, issues, gotchas]
---

# Known issues / gotchas

## High signal — fix later

### LLM occasionally extracts wrong customer name
The Passover call originally had `customer_name = "Afaq"` (which is actually the broker, mis-detected). After the Quality Agent run, it's been corrected — but **per-call** detect_names is the failure mode. Solution: add a Customer-Name Specialist Agent (single-purpose, single-call) — see [[03_AI_Pipeline/Future_Agents]].

### Empty-checkpoints scripts
Multiple seed scripts in DB have `checkpoints: "[]"`. Pipeline auto-falls-through to V1 third-party-disclosure rule, but the score is 0/3 (universal) instead of N/M (script-specific). Fix: run `backend/scripts/seed_compliance_data.py --apply` after dropping the markdown extracts at `.planning/phase2-docs/`.

### Old transcripts don't re-label on retry
`format_diarized_transcript` only runs during Step 2 (`_step_transcribe`). On `/retry`, the cached `Call.transcript` is reused. So OLD calls that were transcribed BEFORE the speaker-label fix still show wrong labels. Workaround: clear `Call.transcript` (and `Call.word_data`) before retry to force re-transcription. Lower priority: most users will never see this since fresh uploads work correctly.

### Failed call still shows as "(auto-detect pending 42a89a59)"
The early Crosby grange call from before the OpenRouter key fix failed during pipeline and never got a customer rename. Trash it via the new delete button (`/calls` → hover row → trash icon), or it'll sit there cosmetically.

## Low signal — be aware

### Vercel auto-deploys can theoretically still hijack alias
Even with the rootDirectory fix, Vercel deploys EVERY commit. Most of the time these now succeed (real ~1m builds with actual content). If anything goes back to 0ms empty, suspect rootDirectory drift first. Quick diagnose:
```bash
TOKEN=$(cat "$APPDATA/com.vercel.cli/Data/auth.json" | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -s "https://api.vercel.com/v9/projects/prj_eHIyIFyxusNdCd6mR9Ff469NrcKO?teamId=team_fNQJtpp1M2P2dkcoWvQIziCr" -H "Authorization: Bearer $TOKEN" | python -c "import json,sys;d=json.load(sys.stdin);print('rootDirectory:', d.get('rootDirectory'));print('framework:', d.get('framework'))"
```
Should be `frontend-v3` and `nextjs`. If not, PATCH it back.

### Local IDE shows sqlalchemy import error
Pylance/Pyright in VS Code says "Cannot find module sqlalchemy.orm" because the local Windows Python interpreter doesn't have it installed. Runtime is on Railway with `pip install -r requirements.txt` — sqlalchemy IS installed there. Ignore the IDE warning. (Don't try to "fix" it by removing the import.)

### Vercel CLI alias on Windows needs `NODE_OPTIONS=--use-system-ca`
Otherwise certificate verification fails. Already documented in [[01_Project/Deploy]].

### Manual `vercel deploy --prod` from `frontend-v3/` no longer works
After the rootDirectory fix, the CLI tries to find `frontend-v3/frontend-v3/` and fails. Run from REPO ROOT instead, or use the API-triggered deploy pattern (also in [[01_Project/Deploy]]).

## False alarms (NOT bugs)

### "Failed to connect" on `claude mcp list` for Playwright
Means the current session was started before the MCP was registered. Restart the Claude Code session — it'll connect on the next start. (Documented at [[06_Operations/Deploy_Commands]] section "MCP".)

### `deal_id=NONE` in `/api/calls?limit=10` list view
The list-view projection doesn't include deal_id. The full call detail endpoint `/api/calls/<id>` does include it. Don't panic from the list view alone.

### Agent page shows "no data" for Parat
Parat has 1 completed call but **0 dead rejections**. The agent page's main tab is "Recent flags" which sources from `dead_rejections`. Empty list is correct, just looks empty. Could add an EmptyState component for clarity.
