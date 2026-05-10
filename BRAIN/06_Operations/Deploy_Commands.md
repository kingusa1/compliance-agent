---
created: 2026-05-10
updated: 2026-05-10
tags: [ops, cheatsheet]
---

# Deploy commands cheat sheet

## Health checks
```bash
curl -sk https://compliance-agent-production-690e.up.railway.app/api/health
curl -sk https://compliance-agent-production-690e.up.railway.app/healthz
curl -sk https://compliance-agent-production-690e.up.railway.app/readyz

curl -sk https://compliance-agent-mu.vercel.app/dashboard -o /dev/null -w "%{http_code}\n"
```

## Backend deploy
```bash
cd backend
railway up --service=compliance-agent --ci
```
- Watch build: `railway logs --build`
- Runtime logs: `railway logs`

## Frontend deploy (manual)
```bash
# IMPORTANT: from REPO ROOT, not from frontend-v3/
NODE_OPTIONS=--use-system-ca vercel deploy --prod --yes --force --scope mohamed-hishams-projects-0b4feda9
```

## Frontend deploy (API-triggered, most reliable)
```bash
TOKEN=$(cat "$APPDATA/com.vercel.cli/Data/auth.json" | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -X POST "https://api.vercel.com/v13/deployments?teamId=team_fNQJtpp1M2P2dkcoWvQIziCr&forceNew=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"compliance-agent","project":"prj_eHIyIFyxusNdCd6mR9Ff469NrcKO","gitSource":{"type":"github","ref":"main","repoId":1233382040},"target":"production"}'
```

## Re-alias after a deploy hijack
```bash
# Find the latest 1m+ build (long duration = real build)
NODE_OPTIONS=--use-system-ca vercel ls --scope mohamed-hishams-projects-0b4feda9 | head -10

# Then alias it
NODE_OPTIONS=--use-system-ca vercel alias set https://compliance-agent-<DEPLOY-ID>-mohamed-hishams-projects-0b4feda9.vercel.app compliance-agent-mu.vercel.app --scope mohamed-hishams-projects-0b4feda9
```

## Run the Quality Agent on demand
```bash
curl -X POST https://compliance-agent-production-690e.up.railway.app/api/admin/quality-resolve
```

## Trigger a re-process on an existing call
```bash
curl -X POST https://compliance-agent-production-690e.up.railway.app/api/calls/<CALL_ID>/retry
```

## Verify Vercel project root settings (the 404 root cause)
```bash
TOKEN=$(cat "$APPDATA/com.vercel.cli/Data/auth.json" | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -s "https://api.vercel.com/v9/projects/prj_eHIyIFyxusNdCd6mR9Ff469NrcKO?teamId=team_fNQJtpp1M2P2dkcoWvQIziCr" \
  -H "Authorization: Bearer $TOKEN" | python -c "import json,sys;d=json.load(sys.stdin);print('rootDirectory:', d.get('rootDirectory'));print('framework:', d.get('framework'))"
```
Expected: `rootDirectory: frontend-v3`, `framework: nextjs`. If not:
```bash
curl -X PATCH "https://api.vercel.com/v9/projects/prj_eHIyIFyxusNdCd6mR9Ff469NrcKO?teamId=team_fNQJtpp1M2P2dkcoWvQIziCr" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"rootDirectory":"frontend-v3","framework":"nextjs"}'
```

## MCP — manage installed servers
```bash
claude mcp list
claude mcp add playwright npx -- "@playwright/mcp@latest"
claude mcp remove <name>
```
After adding/removing MCP, **restart the Claude Code session** for the change to take effect.

## Git — recent log
```bash
git log --oneline -20
```

## Database — quick checks via API
```bash
curl -s https://compliance-agent-production-690e.up.railway.app/api/customers   # all customers
curl -s https://compliance-agent-production-690e.up.railway.app/api/deals        # all deals
curl -s https://compliance-agent-production-690e.up.railway.app/api/calls?limit=10
curl -s https://compliance-agent-production-690e.up.railway.app/api/scripts
curl -s https://compliance-agent-production-690e.up.railway.app/api/stats
```
