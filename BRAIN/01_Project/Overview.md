---
created: 2026-05-10
updated: 2026-05-10
tags: [project, overview]
---

# Compliance Agent — Overview

## What it is
An **internal compliance-audit tool** for **Watt Utilities** (UK Ofgem-regulated TPI / energy broker). NOT a SaaS. Single-tenant, single-customer. Watt's reviewers use it to audit recorded sales calls against supplier-mandated scripts and Ofgem's TPI Code.

## What it does
1. Reviewer uploads an MP3/WAV of a call.
2. Pipeline auto-detects: supplier, agent, customer, call_type, script.
3. Opus 4.7 scores every script checkpoint.
4. Calls below threshold land in [[02_Domain/Lifecycle|Review Queue]] for human sign-off.
5. Compliant calls land in `/compliant`. Non-compliant ones spawn a Rejection that flows Active → Fixed/Dead.

## Who uses it
- **Reviewers:** claim a queued call, accept/override the AI verdict (the human verdict is the audit-of-record).
- **Leads:** assign retraining, monitor agent pass rates.
- **Admins:** upload calls, manage scripts, track rejections to closure.

## Non-goals
- Real-time call recording / SIP integration. Calls are uploaded post-hoc.
- Multi-tenant. Only Watt.
- Building Ofgem reports — just feeds Watt's existing tracker.

## Where work happens
| Layer | Path | Lang |
|---|---|---|
| Backend | `backend/` | Python (FastAPI, SQLAlchemy 2, Alembic) |
| Frontend | `frontend-v3/` | TypeScript (Next.js App Router 16, shadcn/ui, Tailwind) |
| Domain | `backend/app/watt_compliance/` | Watt's 8 standards + 27 rejection codes |
| AI | `backend/app/analysis.py`, `backend/app/quality_agent.py`, `backend/app/checkpoint_analyzer.py` | Opus 4.7 via OpenRouter |
| Pipeline | `backend/app/pipeline.py` | Orchestration |

See [[01_Project/Architecture]] · [[01_Project/Stack]] · [[01_Project/Deploy]]
