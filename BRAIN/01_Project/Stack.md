---
created: 2026-05-10
updated: 2026-05-10
tags: [stack]
---

# Tech stack

## Backend
| Lib | Purpose |
|---|---|
| FastAPI 0.115+ | HTTP API |
| SQLAlchemy 2 | ORM |
| Alembic | Migrations (39 versions on `backend/alembic/versions/`) |
| Pydantic 2 | Request/response schemas |
| httpx | Async HTTP (Deepgram, OpenRouter, Anthropic, Gemini, OpenAI) |
| tenacity | Retry policies (`backend/app/resilience.py`) |
| inngest | Durable workflows (optional via `USE_INNGEST_PIPELINE` env) |
| openai | Embeddings (Watt RAG layer) |

## Frontend
| Lib | Purpose |
|---|---|
| Next.js 16 (App Router) | React framework, NOT pages router |
| React 19 | — |
| TanStack Query 5 | Server state |
| shadcn/ui + Radix | UI primitives |
| Tailwind 3.4 | Styling |
| lucide-react | Icons |
| sonner | Toasts |
| @supabase/ssr | Supabase auth glue |

> **Note:** the project's `frontend-v3/AGENTS.md` warns this is "NOT the Next.js you know" — App Router, async server components by default, breaking from training data. Read `node_modules/next/dist/docs/` if doing routing changes.

## Speech / AI
| Service | Use |
|---|---|
| Deepgram Nova-3 | Primary STT (en-GB, EU region, diarise + sentiment + intents + topics + summary in one call) |
| AssemblyAI | Optional fallback |
| Groq Whisper | Optional fallback |
| Cohere | Optional fallback |
| Google Gemini | Optional fallback |
| OpenRouter → Anthropic Opus 4.7 | All LLM analysis (`anthropic/claude-opus-4.7`) |

## Infra
| Service | Use |
|---|---|
| Vercel | Frontend host (region `lhr1`) |
| Railway | Backend host (Dockerfile, eu-west region) |
| Supabase | Postgres + Storage + Auth (project `zcmdsblqbgatsrofptsq`, ap-south-1) |
| Inngest | Durable workflow engine (signing key set, event key set) |
| GitHub | Source: https://github.com/kingusa1/compliance-agent |

See [[01_Project/Deploy]] for deploy commands.
See [[06_Operations/Credentials]] for where each key lives.
