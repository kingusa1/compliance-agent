---
created: 2026-05-10
updated: 2026-05-10
tags: [deploy, vercel, railway]
---

# Deploy

## Vercel — frontend
**Project:** `compliance-agent` under team `mohamed-hishams-projects-0b4feda9`
**Project ID:** `prj_eHIyIFyxusNdCd6mR9Ff469NrcKO`
**Team ID:** `team_fNQJtpp1M2P2dkcoWvQIziCr`
**GitHub Repo ID:** `1233382040`
**Production alias:** `compliance-agent-mu.vercel.app`
**Region:** `lhr1`

### Project settings (CRITICAL — these were the 404 root cause)
- `rootDirectory: "frontend-v3"` ← was `null`, fixed via API on 2026-05-10
- `framework: "nextjs"` ← was `null`, fixed via API on 2026-05-10
- Auto-deploys are ALLOWED on `main` (we've toggled this on/off; current setting honors GitHub pushes)

### Manual deploy (the way it always worked locally)
```bash
# From REPO ROOT now (because rootDirectory=frontend-v3):
cd compliance-agent-feat-wave5-deploy/compliance-agent-feat-wave5-deploy
NODE_OPTIONS=--use-system-ca vercel deploy --prod --yes --force --scope mohamed-hishams-projects-0b4feda9
```

> ⚠️ The `--use-system-ca` is required on Windows due to a Node CA-store mismatch with Vercel's API.

### API-triggered deploy (most reliable when CLI is confused)
```bash
TOKEN=$(cat "$APPDATA/com.vercel.cli/Data/auth.json" | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -X POST "https://api.vercel.com/v13/deployments?teamId=team_fNQJtpp1M2P2dkcoWvQIziCr&forceNew=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"compliance-agent","project":"prj_eHIyIFyxusNdCd6mR9Ff469NrcKO","gitSource":{"type":"github","ref":"main","repoId":1233382040},"target":"production"}'
```

### Re-alias after a deploy hijack
```bash
cd frontend-v3
NODE_OPTIONS=--use-system-ca vercel alias set https://compliance-agent-<DEPLOY-ID>-mohamed-hishams-projects-0b4feda9.vercel.app compliance-agent-mu.vercel.app --scope mohamed-hishams-projects-0b4feda9
```

## Railway — backend
**Project:** `compliance-agent-backend`
**Service:** `compliance-agent`
**URL:** https://compliance-agent-production-690e.up.railway.app
**Region:** EU West

### Manual deploy (the only way I deploy)
```bash
cd backend
railway up --service=compliance-agent --ci
```
- Uploads `backend/` as the build context
- Healthcheck `/healthz` (not `/readyz` — `/readyz` returns 503 on cold DB)
- Healthcheck timeout: 300s (alembic can take 60-90s on cold pooler)

### Top-level Dockerfile (for GitHub auto-deploys)
A second `Dockerfile` exists at REPO ROOT mirroring `backend/Dockerfile` with `backend/` prefix on COPY paths. This lets Railway's GitHub auto-deploy succeed when context is the whole repo. The `railway.json` at repo root carries `watchPatterns = ["backend/**", "Dockerfile", "railway.json"]` so frontend-only commits don't trigger a rebuild.

### Logs
```bash
cd backend && railway logs --build           # Build phase
cd backend && railway logs                   # Runtime
```

### Env (set via dashboard or `railway variables --set`)
See [[06_Operations/Credentials]].

## Supabase
**Project ref:** `zcmdsblqbgatsrofptsq`
**Region:** `ap-south-1`
**Database URL pattern:** `postgresql+psycopg2://postgres.zcmdsblqbgatsrofptsq:<PASSWORD>@aws-1-ap-south-1.pooler.supabase.com:6543/postgres`

Used for: Postgres (all app data), Storage (audio files in `compliance-audio` bucket), Auth (Supabase Auth tokens, frontend uses `@supabase/ssr`).

## Inngest
- `INNGEST_SIGNING_KEY` set on Railway
- `INNGEST_EVENT_KEY` set on Railway
- `INNGEST_ENV=production`
- `USE_INNGEST_PIPELINE=false` (currently — durable workflow code is gated behind this; legacy asyncio path is the default)
