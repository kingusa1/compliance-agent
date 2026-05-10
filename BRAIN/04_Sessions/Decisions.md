---
created: 2026-05-10
updated: 2026-05-10
tags: [decisions, log]
---

# Decisions log — running

> Append at the top. Newest first.

## 2026-05-10

- **Vercel `rootDirectory` MUST be `frontend-v3`** — root cause of every recurring 404. Set via API `PATCH /v9/projects/{id}`. Don't unset.
- **Auto-deploys on `main` are allowed** (didn't disable). The rootDirectory fix is what makes them work. If they ever go bad again, suspect rootDirectory drift first.
- **Quality Agent runs auto** on every upload, not just admin trigger. Cost is bounded by the ≥2-sibling guard. Verdict is bounded by the 0.7-confidence + merge_all guard.
- **Per-call detection ≠ truth.** Treat single-call agent/customer/supplier as a HINT. The Quality Agent is the source of truth for cross-call identity.
- **Customer.slug** is auto-regenerated when stub-rename fires. No manual slugs.
- **Field-source provenance: `user` > `human-override` > `ai` > `inherited` > `auto`.** AI never clobbers human edits.
- **No SaaS multi-tenant work.** Watt is the only customer. Don't build org-switching.
- **`USE_INNGEST_PIPELINE = false`** for now. Asyncio path is the default. Inngest stays warm via env keys but isn't dispatching.
- **Manual `railway up` from `backend/`** is the trusted deploy path. Railway GitHub auto-deploy is "should work" (top-level Dockerfile shipped) but not the path I rely on.
- **Manual `vercel deploy --prod` runs from REPO ROOT** now (because rootDirectory=frontend-v3). Don't run from `frontend-v3/` anymore.

## Session-resume protocol
When user types "read brain" / "read obsidian":
1. [[00_INDEX]]
2. [[05_State/Live_State]]
3. Latest `04_Sessions/<date>_Session.md`
4. Whatever specific area is being asked about

When work happens that changes state:
1. Update [[05_State/Live_State]]
2. Append to today's session file
3. If a decision was made → append HERE (top of file)
