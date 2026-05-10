---
created: 2026-05-10
updated: 2026-05-10
tags: [ops, credentials, secrets]
---

# Credentials map (NOT the keys themselves)

> This file lists WHERE each secret lives. The values are NOT in the brain (vault). Read the value at runtime; don't paste into here.

## Vercel CLI
- **Auth file:** `C:/Users/kingu/AppData/Roaming/com.vercel.cli/Data/auth.json`
- **Token field:** `.token`
- Used for `curl ... -H "Authorization: Bearer $TOKEN"` against api.vercel.com

## Railway CLI
- Linked via `railway link` to project `compliance-agent-backend`, service `compliance-agent`
- Token cached at `~/.railway/` (managed by CLI, don't touch)

## Backend env (set on Railway via dashboard or `railway variables --set`)
- `OPENROUTER_API_KEY` — `sk-or-v1-...` (Mohamed's OpenRouter account, has Opus 4.7 access + credits)
- `OPENROUTER_MODEL` = `anthropic/claude-opus-4.7`
- `DEEPGRAM_API_KEY` — Deepgram (EU region account)
- `DEEPGRAM_BASE_URL` = `https://api.eu.deepgram.com`
- `DEEPGRAM_LANGUAGE` = `en-GB`
- `DATABASE_URL` — `postgresql+psycopg2://postgres.zcmdsblqbgatsrofptsq:<PASSWORD>@aws-1-ap-south-1.pooler.supabase.com:6543/postgres`
- `SUPABASE_URL` = `https://zcmdsblqbgatsrofptsq.supabase.co`
- `SUPABASE_ANON_KEY` — public anon key
- `SUPABASE_SERVICE_ROLE_KEY` — service role
- `INNGEST_SIGNING_KEY` — `signkey-prod-...` (Inngest dashboard → Manage → Signing Keys)
- `INNGEST_EVENT_KEY` — Inngest dashboard → Manage → Event Keys
- `INNGEST_ENV` = `production`
- `USE_INNGEST_PIPELINE` = `false`

## Frontend env (set on Vercel via dashboard or `vercel env add`)
- `NEXT_PUBLIC_API_BASE_URL` = `https://compliance-agent-production-690e.up.railway.app`
- `NEXT_PUBLIC_SUPABASE_URL` — Supabase project URL
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` — Supabase anon key

## Project IDs / refs
- **Vercel project ID:** `prj_eHIyIFyxusNdCd6mR9Ff469NrcKO`
- **Vercel team ID:** `team_fNQJtpp1M2P2dkcoWvQIziCr`
- **GitHub repo:** `https://github.com/kingusa1/compliance-agent`
- **GitHub repo ID:** `1233382040`
- **Supabase project ref:** `zcmdsblqbgatsrofptsq`
- **Railway project:** `compliance-agent-backend` (workspace: mohamed hisham ismail)
- **Railway service:** `compliance-agent`

## Re-key procedure
If a secret leaks:
1. Rotate at the source (Supabase / Deepgram / OpenRouter / Inngest dashboard)
2. Update on Railway: `railway variables --set "KEY=new-value"`
3. Update on Vercel (dashboard or `vercel env add`)
4. Redeploy both
5. Verify health checks pass

## Don't write into this brain
The actual key values. Don't. Ever. The brain is committed to git via the project repo.
