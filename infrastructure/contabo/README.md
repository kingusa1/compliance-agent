# infrastructure/contabo

Codified state for the production stack on Contabo VPS.

**Scope of this directory:**
- ✅ Cloudflare DNS records (Terraform / OpenTofu managed)
- ❌ Contabo VM lifecycle (NOT in IaC — provider too thin; managed via runbook below)

The VPS itself is managed by SSH + Docker Compose. We document that here so a
fresh engineer can rebuild the host from scratch without reading config files
on a live box.

---

## VPS runbook (Contabo)

Production host: Contabo VPS, IPv4 fixed (see Cloudflare A record).
Access: SSH key only; password auth disabled.

### From a fresh Contabo VPS
1. SSH in with the deploy key:
   ```bash
   ssh -i ~/.ssh/contabo_deploy root@<vps_ipv4>
   ```
2. Provision base OS packages:
   ```bash
   apt-get update && apt-get install -y docker.io docker-compose-plugin git ufw fail2ban
   ufw default deny incoming
   ufw allow 22/tcp
   ufw allow 80/tcp
   ufw allow 443/tcp
   ufw enable
   ```
3. Install Cloudflare Tunnel (cloudflared) and register the tunnel that fronts
   `compliance.<domain>`. (Existing tunnel — see Cloudflare Zero Trust dashboard.)
4. Clone repo and start services:
   ```bash
   git clone https://github.com/ArcadeTechLTD/compliance-agent.git /opt/compliance
   cd /opt/compliance
   cp .env.example .env   # populate with Supabase + provider keys
   docker compose pull && docker compose up -d
   ```
5. Verify:
   ```bash
   curl -fsS http://localhost:8001/healthz
   curl -fsS http://localhost:9000        # frontend
   ```

### Day-to-day
- Deploys: `docker compose pull && docker compose up -d` (Wave 5 wires this into GH Actions)
- Backups: nightly `pg_dump` cron (Wave 3)
- Logs: `docker compose logs -f compliance-backend`
- Rebuild: same as "From a fresh Contabo VPS" above; idempotent

### Backups (Wave 3)

Daily `pg_dump` runs via Inngest at 02:00 UTC.

Required env on the VPS (in `/opt/compliance/.env`):

```bash
BACKUP_BUCKET=backups
BACKUP_AGE_RECIPIENT=age1...        # public key of off-VPS identity
```

Manual backup: `docker compose exec compliance-backend python -m scripts.pg_dump_to_storage`.

Restore drill: `bash scripts/restore_drill.sh --latest` from any host with `BACKUP_AGE_IDENTITY` and `DATABASE_URL_SCRATCH` set.

---

## Cloudflare DNS (Terraform / OpenTofu)

The DNS A record `compliance.<domain>` is the single Terraform-managed
resource here. Cloudflare provider is mature, declarative, and worth IaC.

### First-time setup
1. `brew install opentofu` (macOS) or your distro's equivalent.
2. Export creds:
   ```bash
   export TF_VAR_cloudflare_api_token=...
   export TF_VAR_cloudflare_zone_id=...
   export TF_VAR_vps_ipv4=...    # the Contabo public IP
   export TF_VAR_subdomain=compliance
   ```
3. Discover the existing DNS record id:
   ```bash
   curl -s -H "Authorization: Bearer $TF_VAR_cloudflare_api_token" \
     "https://api.cloudflare.com/client/v4/zones/$TF_VAR_cloudflare_zone_id/dns_records?name=compliance.<domain>" \
     | jq '.result[].id'
   ```
4. Initialize and import:
   ```bash
   cd infrastructure/contabo
   tofu init
   tofu import cloudflare_record.compliance_apex <zone_id>/<record_id>
   ```
5. Verify zero diff before merging:
   ```bash
   tofu plan
   ```
   Must report `No changes. Your infrastructure matches the configuration.`

### Day-to-day
- `tofu plan` before any DNS edit.
- Never `tofu apply` against live without a reviewer present.
- Lock file (`.terraform.lock.hcl`) is gitignored because state is not yet
  on a remote backend; once moved to remote backend, commit the lock file.

---

## Branch protection on main (manual GH UI step)

After Wave 1 merges, in GitHub → Settings → Branches → `main`:
- Required status checks: `pytest`, `vitest`, `coverage`, `gate`
- Require linear history
- Restrict force-push and deletion

Subagents cannot drive the GitHub UI; this is a human step.

## Wave-5 prerequisite — rotate compromised PAT

A GitHub Personal Access Token was leaked into a `git remote -v` output
during brainstorming and is in the conversation transcript. Treat it as
compromised. **Before Wave 5** (`deploy.yml` adds repo SSH/PAT secrets):

1. Revoke at https://github.com/settings/tokens.
2. Issue a fresh token with minimum scopes (`repo` only if needed).
3. Replace the local remote:
   ```bash
   git remote set-url origin https://github.com/ArcadeTechLTD/compliance-agent.git
   gh auth login   # store new token in macOS keychain
   ```
4. Audit GitHub audit log for unauthorized activity while the leaked token was live.

This is a hard prereq, not optional.

---

### Auto-deploy via GitHub Actions (Wave 5)

`push` to `main` → `.github/workflows/deploy.yml` → SSH to this VPS → `infrastructure/contabo/deploy.sh` → `git reset --hard origin/main` → `docker compose up -d` → wait for `/healthz` → rollback on failure.

#### One-time setup

1. **Generate a deploy-only SSH key** (no passphrase, ed25519):

   ```bash
   ssh-keygen -t ed25519 -C "compliance-agent-deploy" -f ~/.ssh/compliance_deploy -N ""
   ```

2. **Append the public key** to `/root/.ssh/authorized_keys` on the VPS (or whichever user owns `/opt/compliance`):

   ```bash
   ssh root@<vps-ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" < ~/.ssh/compliance_deploy.pub
   ```

   Optional but recommended: restrict the key to running only `deploy.sh`:

   ```
   # in /root/.ssh/authorized_keys, prefix the key with:
   command="cd /opt/compliance && bash infrastructure/contabo/deploy.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...
   ```

3. **Set repo secrets** in GitHub → Settings → Secrets and variables → Actions:

   | Secret | Value |
   |---|---|
   | `CONTABO_DEPLOY_HOST` | `161.97.178.185` (or DNS name) |
   | `CONTABO_DEPLOY_USER` | `root` (or the deploy user) |
   | `CONTABO_DEPLOY_KEY` | contents of `~/.ssh/compliance_deploy` (PRIVATE key) |
   | `CONTABO_DEPLOY_PORT` | `22` (default; only set if non-standard) |
   | `CONTABO_HEALTHZ_URL` | `https://compliance.<domain>/healthz` (Cloudflare Tunnel hostname) |

4. **Verify the path works manually first** before relying on Actions:

   ```bash
   ssh -i ~/.ssh/compliance_deploy root@<vps-ip> "cd /opt/compliance && bash infrastructure/contabo/deploy.sh"
   ```
   Expected: prints `[deploy] OK — now at <sha>`.

#### Rotating the deploy key

Every quarter, regenerate the keypair:

```bash
ssh-keygen -t ed25519 -C "compliance-agent-deploy-$(date +%Y%m)" -f ~/.ssh/compliance_deploy -N ""
ssh root@<vps-ip> "sed -i '/compliance-agent-deploy/d' ~/.ssh/authorized_keys"
ssh root@<vps-ip> "cat >> ~/.ssh/authorized_keys" < ~/.ssh/compliance_deploy.pub
gh secret set CONTABO_DEPLOY_KEY < ~/.ssh/compliance_deploy
```

#### Manual emergency redeploy

If automated deploy fails or you need to roll forward without merging:

- GitHub UI → Actions → `deploy` → "Run workflow" (uses `workflow_dispatch`).
- OR via gh: `gh workflow run deploy.yml`.
- OR direct SSH: `ssh root@<vps-ip> "cd /opt/compliance && bash infrastructure/contabo/deploy.sh"`.
