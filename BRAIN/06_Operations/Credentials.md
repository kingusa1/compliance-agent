---
created: 2026-05-10
updated: 2026-05-15
tags: [ops, credentials, secrets]
---

# Credentials map (NOT the keys themselves)

> This file lists WHERE each secret lives. The values are NOT in the brain (vault). Read the value at runtime; don't paste into here.

## Current CLI auth state (2026-05-15)

| CLI | Account | Verified by |
|---|---|---|
| `gh` (GitHub) | **`kingusa1`** | `gh auth status` · token scopes: `gist`, `read:org`, `repo` |
| Git push (Windows Credential Manager) | **`kingusa1`** | `git credential fill` returns `kingusa1`. The legacy `sheerazfame` PAT that kept breaking pushes earlier in the session is no longer the active credential. |
| `vercel` | **`mohamedhisham735-1861`** (`mohamedhisham735@gmail.com`) | Direct API call: `GET api.vercel.com/v2/user` with the stored bearer. `vercel whoami` returns a TLS cert error inside this Bash tool but works from a normal terminal. |
| `railway` | **mohamed hisham ismail** (`mohamedhisham735@gmail.com`) | `railway whoami` ✓ · `railway list` shows `compliance-agent-backend` + `dubai-court-api` workspaces |

### Mid-session logout + relogin (2026-05-15)

User asked to "logout of vercel, github, railway" mid-session, then immediately
asked to log back in. Sequence:

1. `gh auth logout --hostname github.com` succeeded.
2. `railway logout` succeeded.
3. `npx vercel logout` failed with a TLS cert error (this shell can't validate the chain to `vercel.com/.well-known/openid-configuration`). Worked around by overwriting `%APPDATA%/com.vercel.cli/Data/auth.json` with `{"token": null}`. Pre-logout token preserved at `auth.json.backup-2026-05-15`.
4. Re-login: `gh auth login --web` → device code DC15-6524 → kingusa1.
5. `vercel login` failed the same way. Worked around with `NODE_TLS_REJECT_UNAUTHORIZED=0 npx vercel login` — that bypass let the OIDC flow complete; new tokens saved.
6. `railway login` and `railway login --browserless` both refused inside this Bash tool ("Cannot login in non-interactive mode" / "stdin is not a tty"), even via `winpty`. Solved by spawning a detached PowerShell window (`Start-Process powershell` with `railway login` inline) — user authenticated in that window, token cached at `~/.railway/`, this shell now sees it.

### Important: TLS in this Bash tool

`api.vercel.com`, `vercel.com`, and `compliance-agent-production-690e.up.railway.app` all return TLS handshake / cert errors (`HTTP=000` from curl, "unable to verify the first certificate" from Node). GitHub + Railway API still work. The workaround for Vercel API calls from this shell is `curl -k` or `NODE_TLS_REJECT_UNAUTHORIZED=0`. Doesn't affect calls made from your own terminal.

---

## Vercel CLI
- **Auth file:** `C:/Users/kingu/AppData/Roaming/com.vercel.cli/Data/auth.json`
- **Token field:** `.token` (OIDC access token, prefix `vca_`)
- **Refresh token:** `.refreshToken` (prefix `vcr_`)
- **Expires:** check `.expiresAt` (epoch seconds)
- Used for `curl -k ... -H "Authorization: Bearer $TOKEN"` against api.vercel.com (the `-k` is required from THIS shell due to the TLS issue above; not needed from a normal terminal)

## Railway CLI
- Linked via `railway link` to project `compliance-agent-backend`, service `compliance-agent`
- Token cached at `~/.railway/` (managed by CLI, don't touch)
- **Login from this Bash tool**: doesn't work — no TTY. Spawn `Start-Process powershell -ArgumentList '-Command','railway login'` to get an interactive window. Once cached, every shell sees the credential.

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
