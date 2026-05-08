# Compliance Agent — Backend

FastAPI + SQLAlchemy service. Transcribes call audio, runs the Watt-grounded
compliance analysis (regex pre-pass → Opus 4.7 → persistence), and serves the
reviewer dashboard's API.

## Layout

```
backend/
├── app/                          ← application code
│   ├── main.py                   ← FastAPI entry (lifespan, JWKS, Inngest)
│   ├── routes.py                 ← REST endpoints
│   ├── models.py                 ← SQLAlchemy models
│   ├── analysis.py               ← LLM analysis (analyze_compliance_watt)
│   ├── watt_compliance/          ← taxonomy, regex, prompts, persist (single source of truth)
│   ├── workflows/                ← Inngest durable steps (process_call)
│   ├── storage/                  ← Supabase / S3 storage adapters
│   ├── notifications/            ← feedback email + escalation cron
│   └── tracker_export.py         ← XLSX export matching tracker template
│
├── alembic/                      ← active Alembic migration chain (40 versions)
├── migrations_sql/               ← raw SQL loaded by specific Alembic revisions
├── scripts/                      ← curated ops scripts
│   ├── seed_compliance_data.py   ← RAG-ingest 15 supplier scripts
│   ├── extract_phase2_*.py       ← parse compliance-docs/ source material
│   ├── ab_parity.py, pg_dump_to_storage.py, …
│   └── legacy/                   ← ad-hoc benchmark / backfill scripts (not run in prod)
│
├── skills/                       ← supplier compliance reference markdown (read by app/)
├── tests/                        ← pytest suite (131 Watt-canonical tests + …)
│
├── Dockerfile                    ← Railway deploy
├── railway.toml                  ← Railway service config
├── requirements.txt              ← Python deps
├── alembic.ini, pytest.ini       ← tooling configs
└── .env.example                  ← env vars template
```

## Run

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env  # then fill in Supabase + OpenRouter + Deepgram + Inngest keys
./venv/bin/alembic upgrade head
./venv/bin/uvicorn app.main:app --port 8001 --reload
```

## Test

```bash
./venv/bin/pytest tests/ -q
```

The 131 Watt-canonical tests live under names matching `test_compliance_*.py`,
`test_analyze_compliance_watt.py`, `test_persist_watt_analysis.py`, and the
notification tests.

## Health checks

- `GET /healthz` — process up
- `GET /readyz`  — process up + DB reachable + JWKS warm
- `GET /metrics` — Prometheus scrape
- `GET /docs`    — Swagger UI
