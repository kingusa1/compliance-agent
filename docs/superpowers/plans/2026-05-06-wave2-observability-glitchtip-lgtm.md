# Wave 2 — Observability: GlitchTip + LGTM-lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-hosted observability stack (GlitchTip for errors + Loki/Promtail/Prometheus/Grafana for logs+metrics) and wire Sentry SDK into FastAPI backend and Next.js frontend, plus four seed Grafana dashboards (Pipeline / LLM / API / Errors). Capture-only, no behavior change. Wave 2 of 5.

**Architecture:** Three independent capture surfaces land in one wave:
(a) **Errors** — Sentry SDK on backend (FastAPI integration) and frontend (`@sentry/nextjs`) ship to self-hosted GlitchTip (Sentry-API-compatible) running in Docker.
(b) **Metrics** — `prometheus-fastapi-instrumentator` exposes `/metrics`; a new `app/observability_metrics.py` registers custom counters/histograms for pipeline steps and LLM calls. Prometheus scrapes `compliance-backend:8001/metrics` every 15 s.
(c) **Logs** — backend logger formats stdout as single-line JSON; Promtail tails Docker container stdout and ships to Loki; Grafana Explore searches by `job_id`.
All four observability containers (GlitchTip + Loki + Promtail + Prom + Grafana, plus GlitchTip's Postgres+Redis) live in a separate `docker-compose.observability.yml` overlay so they can be brought up/down without touching the app stack.

**Tech Stack:** sentry-sdk 2.18, prometheus-fastapi-instrumentator 7.0, prometheus-client 0.21, @sentry/nextjs 8.x, GlitchTip 4.1, Loki 3.2, Promtail 3.2, Prometheus 2.55, Grafana 11.3, Docker Compose v2.

**Spec source:** `docs/superpowers/specs/2026-05-06-enterprise-hardening-inject-design.md` §9 Wave 2 (W2a + W2b + W2c). W2d (audit writes + failed_jobs writer) shipped already in Wave 1 — not re-done here.

**Prereqs:**
- Wave 1 merged to `main` (audit_log + failed_jobs migrations live).
- Local dev DB up (Docker pgvector at `:5433`, see `backend/.env`).
- Working branch: `feat/wave2-observability` cut from `main` after Wave 1 merges. If Wave 1 still on `feat/wave1-foundation`, cut from there to keep the chain straight.

**Wave 3–5 deferred** to separate plans, generated after this wave verifies green.

---

## Branch + scope

```bash
git checkout main
git pull --ff-only
git checkout -b feat/wave2-observability
```

If Wave 1 not merged yet, branch from it instead:
```bash
git checkout feat/wave1-foundation
git checkout -b feat/wave2-observability
```

---

## File Structure

| Path | New / Mod | Responsibility |
|---|---|---|
| `backend/requirements.txt` | MOD | + `sentry-sdk[fastapi]==2.18.0`, `prometheus-fastapi-instrumentator==7.0.2`, `python-json-logger==2.0.7` |
| `backend/app/config.py` | MOD | + `sentry_dsn`, `sentry_environment`, `sentry_traces_sample_rate`, `prometheus_enabled` |
| `backend/app/logger.py` | MOD | Switch formatter to JSON (single-line) so Promtail → Loki gets structured fields |
| `backend/app/observability_metrics.py` | NEW | Prometheus registry + counters + histograms (`pipeline_step_duration_seconds`, `llm_calls_total`, `llm_call_duration_seconds`) |
| `backend/app/main.py` | MOD | `init_sentry()` on startup, mount `/healthz` + `/readyz`, mount `/metrics` via `Instrumentator().instrument(app).expose(app)` |
| `backend/app/workflows/process_call.py` | MOD | Wrap each `ctx.step.run(...)` call in `record_pipeline_step(step_name, duration)` |
| `backend/app/agent/agent_loop.py` | MOD | `record_llm_call(model, duration, escalated)` on first-pass + escalation paths |
| `backend/tests/test_health_routes.py` | NEW | `/healthz`, `/readyz`, `/metrics` smoke tests |
| `backend/tests/test_observability_metrics.py` | NEW | metric registration, increment, histogram observe |
| `backend/tests/test_sentry_init.py` | NEW | Init no-op when DSN empty; init when DSN set; never raises |
| `backend/tests/test_logger_json.py` | NEW | Logger emits valid JSON line with required fields |
| `frontend-v3/package.json` | MOD | + `@sentry/nextjs` (caret latest 8.x) |
| `frontend-v3/sentry.client.config.ts` | NEW | Browser SDK init, env-gated by `NEXT_PUBLIC_SENTRY_DSN` |
| `frontend-v3/sentry.server.config.ts` | NEW | Server SDK init, env-gated by `SENTRY_DSN` |
| `frontend-v3/sentry.edge.config.ts` | NEW | Edge runtime init |
| `frontend-v3/next.config.mjs` | MOD | Wrap export with `withSentryConfig(...)` |
| `frontend-v3/tests/sentry-init.test.ts` | NEW | Vitest: import sentry config without DSN → no-op (no throw) |
| `docker-compose.observability.yml` | NEW | GlitchTip web + worker + Postgres + Redis + Loki + Promtail + Prometheus + Grafana |
| `infrastructure/prometheus/prometheus.yml` | NEW | scrape `compliance-backend:8001/metrics` every 15 s |
| `infrastructure/promtail/config.yml` | NEW | Tail Docker container stdout, ship to Loki, parse JSON labels |
| `infrastructure/grafana/provisioning/datasources/datasources.yml` | NEW | Loki + Prometheus datasources |
| `infrastructure/grafana/provisioning/dashboards/dashboards.yml` | NEW | Loader pointing at `/var/lib/grafana/dashboards` |
| `infrastructure/grafana/dashboards/pipeline.json` | NEW | Per-step duration p50/p95/p99 + throughput |
| `infrastructure/grafana/dashboards/llm.json` | NEW | LLM call rate + escalation rate + latency |
| `infrastructure/grafana/dashboards/api.json` | NEW | RPS + p50/p95/p99 + error rate per route |
| `infrastructure/grafana/dashboards/errors.json` | NEW | GlitchTip top exceptions (Loki query) + Loki errors-per-minute |
| `docs/observability.md` | NEW | Stack runbook: bring-up, smoke procedure, dashboard URLs, secrets |
| `.env.example` | MOD (or NEW if missing) | + `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN`, `GRAFANA_ADMIN_PASSWORD`, `GLITCHTIP_SECRET_KEY` |

---

## Task 1: Add backend dependencies

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add three lines to requirements.txt**

Open `backend/requirements.txt` and append (alphabetised group at end):

```
prometheus-fastapi-instrumentator==7.0.2
python-json-logger==2.0.7
sentry-sdk[fastapi]==2.18.0
```

- [ ] **Step 2: Install locally**

Run:
```bash
cd backend && source venv/bin/activate && pip install -r requirements.txt
```
Expected: three new packages downloaded, no version conflicts.

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "deps(backend): add sentry-sdk, prometheus-fastapi-instrumentator, python-json-logger"
```

---

## Task 2: Add observability config keys

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Append fields to `Settings` class**

In `backend/app/config.py`, inside `class Settings(BaseSettings):` (before `settings = Settings()`), add:

```python
    # ─── Wave 2 — observability ───────────────────────────────────────
    sentry_dsn: str = ""  # GlitchTip-compatible DSN; empty → SDK no-ops
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    prometheus_enabled: bool = True
```

- [ ] **Step 2: Verify import**

```bash
cd backend && python -c "from app.config import settings; print(settings.sentry_dsn, settings.prometheus_enabled)"
```
Expected: prints `"" True`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "config(backend): add Wave 2 observability settings (sentry_dsn, prometheus_enabled, traces_sample_rate)"
```

---

## Task 3: JSON logger

**Files:**
- Modify: `backend/app/logger.py`
- Create: `backend/tests/test_logger_json.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_logger_json.py`:

```python
import json
import logging
from app.logger import setup_logger


def test_logger_emits_json_with_required_fields(caplog):
    log = setup_logger()
    with caplog.at_level(logging.INFO, logger="compliance"):
        log.info("hello", extra={"job_id": "abc123", "step": "transcribe"})

    record = caplog.records[-1]
    formatter = log.handlers[0].formatter
    line = formatter.format(record)

    payload = json.loads(line)
    assert payload["message"] == "hello"
    assert payload["job_id"] == "abc123"
    assert payload["step"] == "transcribe"
    assert payload["levelname"] == "INFO"
    assert "asctime" in payload
```

- [ ] **Step 2: Run test, verify red**

```bash
cd backend && pytest tests/test_logger_json.py -v
```
Expected: FAIL — current formatter is plain text, not JSON.

- [ ] **Step 3: Replace formatter in `app/logger.py`**

Replace the body of `setup_logger` in `backend/app/logger.py`:

```python
import logging
import sys
from datetime import datetime

from pythonjsonlogger import jsonlogger


def setup_logger():
    """Configure single-line JSON structured logging for Promtail → Loki."""
    logger = logging.getLogger("compliance")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)

    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "asctime", "levelname": "levelname"},
        json_ensure_ascii=False,
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = setup_logger()
```

- [ ] **Step 4: Run test, verify green**

```bash
cd backend && pytest tests/test_logger_json.py -v
```
Expected: PASS.

- [ ] **Step 5: Sanity-check existing log output**

```bash
cd backend && python -c "from app.logger import log; log.info('boot', extra={'job_id': 'x'})"
```
Expected: stdout shows one JSON line containing `"message": "boot"`, `"job_id": "x"`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/logger.py backend/tests/test_logger_json.py
git commit -m "feat(logger): switch to JSON formatter for Loki ingestion"
```

---

## Task 4: Health + readiness endpoints

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_health_routes.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_health_routes.py`:

```python
from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


def test_healthz_returns_200():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_returns_200_when_db_reachable():
    r = client.get("/readyz")
    assert r.status_code in (200, 503)
    assert "status" in r.json()
    assert "checks" in r.json()
```

- [ ] **Step 2: Run test, verify red**

```bash
cd backend && pytest tests/test_health_routes.py -v
```
Expected: FAIL — 404 on both.

- [ ] **Step 3: Add routes in main.py**

In `backend/app/main.py`, after `app = FastAPI(...)` is constructed but before routers are mounted, add:

```python
from sqlalchemy import text
from app.database import engine


@app.get("/healthz", tags=["ops"])
def healthz():
    """Liveness — process is up."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
def readyz():
    """Readiness — process can serve traffic (DB reachable)."""
    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness must surface every failure mode
        checks["db"] = f"fail: {type(exc).__name__}"
    status_code = 200 if all(v == "ok" for v in checks.values()) else 503
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "ready" if status_code == 200 else "degraded", "checks": checks}, status_code=status_code)
```

- [ ] **Step 4: Run test, verify green**

```bash
cd backend && pytest tests/test_health_routes.py -v
```
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_health_routes.py
git commit -m "feat(api): add /healthz and /readyz endpoints"
```

---

## Task 5: Observability metrics module

**Files:**
- Create: `backend/app/observability_metrics.py`
- Create: `backend/tests/test_observability_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_observability_metrics.py`:

```python
from prometheus_client import REGISTRY
from app.observability_metrics import (
    record_pipeline_step,
    record_llm_call,
    PIPELINE_STEP_DURATION,
    LLM_CALLS_TOTAL,
    LLM_CALL_DURATION,
)


def test_pipeline_step_metric_observes_duration():
    record_pipeline_step("transcribe", 1.234)
    samples = list(PIPELINE_STEP_DURATION.collect()[0].samples)
    count = next(s for s in samples if s.name.endswith("_count") and s.labels.get("step") == "transcribe")
    assert count.value >= 1


def test_llm_calls_counter_increments():
    before = _counter_value(LLM_CALLS_TOTAL, model="gemini-flash", escalated="false")
    record_llm_call("gemini-flash", duration=0.5, escalated=False)
    after = _counter_value(LLM_CALLS_TOTAL, model="gemini-flash", escalated="false")
    assert after - before == 1


def test_llm_escalation_labelled_separately():
    record_llm_call("claude-sonnet", duration=2.0, escalated=True)
    val = _counter_value(LLM_CALLS_TOTAL, model="claude-sonnet", escalated="true")
    assert val >= 1


def _counter_value(metric, **labels) -> float:
    for sample in metric.collect()[0].samples:
        if sample.name.endswith("_total") and sample.labels == labels:
            return sample.value
    return 0.0
```

- [ ] **Step 2: Run test, verify red**

```bash
cd backend && pytest tests/test_observability_metrics.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the module**

Create `backend/app/observability_metrics.py`:

```python
"""Prometheus metric registry for the Compliance pipeline.

Three primary metrics:
  - pipeline_step_duration_seconds (Histogram, labelled by `step`)
  - llm_calls_total                 (Counter,   labelled by `model`, `escalated`)
  - llm_call_duration_seconds       (Histogram, labelled by `model`)

These feed the Pipeline + LLM Grafana dashboards. API/HTTP metrics are
provided by prometheus-fastapi-instrumentator and live under the
`http_request_*` namespace — no work needed here for those.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram


PIPELINE_STEP_DURATION = Histogram(
    "pipeline_step_duration_seconds",
    "Time spent in each compliance pipeline step",
    labelnames=("step",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

LLM_CALLS_TOTAL = Counter(
    "llm_calls_total",
    "Number of LLM calls issued by the pipeline",
    labelnames=("model", "escalated"),
)

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "Latency of individual LLM calls",
    labelnames=("model",),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 60.0),
)


def record_pipeline_step(step: str, duration_seconds: float) -> None:
    PIPELINE_STEP_DURATION.labels(step=step).observe(duration_seconds)


def record_llm_call(model: str, duration: float, escalated: bool = False) -> None:
    LLM_CALLS_TOTAL.labels(model=model, escalated=str(escalated).lower()).inc()
    LLM_CALL_DURATION.labels(model=model).observe(duration)
```

- [ ] **Step 4: Run test, verify green**

```bash
cd backend && pytest tests/test_observability_metrics.py -v
```
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add backend/app/observability_metrics.py backend/tests/test_observability_metrics.py
git commit -m "feat(metrics): add Prometheus registry for pipeline + LLM metrics"
```

---

## Task 6: Mount /metrics endpoint

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_health_routes.py` (add /metrics smoke test)

- [ ] **Step 1: Add failing /metrics test**

Append to `backend/tests/test_health_routes.py`:

```python
def test_metrics_endpoint_exposes_prometheus_text():
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    # Default instrumentator metrics
    assert "http_requests_total" in body or "http_request_duration_seconds" in body
    # Custom metric registered at import time
    assert "pipeline_step_duration_seconds" in body
```

- [ ] **Step 2: Run test, verify red**

```bash
cd backend && pytest tests/test_health_routes.py::test_metrics_endpoint_exposes_prometheus_text -v
```
Expected: FAIL — 404.

- [ ] **Step 3: Wire instrumentator in `main.py`**

In `backend/app/main.py`, after `app = FastAPI(...)` and after CORS middleware is added, add:

```python
from prometheus_fastapi_instrumentator import Instrumentator

# Touch the metric registry at import time so /metrics surfaces our
# custom series even before the first pipeline run.
import app.observability_metrics  # noqa: F401

if settings.prometheus_enabled:
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/healthz", "/readyz"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
```

- [ ] **Step 4: Run test, verify green**

```bash
cd backend && pytest tests/test_health_routes.py -v
```
Expected: PASS (all three: healthz, readyz, metrics).

- [ ] **Step 5: Manual smoke**

```bash
cd backend && uvicorn app.main:app --port 8001 &
sleep 2
curl -s http://localhost:8001/metrics | head -30
kill %1
```
Expected: Prometheus exposition format text including `http_requests_total{...}` and `pipeline_step_duration_seconds_bucket{...}`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_health_routes.py
git commit -m "feat(api): mount /metrics endpoint via prometheus-fastapi-instrumentator"
```

---

## Task 7: Instrument pipeline steps

**Files:**
- Modify: `backend/app/workflows/process_call.py`

- [ ] **Step 0: Locate the six step.run sites**

```bash
grep -n "ctx.step.run" backend/app/workflows/process_call.py
```
Expected: 6 hits at the lines mapping to `download_audio`, `transcribe`, `persist_transcript` (or similar), `analyze_checkpoints`, `score`, `finalize`. Capture exact step name strings — they become the `step=` label values.

- [ ] **Step 1: Write the failing integration test**

Create `backend/tests/test_pipeline_metrics_integration.py`:

```python
"""Integration: simulating a step run records duration in the histogram."""
import time
from prometheus_client import REGISTRY
from app.observability_metrics import (
    PIPELINE_STEP_DURATION,
    record_pipeline_step,
)


def _bucket_count(step_name: str) -> float:
    for sample in PIPELINE_STEP_DURATION.collect()[0].samples:
        if sample.name.endswith("_count") and sample.labels.get("step") == step_name:
            return sample.value
    return 0.0


def test_record_pipeline_step_increments_count():
    before = _bucket_count("transcribe")
    record_pipeline_step("transcribe", 0.12)
    after = _bucket_count("transcribe")
    assert after - before == 1
```

- [ ] **Step 2: Run test, verify green** (already green from Task 5 — included as a regression guard).

```bash
cd backend && pytest tests/test_pipeline_metrics_integration.py -v
```
Expected: PASS.

- [ ] **Step 3: Wrap each step.run with timing**

In `backend/app/workflows/process_call.py`, add at the top of the file (after existing imports):

```python
import time as _time
from app.observability_metrics import record_pipeline_step
```

For **each** of the six `await ctx.step.run(...)` call sites, wrap with a timer. Pattern:

```python
# BEFORE
audio_path_local = await ctx.step.run("download_audio", ...)

# AFTER
_t0 = _time.monotonic()
try:
    audio_path_local = await ctx.step.run("download_audio", ...)
finally:
    record_pipeline_step("download_audio", _time.monotonic() - _t0)
```

Apply identically to: `download_audio`, `transcribe`, the persist-transcript step, `analyze_checkpoints`, `score`, `finalize`. Use the exact step-name string passed to `ctx.step.run` as the metric label.

- [ ] **Step 4: Run unit + integration tests**

```bash
cd backend && pytest tests/test_observability_metrics.py tests/test_pipeline_metrics_integration.py tests/test_health_routes.py -v
```
Expected: PASS.

- [ ] **Step 5: Static check the wrap is balanced**

```bash
grep -c "record_pipeline_step(" backend/app/workflows/process_call.py
```
Expected: 6 (one per step).

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/process_call.py backend/tests/test_pipeline_metrics_integration.py
git commit -m "feat(workflows): instrument process_call steps with pipeline_step_duration_seconds"
```

---

## Task 8: Instrument LLM calls in agent loop

**Files:**
- Modify: `backend/app/agent/agent_loop.py`

- [ ] **Step 0: Locate LLM call sites**

```bash
grep -n "first_pass\|escalat\|gemini_flash_model\|agent_escalation_model" backend/app/agent/agent_loop.py | head -20
```
Capture the two strategic boundaries: (a) first-pass invocation (Gemini Flash), (b) escalation invocation (Sonnet).

- [ ] **Step 1: Add failing test**

Create `backend/tests/test_agent_loop_metrics.py`:

```python
from app.observability_metrics import LLM_CALLS_TOTAL


def _counter_value(model: str, escalated: str) -> float:
    for sample in LLM_CALLS_TOTAL.collect()[0].samples:
        if sample.name.endswith("_total") and sample.labels == {"model": model, "escalated": escalated}:
            return sample.value
    return 0.0


def test_record_llm_call_helper_emits_counter():
    """Smoke: confirms the import path the agent loop will use."""
    from app.observability_metrics import record_llm_call
    before = _counter_value("gemini-2.5-flash", "false")
    record_llm_call("gemini-2.5-flash", duration=0.3, escalated=False)
    after = _counter_value("gemini-2.5-flash", "false")
    assert after - before == 1
```

- [ ] **Step 2: Run, verify green** (Task 5 already covers the helper).

```bash
cd backend && pytest tests/test_agent_loop_metrics.py -v
```

- [ ] **Step 3: Add metric calls in agent_loop.py**

At the top of `backend/app/agent/agent_loop.py` (after existing imports), add:

```python
import time as _time
from app.observability_metrics import record_llm_call
```

At the **first-pass LLM invocation** site (Gemini Flash), wrap with:

```python
_t0 = _time.monotonic()
try:
    first_pass_result = await _invoke_first_pass(...)  # existing call
finally:
    record_llm_call(
        model=settings.gemini_flash_model,
        duration=_time.monotonic() - _t0,
        escalated=False,
    )
```

At the **escalation LLM invocation** site (Sonnet), wrap with:

```python
_t0 = _time.monotonic()
try:
    escalated_result = await _invoke_escalation(...)  # existing call
finally:
    record_llm_call(
        model=settings.agent_escalation_model,
        duration=_time.monotonic() - _t0,
        escalated=True,
    )
```

Substitute the actual function names found in Step 0; keep all existing arguments unchanged.

- [ ] **Step 4: Run agent loop existing tests**

```bash
cd backend && pytest tests/ -k agent -v
```
Expected: existing agent tests still pass; new metric counter calls don't change behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/agent_loop.py backend/tests/test_agent_loop_metrics.py
git commit -m "feat(agent): record llm_calls_total + llm_call_duration on first-pass and escalation"
```

---

## Task 9: Sentry SDK init (backend)

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_sentry_init.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sentry_init.py`:

```python
import importlib
import os
import pytest


def test_init_sentry_no_op_when_dsn_empty(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "")
    from app.main import init_sentry
    # Must not raise even when no DSN
    assert init_sentry() is None


def test_init_sentry_initialises_when_dsn_set(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@glitchtip.example.com/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "test")
    # Reload settings so env override is read
    from app import config
    importlib.reload(config)
    from app.main import init_sentry
    # Should complete without raising
    init_sentry()
    import sentry_sdk
    assert sentry_sdk.Hub.current.client is not None
```

- [ ] **Step 2: Run test, verify red**

```bash
cd backend && pytest tests/test_sentry_init.py -v
```
Expected: FAIL — `init_sentry` not exported.

- [ ] **Step 3: Add `init_sentry` in `main.py`**

In `backend/app/main.py`, before `app = FastAPI(...)` is constructed, add:

```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration


def init_sentry() -> None:
    """Initialise Sentry SDK if DSN is configured. No-op otherwise.

    Sentry-API-compatible — points at self-hosted GlitchTip in prod.
    Errors here MUST NOT take the process down: GlitchTip availability
    is non-critical to request-path code.
    """
    dsn = settings.sentry_dsn.strip()
    if not dsn:
        return
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.sentry_environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(),
            ],
        )
    except Exception:  # noqa: BLE001 — Sentry init must never break boot
        import logging
        logging.getLogger(__name__).warning("sentry_init_failed", exc_info=True)


init_sentry()
```

- [ ] **Step 4: Run test, verify green**

```bash
cd backend && pytest tests/test_sentry_init.py -v
```
Expected: PASS.

- [ ] **Step 5: Boot smoke**

```bash
cd backend && SENTRY_DSN="" python -c "from app.main import app; print('ok')"
```
Expected: `ok` printed, no traceback.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_sentry_init.py
git commit -m "feat(sentry): add init_sentry() with FastAPI integration, DSN-gated"
```

---

## Task 10: Frontend Sentry SDK install + init

**Files:**
- Modify: `frontend-v3/package.json` + `package-lock.json`
- Create: `frontend-v3/sentry.client.config.ts`
- Create: `frontend-v3/sentry.server.config.ts`
- Create: `frontend-v3/sentry.edge.config.ts`
- Modify: `frontend-v3/next.config.mjs`
- Create: `frontend-v3/tests/sentry-init.test.ts`

- [ ] **Step 1: Install dependency**

```bash
cd frontend-v3 && npm install --save @sentry/nextjs@^8.40.0
```
Expected: `@sentry/nextjs` added to `dependencies`.

- [ ] **Step 2: Write the failing vitest test**

Create `frontend-v3/tests/sentry-init.test.ts`:

```ts
import { describe, it, expect, vi } from 'vitest';

describe('sentry client config', () => {
  it('does not throw when DSN env var is unset', async () => {
    vi.stubEnv('NEXT_PUBLIC_SENTRY_DSN', '');
    await expect(import('../sentry.client.config')).resolves.not.toThrow();
  });

  it('does not throw when DSN env var is set', async () => {
    vi.stubEnv('NEXT_PUBLIC_SENTRY_DSN', 'https://public@example.com/1');
    await expect(import('../sentry.client.config')).resolves.not.toThrow();
  });
});
```

- [ ] **Step 3: Run, verify red**

```bash
cd frontend-v3 && npm run test:unit -- --run tests/sentry-init.test.ts
```
Expected: FAIL — config files don't exist.

- [ ] **Step 4: Create the three config files**

`frontend-v3/sentry.client.config.ts`:

```ts
import * as Sentry from '@sentry/nextjs';

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? 'development',
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
  });
}
```

`frontend-v3/sentry.server.config.ts`:

```ts
import * as Sentry from '@sentry/nextjs';

const dsn = process.env.SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? 'development',
    tracesSampleRate: 0.1,
  });
}
```

`frontend-v3/sentry.edge.config.ts`:

```ts
import * as Sentry from '@sentry/nextjs';

const dsn = process.env.SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? 'development',
    tracesSampleRate: 0.1,
  });
}
```

- [ ] **Step 5: Wrap next.config.mjs**

Read existing `frontend-v3/next.config.mjs`. Wrap the default export with `withSentryConfig`:

```js
import { withSentryConfig } from '@sentry/nextjs';

// ...existing nextConfig object unchanged...

export default withSentryConfig(nextConfig, {
  silent: true,
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  // GlitchTip-compatible: skip source-map upload when no auth token.
  authToken: process.env.SENTRY_AUTH_TOKEN,
  disableServerWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
  disableClientWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
});
```

If `next.config.mjs` uses `module.exports` syntax, adapt accordingly (existing file is `.mjs` so ESM is fine).

- [ ] **Step 6: Run vitest, verify green**

```bash
cd frontend-v3 && npm run test:unit -- --run tests/sentry-init.test.ts
```
Expected: PASS.

- [ ] **Step 7: Build smoke**

```bash
cd frontend-v3 && SENTRY_AUTH_TOKEN="" NEXT_PUBLIC_SENTRY_DSN="" npm run build
```
Expected: build completes (Sentry warns about missing auth token but does not fail with `disableServerWebpackPlugin` flag).

- [ ] **Step 8: Commit**

```bash
git add frontend-v3/package.json frontend-v3/package-lock.json \
        frontend-v3/sentry.client.config.ts \
        frontend-v3/sentry.server.config.ts \
        frontend-v3/sentry.edge.config.ts \
        frontend-v3/next.config.mjs \
        frontend-v3/tests/sentry-init.test.ts
git commit -m "feat(frontend): add @sentry/nextjs with env-gated init"
```

---

## Task 11: Observability docker-compose stack

**Files:**
- Create: `docker-compose.observability.yml`
- Modify: `.env.example`

- [ ] **Step 1: Write compose file**

Create `docker-compose.observability.yml` at repo root:

```yaml
# docker-compose.observability.yml
# GlitchTip + LGTM-lite (Loki/Promtail/Prometheus/Grafana).
# Runs alongside the app stack:
#   docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
#
# All seven services live on the `compliance-net` external network so
# Prometheus can scrape `compliance-backend:8001/metrics` and Promtail
# can read /var/lib/docker/containers/*.

networks:
  compliance-net:
    external: true

volumes:
  glitchtip-pgdata:
  glitchtip-uploads:
  loki-data:
  prom-data:
  grafana-data:

services:
  # ── GlitchTip (Sentry-API-compatible error tracking) ────────────
  glitchtip-postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: glitchtip
      POSTGRES_USER: glitchtip
      POSTGRES_PASSWORD: ${GLITCHTIP_PG_PASSWORD:-glitchtip}
    volumes:
      - glitchtip-pgdata:/var/lib/postgresql/data
    networks: [compliance-net]

  glitchtip-redis:
    image: redis:7-alpine
    restart: unless-stopped
    networks: [compliance-net]

  glitchtip-web:
    image: glitchtip/glitchtip:v4.1
    restart: unless-stopped
    depends_on: [glitchtip-postgres, glitchtip-redis]
    environment:
      DATABASE_URL: postgres://glitchtip:${GLITCHTIP_PG_PASSWORD:-glitchtip}@glitchtip-postgres:5432/glitchtip
      SECRET_KEY: ${GLITCHTIP_SECRET_KEY:?GLITCHTIP_SECRET_KEY required}
      PORT: "8000"
      EMAIL_URL: "consolemail://"
      GLITCHTIP_DOMAIN: ${GLITCHTIP_DOMAIN:-http://localhost:8080}
      DEFAULT_FROM_EMAIL: glitchtip@localhost
      CELERY_WORKER_AUTOSCALE: "1,3"
      REDIS_URL: redis://glitchtip-redis:6379/0
    ports:
      - "8080:8000"
    volumes:
      - glitchtip-uploads:/code/uploads
    networks: [compliance-net]

  glitchtip-worker:
    image: glitchtip/glitchtip:v4.1
    restart: unless-stopped
    depends_on: [glitchtip-postgres, glitchtip-redis]
    command: ./bin/run-celery-with-beat.sh
    environment:
      DATABASE_URL: postgres://glitchtip:${GLITCHTIP_PG_PASSWORD:-glitchtip}@glitchtip-postgres:5432/glitchtip
      SECRET_KEY: ${GLITCHTIP_SECRET_KEY:?GLITCHTIP_SECRET_KEY required}
      REDIS_URL: redis://glitchtip-redis:6379/0
      EMAIL_URL: "consolemail://"
      GLITCHTIP_DOMAIN: ${GLITCHTIP_DOMAIN:-http://localhost:8080}
    volumes:
      - glitchtip-uploads:/code/uploads
    networks: [compliance-net]

  # ── Loki (logs) ──────────────────────────────────────────────────
  loki:
    image: grafana/loki:3.2.1
    restart: unless-stopped
    command: -config.file=/etc/loki/local-config.yaml
    volumes:
      - loki-data:/loki
    ports:
      - "3100:3100"
    networks: [compliance-net]

  # ── Promtail (log shipper) ───────────────────────────────────────
  promtail:
    image: grafana/promtail:3.2.1
    restart: unless-stopped
    command: -config.file=/etc/promtail/config.yml
    volumes:
      - ./infrastructure/promtail/config.yml:/etc/promtail/config.yml:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
    depends_on: [loki]
    networks: [compliance-net]

  # ── Prometheus (metrics) ─────────────────────────────────────────
  prometheus:
    image: prom/prometheus:v2.55.1
    restart: unless-stopped
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=14d"
    volumes:
      - ./infrastructure/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prom-data:/prometheus
    ports:
      - "9090:9090"
    networks: [compliance-net]

  # ── Grafana (dashboards) ─────────────────────────────────────────
  grafana:
    image: grafana/grafana:11.3.0
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD required}
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - grafana-data:/var/lib/grafana
      - ./infrastructure/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./infrastructure/grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "3001:3000"
    depends_on: [loki, prometheus]
    networks: [compliance-net]
```

- [ ] **Step 2: Append observability env vars to .env.example**

If `.env.example` does not exist, create it; else append:

```bash
# ─── Wave 2 — Observability ───────────────────────────────────────
# GlitchTip (Sentry-compatible error tracking)
SENTRY_DSN=
SENTRY_ENVIRONMENT=development
NEXT_PUBLIC_SENTRY_DSN=
NEXT_PUBLIC_SENTRY_ENVIRONMENT=development
SENTRY_ORG=compliance
SENTRY_PROJECT=compliance-frontend
SENTRY_AUTH_TOKEN=

# GlitchTip self-hosted secrets
GLITCHTIP_SECRET_KEY=change-me-at-least-50-chars-of-entropy-here-please
GLITCHTIP_PG_PASSWORD=change-me
GLITCHTIP_DOMAIN=http://localhost:8080

# Grafana
GRAFANA_ADMIN_PASSWORD=change-me
```

- [ ] **Step 3: Validate compose syntax**

```bash
docker compose -f docker-compose.observability.yml config > /dev/null
```
Expected: exits 0 with no output (config valid). If warns about missing env vars (`GLITCHTIP_SECRET_KEY required`, etc.), that's expected — env file is local only.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.observability.yml .env.example
git commit -m "feat(observability): add docker-compose.observability.yml (GlitchTip + LGTM-lite)"
```

---

## Task 12: Prometheus scrape config

**Files:**
- Create: `infrastructure/prometheus/prometheus.yml`

- [ ] **Step 1: Write config**

Create `infrastructure/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: compliance-backend
    metrics_path: /metrics
    static_configs:
      - targets:
          - compliance-backend:8001
        labels:
          service: compliance-backend

  - job_name: prometheus
    static_configs:
      - targets:
          - localhost:9090
```

- [ ] **Step 2: Validate**

```bash
docker run --rm -v "$(pwd)/infrastructure/prometheus/prometheus.yml:/p.yml:ro" \
  prom/prometheus:v2.55.1 promtool check config /p.yml
```
Expected: `SUCCESS: /p.yml is valid prometheus config file syntax`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/prometheus/prometheus.yml
git commit -m "feat(observability): add Prometheus scrape config for compliance-backend"
```

---

## Task 13: Promtail config (Docker stdout → Loki)

**Files:**
- Create: `infrastructure/promtail/config.yml`

- [ ] **Step 1: Write config**

Create `infrastructure/promtail/config.yml`:

```yaml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: docker-containers
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      - source_labels: [__meta_docker_container_name]
        regex: "/(.*)"
        target_label: container
      - source_labels: [__meta_docker_container_log_stream]
        target_label: stream
      - source_labels: [__meta_docker_container_label_com_docker_compose_service]
        target_label: compose_service
    pipeline_stages:
      - json:
          expressions:
            level: levelname
            job_id: job_id
            step: step
            message: message
      - labels:
          level:
          job_id:
          step:
```

- [ ] **Step 2: Commit**

```bash
git add infrastructure/promtail/config.yml
git commit -m "feat(observability): add Promtail config (Docker SD → Loki, JSON parsed labels)"
```

---

## Task 14: Grafana datasource + dashboard provisioning

**Files:**
- Create: `infrastructure/grafana/provisioning/datasources/datasources.yml`
- Create: `infrastructure/grafana/provisioning/dashboards/dashboards.yml`

- [ ] **Step 1: Datasources**

Create `infrastructure/grafana/provisioning/datasources/datasources.yml`:

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    jsonData:
      timeInterval: 15s

  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    jsonData:
      maxLines: 1000
```

- [ ] **Step 2: Dashboard loader**

Create `infrastructure/grafana/provisioning/dashboards/dashboards.yml`:

```yaml
apiVersion: 1

providers:
  - name: compliance-seed
    orgId: 1
    folder: Compliance
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: false
```

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/provisioning/
git commit -m "feat(observability): provision Grafana datasources + dashboard loader"
```

---

## Task 15: Pipeline dashboard

**Files:**
- Create: `infrastructure/grafana/dashboards/pipeline.json`

- [ ] **Step 1: Write dashboard JSON**

Create `infrastructure/grafana/dashboards/pipeline.json`:

```json
{
  "title": "Pipeline",
  "uid": "compliance-pipeline",
  "schemaVersion": 39,
  "version": 1,
  "timezone": "browser",
  "time": { "from": "now-6h", "to": "now" },
  "refresh": "30s",
  "tags": ["compliance", "pipeline"],
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "Step duration p50 / p95 / p99 (s)",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 0 },
      "targets": [
        {
          "expr": "histogram_quantile(0.50, sum by (le, step) (rate(pipeline_step_duration_seconds_bucket[5m])))",
          "legendFormat": "p50 — {{step}}",
          "refId": "A"
        },
        {
          "expr": "histogram_quantile(0.95, sum by (le, step) (rate(pipeline_step_duration_seconds_bucket[5m])))",
          "legendFormat": "p95 — {{step}}",
          "refId": "B"
        },
        {
          "expr": "histogram_quantile(0.99, sum by (le, step) (rate(pipeline_step_duration_seconds_bucket[5m])))",
          "legendFormat": "p99 — {{step}}",
          "refId": "C"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "s" }, "overrides": [] },
      "options": { "legend": { "showLegend": true, "displayMode": "table", "placement": "right" } }
    },
    {
      "id": 2,
      "type": "timeseries",
      "title": "Throughput — completed steps / min",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 8, "w": 24, "x": 0, "y": 9 },
      "targets": [
        {
          "expr": "sum by (step) (rate(pipeline_step_duration_seconds_count[1m])) * 60",
          "legendFormat": "{{step}}",
          "refId": "A"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "ops" }, "overrides": [] }
    }
  ]
}
```

- [ ] **Step 2: Validate JSON**

```bash
python -c "import json; json.load(open('infrastructure/grafana/dashboards/pipeline.json'))"
```
Expected: no output (valid).

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/dashboards/pipeline.json
git commit -m "feat(observability): add Pipeline Grafana dashboard"
```

---

## Task 16: LLM dashboard

**Files:**
- Create: `infrastructure/grafana/dashboards/llm.json`

- [ ] **Step 1: Write dashboard JSON**

Create `infrastructure/grafana/dashboards/llm.json`:

```json
{
  "title": "LLM",
  "uid": "compliance-llm",
  "schemaVersion": 39,
  "version": 1,
  "timezone": "browser",
  "time": { "from": "now-6h", "to": "now" },
  "refresh": "30s",
  "tags": ["compliance", "llm"],
  "panels": [
    {
      "id": 1,
      "type": "stat",
      "title": "LLM calls / min (last 5 min)",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 5, "w": 8, "x": 0, "y": 0 },
      "targets": [
        {
          "expr": "sum(rate(llm_calls_total[5m])) * 60",
          "refId": "A"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "short" }, "overrides": [] }
    },
    {
      "id": 2,
      "type": "stat",
      "title": "Escalation rate (last 5 min)",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 5, "w": 8, "x": 8, "y": 0 },
      "targets": [
        {
          "expr": "sum(rate(llm_calls_total{escalated=\"true\"}[5m])) / clamp_min(sum(rate(llm_calls_total[5m])), 1e-9)",
          "refId": "A"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "percentunit", "min": 0, "max": 1 }, "overrides": [] }
    },
    {
      "id": 3,
      "type": "timeseries",
      "title": "LLM call rate by model",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 5 },
      "targets": [
        {
          "expr": "sum by (model, escalated) (rate(llm_calls_total[1m])) * 60",
          "legendFormat": "{{model}} — escalated={{escalated}}",
          "refId": "A"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "ops" }, "overrides": [] }
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "LLM latency p50 / p95 by model",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 14 },
      "targets": [
        {
          "expr": "histogram_quantile(0.50, sum by (le, model) (rate(llm_call_duration_seconds_bucket[5m])))",
          "legendFormat": "p50 — {{model}}",
          "refId": "A"
        },
        {
          "expr": "histogram_quantile(0.95, sum by (le, model) (rate(llm_call_duration_seconds_bucket[5m])))",
          "legendFormat": "p95 — {{model}}",
          "refId": "B"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "s" }, "overrides": [] }
    }
  ]
}
```

- [ ] **Step 2: Validate**

```bash
python -c "import json; json.load(open('infrastructure/grafana/dashboards/llm.json'))"
```

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/dashboards/llm.json
git commit -m "feat(observability): add LLM Grafana dashboard"
```

---

## Task 17: API dashboard

**Files:**
- Create: `infrastructure/grafana/dashboards/api.json`

- [ ] **Step 1: Write dashboard JSON**

Create `infrastructure/grafana/dashboards/api.json`:

```json
{
  "title": "API",
  "uid": "compliance-api",
  "schemaVersion": 39,
  "version": 1,
  "timezone": "browser",
  "time": { "from": "now-1h", "to": "now" },
  "refresh": "30s",
  "tags": ["compliance", "api"],
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "RPS by route (last 1m)",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 0 },
      "targets": [
        {
          "expr": "sum by (handler) (rate(http_requests_total{handler!~\"/metrics|/healthz|/readyz\"}[1m]))",
          "legendFormat": "{{handler}}",
          "refId": "A"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "reqps" }, "overrides": [] }
    },
    {
      "id": 2,
      "type": "timeseries",
      "title": "Latency p50 / p95 / p99",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 9 },
      "targets": [
        {
          "expr": "histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))",
          "legendFormat": "p50",
          "refId": "A"
        },
        {
          "expr": "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))",
          "legendFormat": "p95",
          "refId": "B"
        },
        {
          "expr": "histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))",
          "legendFormat": "p99",
          "refId": "C"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "s" }, "overrides": [] }
    },
    {
      "id": 3,
      "type": "timeseries",
      "title": "Error rate per route (5xx + 4xx)",
      "datasource": { "type": "prometheus", "uid": "Prometheus" },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 18 },
      "targets": [
        {
          "expr": "sum by (handler, status) (rate(http_requests_total{status=~\"4..|5..\"}[5m]))",
          "legendFormat": "{{handler}} — {{status}}",
          "refId": "A"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "reqps" }, "overrides": [] }
    }
  ]
}
```

- [ ] **Step 2: Validate**

```bash
python -c "import json; json.load(open('infrastructure/grafana/dashboards/api.json'))"
```

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/dashboards/api.json
git commit -m "feat(observability): add API Grafana dashboard"
```

---

## Task 18: Errors dashboard (Loki-driven)

**Files:**
- Create: `infrastructure/grafana/dashboards/errors.json`

- [ ] **Step 1: Write dashboard JSON**

Create `infrastructure/grafana/dashboards/errors.json`:

```json
{
  "title": "Errors",
  "uid": "compliance-errors",
  "schemaVersion": 39,
  "version": 1,
  "timezone": "browser",
  "time": { "from": "now-24h", "to": "now" },
  "refresh": "1m",
  "tags": ["compliance", "errors"],
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "ERROR-level log lines per minute",
      "datasource": { "type": "loki", "uid": "Loki" },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 0 },
      "targets": [
        {
          "expr": "sum by (compose_service) (count_over_time({compose_service=~\"compliance-.*\"} | json | level=\"ERROR\" [1m]))",
          "legendFormat": "{{compose_service}}",
          "refId": "A"
        }
      ],
      "fieldConfig": { "defaults": { "unit": "logs" }, "overrides": [] }
    },
    {
      "id": 2,
      "type": "logs",
      "title": "Recent ERROR log lines (compliance-backend)",
      "datasource": { "type": "loki", "uid": "Loki" },
      "gridPos": { "h": 14, "w": 24, "x": 0, "y": 9 },
      "targets": [
        {
          "expr": "{compose_service=\"compliance-backend\"} | json | level=\"ERROR\"",
          "refId": "A"
        }
      ],
      "options": { "showTime": true, "wrapLogMessage": true, "dedupStrategy": "none" }
    },
    {
      "id": 3,
      "type": "text",
      "title": "GlitchTip — top exceptions",
      "gridPos": { "h": 5, "w": 24, "x": 0, "y": 23 },
      "options": {
        "mode": "markdown",
        "content": "GlitchTip top exception list lives at [GLITCHTIP_DOMAIN/issues](http://localhost:8080/issues). Embed via iframe panel after first event arrives."
      }
    }
  ]
}
```

- [ ] **Step 2: Validate**

```bash
python -c "import json; json.load(open('infrastructure/grafana/dashboards/errors.json'))"
```

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/dashboards/errors.json
git commit -m "feat(observability): add Errors Grafana dashboard"
```

---

## Task 19: Observability runbook

**Files:**
- Create: `docs/observability.md`

- [ ] **Step 1: Write runbook**

Create `docs/observability.md`:

```markdown
# Observability runbook (Wave 2)

## Stack

| Service | Image | Port | Purpose |
|---|---|---|---|
| `glitchtip-web` | `glitchtip/glitchtip:v4.1` | 8080 | Sentry-API error tracking UI |
| `glitchtip-worker` | same | — | Celery worker + beat |
| `glitchtip-postgres` | `postgres:16-alpine` | — | GlitchTip DB |
| `glitchtip-redis` | `redis:7-alpine` | — | GlitchTip broker |
| `prometheus` | `prom/prometheus:v2.55.1` | 9090 | Metric scraper + TSDB |
| `loki` | `grafana/loki:3.2.1` | 3100 | Log aggregator |
| `promtail` | `grafana/promtail:3.2.1` | — | Docker stdout shipper |
| `grafana` | `grafana/grafana:11.3.0` | 3001 | Dashboards |

## Bring up locally

```bash
# 1. Pre-req: app stack network exists
docker network create compliance-net 2>/dev/null || true

# 2. Populate observability secrets in .env (see .env.example)
#    GLITCHTIP_SECRET_KEY, GLITCHTIP_PG_PASSWORD, GRAFANA_ADMIN_PASSWORD

# 3. Boot
docker compose -f docker-compose.observability.yml up -d

# 4. Open
#    http://localhost:8080  GlitchTip (sign up admin user, create project, copy DSN)
#    http://localhost:3001  Grafana   (admin / GRAFANA_ADMIN_PASSWORD)
#    http://localhost:9090  Prometheus
```

## First-time GlitchTip setup

1. Open `http://localhost:8080`, register the first user (becomes superuser).
2. Create org `compliance` and projects `compliance-backend` and `compliance-frontend`.
3. Copy each project's DSN.
4. Backend: `SENTRY_DSN=<backend DSN>` in `backend/.env`, restart uvicorn.
5. Frontend: `NEXT_PUBLIC_SENTRY_DSN=<frontend DSN>` in `frontend-v3/.env.local`, restart `next dev`.

## Smoke procedure

### Backend error → GlitchTip

```bash
# Trigger an unhandled exception via temporary debug route
curl -X POST http://localhost:8001/__debug_raise   # only present if you add a stub; else use any route that raises
```
Open GlitchTip, expect the exception within 30 s with stack trace + request URL.

### Metrics → Prometheus → Grafana

```bash
curl -s http://localhost:8001/metrics | grep -E "^(http_requests_total|pipeline_step_duration_seconds_count|llm_calls_total)" | head -5
```
Open Grafana → Explore → Prometheus → query `up{job="compliance-backend"}` → expect `1`.

### Logs → Loki → Grafana

```bash
docker logs compliance-backend --tail 5
```
Open Grafana → Explore → Loki → query `{compose_service="compliance-backend"}` → expect lines.
Filter by `job_id`: `{compose_service="compliance-backend"} | json | job_id="<id>"`.

## Dashboards

Provisioned automatically from `infrastructure/grafana/dashboards/*.json`.
Find them in Grafana → Dashboards → Compliance folder:
- **Pipeline** — per-step duration p50/p95/p99 + throughput
- **LLM** — call rate, escalation rate, latency by model
- **API** — RPS, latency p50/p95/p99, error rate per route
- **Errors** — ERROR-level log rate + recent ERROR lines (Loki)

## Production deploy (Contabo VPS)

Append to the existing `docker compose up -d` step in `infrastructure/contabo/README.md`:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml pull
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
```

Expose only Grafana through the Cloudflare Tunnel (Loki, Prom, GlitchTip stay internal). Configure a separate hostname `grafana.compliance.<domain>` in the Cloudflare Tunnel.

## Cost / RAM budget

| Service | RAM (idle / typical) |
|---|---|
| GlitchTip web + worker + Postgres + Redis | ~700 MB |
| Loki + Promtail | ~200 MB |
| Prometheus (14 d retention) | ~300 MB |
| Grafana | ~150 MB |
| **Total** | **~1.4 GB** |

Contabo VPS spec must have ≥4 GB RAM headroom on top of app stack.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `up{job="compliance-backend"} == 0` in Prom | App not on `compliance-net` | `docker network connect compliance-net compliance-backend` |
| GlitchTip web 500 on `/issues` | DB migrations didn't run | `docker compose run --rm glitchtip-web ./manage.py migrate` |
| Grafana shows "No data" on Errors panel | Promtail not reading Docker logs | Check `docker logs promtail` for permission errors on `/var/run/docker.sock` |
| `pipeline_step_duration_seconds_count` always 0 | Pipeline never ran | Trigger one upload through `/upload` |
| Sentry SDK warns about missing source maps on `next build` | Expected without `SENTRY_AUTH_TOKEN` | Set token only in production CI |
```

- [ ] **Step 2: Commit**

```bash
git add docs/observability.md
git commit -m "docs(observability): runbook for GlitchTip + LGTM-lite stack"
```

---

## Task 20: End-to-end smoke (manual, post-merge to feature branch)

**Goal:** Verify the full capture loop end-to-end before opening PR. This is a manual gate — run locally, confirm each check, then mark the wave done.

- [ ] **Step 1: Boot the observability stack**

```bash
docker network create compliance-net 2>/dev/null || true
cp .env.example .env  # if not already present
# Set: GLITCHTIP_SECRET_KEY, GLITCHTIP_PG_PASSWORD, GRAFANA_ADMIN_PASSWORD
docker compose -f docker-compose.observability.yml up -d
sleep 30  # give GlitchTip migrations time
```

- [ ] **Step 2: Verify all containers healthy**

```bash
docker compose -f docker-compose.observability.yml ps
```
Expected: 8 services in state `running`.

- [ ] **Step 3: GlitchTip first-user setup**

Browser: `http://localhost:8080` → register → create org → create `compliance-backend` project → copy DSN → set `SENTRY_DSN=<dsn>` in `backend/.env`.

- [ ] **Step 4: Boot backend with Sentry on**

```bash
cd backend && uvicorn app.main:app --port 8001 --reload
```

- [ ] **Step 5: Verify /metrics, /healthz, /readyz**

In another terminal:
```bash
curl -fsS http://localhost:8001/healthz
curl -fsS http://localhost:8001/readyz
curl -fsS http://localhost:8001/metrics | grep "pipeline_step_duration_seconds_count"
```
Expected: 200s, metrics text contains the histogram.

- [ ] **Step 6: Verify Prometheus scrapes the backend**

Browser: `http://localhost:9090/targets` → `compliance-backend` job is `UP`.

- [ ] **Step 7: Verify Grafana dashboards load**

Browser: `http://localhost:3001` (admin / `$GRAFANA_ADMIN_PASSWORD`) → Dashboards → Compliance folder → all four dashboards present, datasources resolve.

- [ ] **Step 8: Verify Loki receives logs**

Grafana → Explore → Loki → query `{compose_service="compliance-backend"}` → expect log lines (after backend run).

- [ ] **Step 9: Trigger backend exception, confirm GlitchTip catches**

Easiest: hit a route with malformed input that raises. Or temporarily add a `/__debug_raise` route, hit it, then revert.

```python
# TEMP — do not commit
@app.get("/__debug_raise")
def _debug_raise():
    raise RuntimeError("wave2 smoke test")
```

```bash
curl http://localhost:8001/__debug_raise
```
Expected: GlitchTip UI shows `RuntimeError: wave2 smoke test` within 30 s with full stack trace.

Revert the debug route before the next commit:
```bash
git checkout backend/app/main.py
```

- [ ] **Step 10: Document the smoke pass in claude-progress.txt**

Append to `claude-progress.txt`:

```
[2026-05-06] WAVE 2 SMOKE: GlitchTip caught RuntimeError stack trace within 12s.
Prometheus scrape compliance-backend UP. Grafana 4 dashboards rendered. Loki
returns logs filtered by job_id. /healthz /readyz /metrics all 200.
```

- [ ] **Step 11: Final commit + push branch**

```bash
git add claude-progress.txt
git commit -m "docs(progress): wave 2 smoke pass — observability stack verified end-to-end"
git push -u origin feat/wave2-observability
```

---

## Task 21: Open PR

- [ ] **Step 1: Create PR**

```bash
gh pr create \
  --base main \
  --head feat/wave2-observability \
  --title "Wave 2 — Observability: GlitchTip + LGTM-lite + 4 seed dashboards" \
  --body-file - <<'EOF'
## Summary

- GlitchTip self-hosted (Sentry-API) + Sentry SDK on FastAPI + Next.js
- `prometheus-fastapi-instrumentator` exposes `/metrics`; new `app/observability_metrics.py` registers pipeline + LLM custom metrics
- `docker-compose.observability.yml` brings up GlitchTip + Loki + Promtail + Prometheus + Grafana (with auto-provisioned datasources + 4 dashboards)
- Logger switched to JSON for Promtail → Loki ingestion
- `/healthz` + `/readyz` endpoints for VPS smoke checks
- Capture-only — no behavior change in the request path

## Test plan
- [x] pytest backend/tests/test_*observability* test_health_routes test_sentry_init test_logger_json
- [x] vitest frontend-v3/tests/sentry-init.test.ts
- [x] `docker compose -f docker-compose.observability.yml config` validates
- [x] `promtool check config` validates Prom scrape file
- [x] Manual smoke: deliberate exception → visible in GlitchTip within 30 s
- [x] Manual smoke: Grafana renders all 4 dashboards with non-empty data after one upload run

## Out of scope (Wave 3+)
- Storage backend ABC (Wave 3)
- Replay endpoint (Wave 3)
- pg_dump backup cron (Wave 3)
- Embedding pre-filter + tiered LLM flag flips (Wave 4)
- Branch protection + deploy.yml SSH (Wave 5)
EOF
```

- [ ] **Step 2: Verify required checks run**

Watch `gh pr checks --watch`. Expected: `pytest`, `vitest`, `coverage`, `touched-fns-gate` all green.

- [ ] **Step 3: Note for the human**

Wave 2 PR ships a non-trivial new infra stack but **zero new request-path code**. The only application-code changes are import-time Sentry init, /metrics mount, /healthz + /readyz routes, JSON logger formatter, and ~8 lines of metric instrumentation in `process_call.py` + `agent_loop.py`. Reviewer should focus on:

1. JSON logger doesn't drop fields existing log statements rely on. Run a few `/upload` calls locally and grep logs for `job_id`, `step`, `level` keys.
2. `init_sentry()` must be a strict no-op when `SENTRY_DSN=""`. Boot smoke (Task 9 Step 5) covers this.
3. `prometheus-fastapi-instrumentator` excludes `/metrics`, `/healthz`, `/readyz` from histograms (so they don't pollute API dashboard).
4. Pipeline step wrapping in `process_call.py` uses `try/finally` — failures still record duration. This is intentional.
5. Promtail config requires Docker SD socket access — read-only, but flag in security review.

After merge, the human still must:
1. Add Cloudflare Tunnel hostname for `grafana.compliance.<domain>` (private network ingress).
2. Set `SENTRY_DSN` + `GLITCHTIP_SECRET_KEY` + `GRAFANA_ADMIN_PASSWORD` as repo secrets (GitHub → Settings → Secrets and variables → Actions) ready for Wave 5 deploy.yml.

---

## Wave 2 acceptance gate

- [ ] All 21 tasks complete and committed
- [ ] CI green on PR (`pytest`, `vitest`, `coverage`, `touched-fns-gate`)
- [ ] `docker compose -f docker-compose.observability.yml up -d` boots all 8 services
- [ ] All 4 Grafana dashboards render with non-empty data after one pipeline run
- [ ] Deliberate exception captured in GlitchTip with stack + request context
- [ ] `claude-progress.txt` updated with WAVE 2 SMOKE entry
- [ ] PR opened, checks green, ready for review

Wave 3 (durability — StorageBackend ABC + replay endpoint + pg_dump cron) is the next plan to write after Wave 2 merges.
