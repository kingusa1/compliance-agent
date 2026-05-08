# Wave 1 — Foundation: CI + IaC + Audit Implementation Plan (v3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **v3 changes (2026-05-06):** Pivoted IaC from Hetzner to Contabo. Contabo
> Terraform provider too thin for full VM IaC; Wave 1 keeps only
> Cloudflare DNS as Terraform-managed; Contabo VPS lifecycle documented as
> SSH + Docker Compose runbook in `infrastructure/contabo/README.md`.
>
> **v2 changes (2026-05-06):** Split T8 into T8a–T8e (one router each). Restructured T9–T10 so subagent writes scaffolding only; human runs `tofu init/import/plan` locally. Added Step 0 to T7/T8a–e for route-path verification. T3 coverage threshold derived from current floor, not hard-coded. T5 adds DB-up prereq. T6 branches on whether an exhaustion handler already exists. T11 flags PAT rotation as Wave-5 prereq.

**Goal:** Establish CI required-checks (pytest + vitest + coverage + touched-fns gate), commit infrastructure-as-code for Cloudflare DNS via OpenTofu plus a Contabo VPS runbook, add the `failed_jobs` table, and expand `record_audit()` coverage across mutating routers. Wave 1 of 5.

**Architecture:** Three independent sub-blocks land in one wave because none touches request-path code. (a) GitHub Actions workflows + verification gate run only on PR/CI hardware. (b) OpenTofu manages Cloudflare DNS only; Contabo VPS lifecycle is documented as an SSH + Docker Compose runbook (Contabo provider too thin for full VM IaC). (c) Postgres adds `failed_jobs` (new); `audit_log` and `record_audit()` already exist (Alembic mig `497bd38e5551`) and only need wider call-site coverage.

**Tech Stack:** GitHub Actions, pytest + pytest-cov, vitest, OpenTofu 1.6+, Cloudflare provider, Alembic 1.14, SQLAlchemy 2.0, FastAPI 0.115.

**Spec source:** `docs/superpowers/specs/2026-05-06-enterprise-hardening-inject-design.md` §9 Wave 1.

**Wave 2–5 deferred** to separate plans, generated after this wave verifies green. (Per "ONE feature at a time" global rule + writing-plans scope-check.)

---

## File Structure

| Path | New / Mod | Responsibility |
|---|---|---|
| `.github/pull_request_template.md` | NEW | PR template (T1 — already shipped, commit `1f34bd1`) |
| `.github/workflows/test.yml` | NEW | pytest + vitest + label-gated playwright |
| `.github/workflows/coverage.yml` | NEW | `pytest-cov` w/ threshold = current floor |
| `.github/workflows/touched-fns-gate.yml` | NEW | Refuse PR with prod changes but no test changes |
| `infrastructure/contabo/versions.tf` | NEW | OpenTofu + Cloudflare provider versions |
| `infrastructure/contabo/variables.tf` | NEW | Variable declarations (`vps_ipv4` maintained manually) |
| `infrastructure/contabo/dns.tf` | NEW | Cloudflare DNS records (single Terraform-managed resource) |
| `infrastructure/contabo/.gitignore` | NEW | Ignore tfstate, tfvars |
| `infrastructure/contabo/README.md` | NEW | Contabo SSH + Docker Compose runbook + Cloudflare DNS import instructions |
| `backend/alembic/versions/<rev>_failed_jobs.py` | NEW | `failed_jobs` table migration |
| `backend/app/models.py` | MOD | Append `FailedJob` ORM class |
| `backend/app/observability_routes.py` | MOD | Add GET `/observability/audit` + `/observability/failed-jobs` |
| `backend/app/workflows/redispatch_watchdog.py` | MOD | `record_failed_job()` + `_handle_exhausted_run()` |
| `backend/app/routes.py` | MOD | `record_audit()` on upload + lifecycle mutations |
| `backend/app/hitl_routes.py` | MOD | `record_audit()` on claim/release/lock-override |
| `backend/app/rules_routes.py` | MOD | `record_audit()` on rule mutations |
| `backend/app/script_routes.py` | MOD | `record_audit()` on script mutations |
| `backend/app/deals_routes.py` | MOD | `record_audit()` on deal mutations |
| `backend/tests/test_failed_jobs.py` | NEW | Migration + writer + integration |
| `backend/tests/test_observability_routes_audit.py` | NEW | Read routes |
| `backend/tests/test_audit_coverage.py` | NEW | Per-router audit assertions (5 sub-tests) |
| `backend/requirements.txt` | MOD | Add `pytest-cov==5.0.0` |

---

## Task 1 — DONE (PR template, commit 1f34bd1)

Skip — shipped in v1 of this plan before revision.

---

## Task 2: Test workflow (pytest + vitest)

**Files:**
- Create: `.github/workflows/test.yml`

- [ ] **Step 1: Verify frontend test scripts exist**

```bash
cd frontend-v3 && npm pkg get scripts.test:unit scripts.test:e2e
```

If either returns `{}` or empty, add to `frontend-v3/package.json`:

```json
"scripts": {
  "test:unit": "vitest",
  "test:e2e": "playwright test"
}
```

Commit any package.json change separately with message `chore(frontend): add test:unit + test:e2e npm scripts` before continuing.

- [ ] **Step 2: Write workflow**

```yaml
name: test
on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: test-${{ github.ref }}
  cancel-in-progress: true

jobs:
  pytest:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
          cache-dependency-path: backend/requirements.txt
      - run: pip install -r requirements.txt
      - run: pytest -v --tb=short

  vitest:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend-v3
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'
          cache-dependency-path: frontend-v3/package-lock.json
      - run: npm ci
      - run: npm run test:unit -- --run

  playwright:
    runs-on: ubuntu-latest
    if: contains(github.event.pull_request.labels.*.name, 'e2e')
    defaults:
      run:
        working-directory: frontend-v3
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'
          cache-dependency-path: frontend-v3/package-lock.json
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npm run test:e2e
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add test workflow (pytest + vitest + label-gated playwright)"
```

- [ ] **Step 4: Note for the human**

After Wave 1 merges to main, watch the workflow run. If `pytest` fails because the existing test suite has prior breakage, fix in a separate PR — do not silence in this workflow.

---

## Task 3: Coverage workflow (pytest-cov gate at current floor)

**Files:**
- Create: `.github/workflows/coverage.yml`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add pytest-cov to requirements**

Append to `backend/requirements.txt`:

```
pytest-cov==5.0.0
```

- [ ] **Step 2: Measure current floor coverage**

```bash
cd backend && pip install pytest-cov && pytest --cov=app --cov-report=term 2>&1 | tail -20
```

Read the `TOTAL` line. Note the percentage. Round **down** to the nearest 5%. That value is `FLOOR` for the next step. (Example: if total is 47%, FLOOR=45.) If pytest itself fails to run, escalate as BLOCKED — this gate cannot be set without a baseline.

- [ ] **Step 3: Write workflow**

Replace `<FLOOR>` in the workflow below with the number from Step 2:

```yaml
name: coverage
on:
  pull_request:
  push:
    branches: [main]

jobs:
  coverage:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
          cache-dependency-path: backend/requirements.txt
      - run: pip install -r requirements.txt
      - name: Determine touched python files
        id: diff
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            BASE="${{ github.event.pull_request.base.sha }}"
            HEAD="${{ github.event.pull_request.head.sha }}"
          else
            BASE="${{ github.event.before }}"
            HEAD="${{ github.event.after }}"
          fi
          TOUCHED=$(git diff --name-only "$BASE" "$HEAD" -- 'backend/app/**/*.py' | sed 's|backend/||' | tr '\n' ',' | sed 's/,$//')
          echo "touched=$TOUCHED" >> $GITHUB_OUTPUT
      - name: Run pytest with coverage gate
        run: |
          if [ -z "${{ steps.diff.outputs.touched }}" ]; then
            echo "No backend python changes — skipping coverage gate."
            exit 0
          fi
          pytest --cov=app --cov-report=term-missing --cov-fail-under=<FLOOR>
```

- [ ] **Step 4: Document the ratchet plan**

In the PR description (or a follow-up commit to `docs/superpowers/specs/2026-05-06-enterprise-hardening-inject-design.md` §12 open-questions), record: "Wave 1 set coverage threshold at `<FLOOR>%` (current measured baseline). Ratchet to next +5% step only after a milestone shows coverage genuinely climbed."

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt .github/workflows/coverage.yml
git commit -m "ci: add coverage workflow with --cov-fail-under at current floor"
```

---

## Task 4: Touched-functions gate

**Files:**
- Create: `.github/workflows/touched-fns-gate.yml`

- [ ] **Step 1: Write workflow**

```yaml
name: touched-fns-gate
on:
  pull_request:

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Refuse prod changes without test changes in same branch
        run: |
          BASE="${{ github.event.pull_request.base.sha }}"
          HEAD="${{ github.event.pull_request.head.sha }}"
          DIFF=$(git diff --name-only "$BASE" "$HEAD")
          PROD=$(echo "$DIFF" | grep -E '^(backend/app|frontend-v3/src)/.*\.(py|ts|tsx)$' | grep -vE '/(tests?|__tests__)/' || true)
          TESTS=$(echo "$DIFF" | grep -E '/(tests?|__tests__)/.*\.(py|ts|tsx)$' || true)
          if [ -n "$PROD" ] && [ -z "$TESTS" ]; then
            echo "::error::Prod files changed without any test file change in same PR."
            echo "Prod files changed:"; echo "$PROD"
            echo "Add or update tests, or label PR 'no-tests-acceptable' (rare; e.g. config-only)."
            if echo '${{ join(github.event.pull_request.labels.*.name, ',') }}' | grep -q 'no-tests-acceptable'; then
              echo "Label 'no-tests-acceptable' set — bypassing gate."
              exit 0
            fi
            exit 1
          fi
          echo "Prod/test parity OK."
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/touched-fns-gate.yml
git commit -m "ci: refuse prod changes without test edits in same PR"
```

- [ ] **Step 3: Note for the human**

The probe-PR verification step requires an actual GitHub PR. After the wave merges, open a no-test-edit probe PR to confirm the gate fires red, then close without merging. Subagents cannot drive the GitHub UI.

---

## Task 5: failed_jobs migration + ORM

**Prereq check (subagent runs first):**
- `backend/.env` exists with `DATABASE_URL` set.
- `psql "$DATABASE_URL" -c 'SELECT 1'` succeeds (Postgres reachable).
- `backend/tests/conftest.py` exists.

If any prereq fails, report `BLOCKED` with the specific failure. Do not write tests against an unconfigured DB.

**Files:**
- Create: `backend/alembic/versions/<rev>_failed_jobs.py` (revision id auto-generated; rename file accordingly)
- Modify: `backend/app/models.py` (append `FailedJob` ORM class + any missing imports)
- Create: `backend/tests/test_failed_jobs.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_failed_jobs.py`:

```python
"""Test failed_jobs table + writer.

Coverage:
  - migration creates table with expected columns + indexes
  - FailedJob ORM round-trips
  - record_failed_job() writes one row idempotently per (call_id, attempts)
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect

from app.database import SessionLocal, engine


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_failed_jobs_table_exists():
    insp = inspect(engine)
    assert "failed_jobs" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("failed_jobs")}
    assert {
        "id", "call_id", "last_step", "attempts", "last_error",
        "exhausted_at", "created_at",
    }.issubset(cols)


def test_failed_jobs_writer(db):
    from app.models import Call, FailedJob
    from app.workflows.redispatch_watchdog import record_failed_job

    call = Call(id=str(uuid.uuid4()), filename="t.mp3", status="failed")
    db.add(call)
    db.commit()

    record_failed_job(
        db,
        call_id=call.id,
        last_step="analyze_checkpoints",
        attempts=3,
        last_error="OpenAI 429",
    )
    db.commit()

    rows = db.query(FailedJob).filter_by(call_id=call.id).all()
    assert len(rows) == 1
    assert rows[0].last_step == "analyze_checkpoints"
    assert rows[0].attempts == 3


def test_failed_jobs_writer_is_idempotent(db):
    """Same (call_id, attempts) twice → still one row."""
    from app.models import Call, FailedJob
    from app.workflows.redispatch_watchdog import record_failed_job

    call = Call(id=str(uuid.uuid4()), filename="t.mp3", status="failed")
    db.add(call); db.commit()
    record_failed_job(db, call_id=call.id, last_step="x", attempts=3, last_error="a")
    db.commit()
    record_failed_job(db, call_id=call.id, last_step="x", attempts=3, last_error="b")
    db.commit()

    assert db.query(FailedJob).filter_by(call_id=call.id).count() == 1
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
cd backend && pytest tests/test_failed_jobs.py -v
```

Expected: FAIL — `FailedJob` import error or table missing.

- [ ] **Step 3: Generate Alembic revision**

```bash
cd backend && alembic revision -m "failed_jobs"
```

Note the revision id printed (e.g. `a1b2c3d4e5f6`). Open the new file under `backend/alembic/versions/`.

- [ ] **Step 4: Edit the new migration file**

Replace `upgrade()` and `downgrade()`:

```python
def upgrade() -> None:
    op.create_table(
        "failed_jobs",
        sa.Column("id", sa.String(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()::text")),
        sa.Column("call_id", sa.String(), sa.ForeignKey("calls.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("last_step", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_failed_jobs_call_attempt",
                    "failed_jobs", ["call_id", "attempts"], unique=True)
    op.create_index("ix_failed_jobs_exhausted_at",
                    "failed_jobs", ["exhausted_at"])


def downgrade() -> None:
    op.drop_index("ix_failed_jobs_exhausted_at", table_name="failed_jobs")
    op.drop_index("ix_failed_jobs_call_attempt", table_name="failed_jobs")
    op.drop_table("failed_jobs")
```

- [ ] **Step 5: Append `FailedJob` ORM to `backend/app/models.py`**

```python
class FailedJob(Base):
    __tablename__ = "failed_jobs"

    id = Column(String, primary_key=True,
                default=lambda: str(uuid.uuid4()))
    call_id = Column(String, ForeignKey("calls.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    last_step = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    exhausted_at = Column(DateTime(timezone=True), server_default=func.now(),
                          nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                        nullable=False)

    __table_args__ = (
        UniqueConstraint("call_id", "attempts", name="ix_failed_jobs_call_attempt"),
    )
```

If `uuid`, `Column`, `String`, `ForeignKey`, `Integer`, `Text`, `DateTime`, `func`, `UniqueConstraint`, `Base` are not already imported at the top of `models.py`, add them. Match existing import style.

- [ ] **Step 6: Add `record_failed_job()` to `backend/app/workflows/redispatch_watchdog.py`**

Append at end:

```python
def record_failed_job(
    db: Session,
    *,
    call_id: str,
    last_step: str,
    attempts: int,
    last_error: str | None = None,
) -> None:
    """Insert one failed_jobs row. Idempotent on (call_id, attempts).

    Caller commits.
    """
    from app.models import FailedJob

    existing = (
        db.query(FailedJob)
        .filter_by(call_id=call_id, attempts=attempts)
        .first()
    )
    if existing is not None:
        return
    db.add(FailedJob(
        call_id=call_id,
        last_step=last_step,
        attempts=attempts,
        last_error=(last_error or "")[:4000],
    ))
```

If `Session` not imported, `from sqlalchemy.orm import Session`.

- [ ] **Step 7: Run migration locally**

```bash
cd backend && alembic upgrade head
```

Expected: revision applies; `failed_jobs` exists.

- [ ] **Step 8: Run test, verify PASS**

```bash
cd backend && pytest tests/test_failed_jobs.py -v
```

Expected: 3 PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/alembic/versions/*_failed_jobs.py backend/app/models.py \
        backend/app/workflows/redispatch_watchdog.py backend/tests/test_failed_jobs.py
git commit -m "feat(durability): failed_jobs table + record_failed_job writer"
```

---

## Task 6: Wire failed_jobs writer into redispatch_watchdog (branching)

**Files:**
- Modify: `backend/app/workflows/redispatch_watchdog.py`
- Modify: `backend/tests/test_failed_jobs.py`

- [ ] **Step 1: Inspect existing watchdog code path**

```bash
grep -n -E "exhaust|max_attempts|retried|failed|attempts" backend/app/workflows/redispatch_watchdog.py
```

Two outcomes:

**Outcome A** — there is an existing branch that handles "Inngest reports run exhausted retries" or "stuck call gives up." In that branch, you add a call to `record_failed_job(...)` and an explicit status flip to `failed`.

**Outcome B** — no such branch exists; the watchdog only redispatches stuck runs. In that case you add a fresh `_handle_exhausted_run(...)` helper plus the call site.

Decide which outcome applies before continuing. Note your decision in the commit message.

- [ ] **Step 2: Write failing test**

Append to `backend/tests/test_failed_jobs.py`:

```python
def test_handle_exhausted_run_writes_failed_jobs_row(db):
    """When _handle_exhausted_run is invoked, it writes one failed_jobs row
    and flips the Call.status to failed."""
    from app.models import Call, FailedJob
    from app.workflows.redispatch_watchdog import _handle_exhausted_run

    call = Call(id=str(uuid.uuid4()), filename="t.mp3", status="processing",
                last_step_name="analyze_checkpoints", last_step_error="boom")
    db.add(call); db.commit()

    _handle_exhausted_run(db, call_id=call.id, attempts=3)
    db.commit()

    rows = db.query(FailedJob).filter_by(call_id=call.id).all()
    assert len(rows) == 1
    assert rows[0].last_error == "boom"
    db.refresh(call)
    assert call.status == "failed"
```

- [ ] **Step 3: Run, verify FAIL**

```bash
cd backend && pytest tests/test_failed_jobs.py::test_handle_exhausted_run_writes_failed_jobs_row -v
```

Expected: FAIL — `_handle_exhausted_run` not defined.

- [ ] **Step 4: Add `_handle_exhausted_run()` to redispatch_watchdog.py**

```python
def _handle_exhausted_run(db: Session, *, call_id: str, attempts: int) -> None:
    """Mark Call failed and write the forensic failed_jobs row.

    Reads `last_step_name` + `last_step_error` from the Call row (already
    populated by the per-step writer in process_call.py) so we never need
    the original Inngest payload.
    """
    from app.models import Call

    call = db.query(Call).filter_by(id=call_id).first()
    if call is None:
        return
    last_step = (getattr(call, "last_step_name", None) or "unknown")
    last_error = (getattr(call, "last_step_error", None) or "")
    record_failed_job(
        db, call_id=call_id, last_step=last_step,
        attempts=attempts, last_error=last_error,
    )
    if call.status != "failed":
        call.status = "failed"
```

- [ ] **Step 5: Wire the call site**

If Outcome A: replace the existing exhaustion branch's body with a call to `_handle_exhausted_run(db, call_id=..., attempts=...)`.
If Outcome B: in the watchdog's main loop, after detecting a run is past its retry budget, call `_handle_exhausted_run(...)`. Add a clear inline comment indicating this was added in Wave 1.

- [ ] **Step 6: Run, verify PASS**

```bash
cd backend && pytest tests/test_failed_jobs.py -v
```

Expected: 4 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/workflows/redispatch_watchdog.py backend/tests/test_failed_jobs.py
git commit -m "feat(durability): _handle_exhausted_run writes failed_jobs (outcome <A|B>)"
```

---

## Task 7: GET /observability/audit + /observability/failed-jobs

**Files:**
- Modify: `backend/app/observability_routes.py`
- Create: `backend/tests/test_observability_routes_audit.py`

- [ ] **Step 0: Verify existing router prefix**

```bash
grep -nE "APIRouter|prefix=" backend/app/observability_routes.py | head -5
grep -nE "include_router.*observability" backend/app/main.py
```

Note the actual mount prefix (e.g. `/observability` vs `/api/observability`). The full URL paths used in tests below MUST match the actual prefix. If the existing router has prefix `/observability`, the tests use `/observability/audit`. If different, substitute.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_observability_routes_audit.py` (substitute `<PREFIX>` from Step 0):

```python
"""GET <PREFIX>/audit + <PREFIX>/failed-jobs routes."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.audit import record_audit
from app.database import SessionLocal
from app.main import app
from app.workflows.redispatch_watchdog import record_failed_job


@pytest.fixture
def client(): return TestClient(app)


def test_audit_route_returns_recent_rows(client):
    db = SessionLocal()
    try:
        record_audit(db, action="probe", entity_type="test",
                     entity_id="x", payload={"a": 1})
        db.commit()
    finally:
        db.close()
    r = client.get("<PREFIX>/audit?limit=10")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert any(row["action"] == "probe" for row in rows)


def test_failed_jobs_route_returns_recent_rows(client):
    from app.models import Call

    db = SessionLocal()
    cid = str(uuid.uuid4())
    try:
        db.add(Call(id=cid, filename="t.mp3", status="failed"))
        db.commit()
        record_failed_job(db, call_id=cid, last_step="transcribe",
                          attempts=3, last_error="x")
        db.commit()
    finally:
        db.close()
    r = client.get("<PREFIX>/failed-jobs?limit=10")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert any(row["call_id"] == cid for row in rows)
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd backend && pytest tests/test_observability_routes_audit.py -v
```

Expected: FAIL with 404.

- [ ] **Step 3: Add routes to `backend/app/observability_routes.py`**

Identify the existing router variable (e.g. `observability_router`). Add:

```python
from fastapi import Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FailedJob


@observability_router.get("/audit")
def list_audit(
    limit: int = Query(50, ge=1, le=500),
    action: str | None = None,
    actor_id: str | None = None,
    db: Session = Depends(get_db),
):
    q = (
        "SELECT id, occurred_at, organization_id, actor_id, action, "
        "       entity_type, entity_id, payload, prev_hash, this_hash "
        "FROM audit_log WHERE 1=1"
    )
    params: dict[str, object] = {"limit": limit}
    if action:
        q += " AND action = :action"; params["action"] = action
    if actor_id:
        q += " AND actor_id = :actor"; params["actor"] = actor_id
    q += " ORDER BY occurred_at DESC, id DESC LIMIT :limit"
    rows = [dict(r._mapping) for r in db.execute(text(q), params).fetchall()]
    return {"rows": rows}


@observability_router.get("/failed-jobs")
def list_failed_jobs(
    limit: int = Query(50, ge=1, le=500),
    call_id: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(FailedJob).order_by(FailedJob.exhausted_at.desc())
    if call_id:
        q = q.filter(FailedJob.call_id == call_id)
    rows = [
        {
            "id": r.id, "call_id": r.call_id, "last_step": r.last_step,
            "attempts": r.attempts, "last_error": r.last_error,
            "exhausted_at": r.exhausted_at.isoformat() if r.exhausted_at else None,
        }
        for r in q.limit(limit).all()
    ]
    return {"rows": rows}
```

If `get_db` is not the existing dependency, use whatever the file already uses (verify with `grep -n "Depends" backend/app/observability_routes.py`).

- [ ] **Step 4: Run, verify PASS**

```bash
cd backend && pytest tests/test_observability_routes_audit.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/observability_routes.py backend/tests/test_observability_routes_audit.py
git commit -m "feat(observability): GET <PREFIX>/{audit,failed-jobs} routes"
```

---

## Tasks T8a–T8e: Expand record_audit() across mutating routers

Five separate small tasks. Each touches one router + adds one focused test. Each task is its own commit.

**Common Step 0 (every T8 sub-task):**

Verify the actual route paths and request shapes by reading the router file you're modifying:

```bash
grep -nE "@.*\.(post|put|patch|delete)" backend/app/<router>.py
```

If a route's path differs from what the test below assumes, substitute the actual path.

**Common pattern** for the audit call:

```python
from app.audit import record_audit

# inside the route handler, after db has the new/updated row but before commit:
record_audit(
    db,
    action="<entity>.<action>",
    entity_type="<entity>",
    entity_id=<resource_id>,
    payload={...},          # only stable, non-PII fields
    actor_id=request.headers.get("x-user-id"),
)
# then db.commit()
```

`request: Request` must be a parameter on the route handler — add it if missing. The actor_id header convention `x-user-id` matches existing usage in `import_xlsx_routes.py`; keep consistent.

---

### Task 8a: routes.py (upload + edit_metadata)

**Files:**
- Modify: `backend/app/routes.py`
- Create: `backend/tests/test_audit_coverage.py` (first sub-test)

- [ ] **Step 0:** verify upload + edit_metadata route paths via grep above.

- [ ] **Step 1: Failing test**

Create `backend/tests/test_audit_coverage.py`:

```python
"""Every mutating route writes one audit row."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal
from app.main import app


@pytest.fixture
def client(): return TestClient(app)


def _audit_count(action: str) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            text("SELECT count(*) FROM audit_log WHERE action = :a"),
            {"a": action},
        ).scalar()
    finally:
        db.close()


def test_upload_writes_audit(client, tmp_path):
    before = _audit_count("call.upload")
    audio = (tmp_path / "t.mp3"); audio.write_bytes(b"fake audio")
    with open(audio, "rb") as f:
        # Substitute actual upload route path from Step 0
        r = client.post("/upload", files={"file": ("t.mp3", f, "audio/mpeg")},
                        headers={"x-user-id": "test-user"})
    assert r.status_code in (200, 202)
    assert _audit_count("call.upload") == before + 1
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Wire `record_audit()` in `routes.py` upload handler.** Action `call.upload`. Payload `{"filename": file.filename, "size": file.size}`.
- [ ] **Step 4: If `routes.py` has an edit-metadata handler, wire it too.** Action `call.edit_metadata`. Add a second test analogous to the upload one.
- [ ] **Step 5: Run, verify PASS.**
- [ ] **Step 6: Commit**

```bash
git add backend/app/routes.py backend/tests/test_audit_coverage.py
git commit -m "feat(audit): record_audit() on /upload + edit_metadata"
```

---

### Task 8b: hitl_routes.py (claim/release/lock_override)

**Files:**
- Modify: `backend/app/hitl_routes.py`
- Modify: `backend/tests/test_audit_coverage.py` (append)

- [ ] **Step 0:** verify claim/release/lock-override route paths.
- [ ] **Step 1: Failing test** — append:

```python
def test_hitl_claim_release_writes_audit(client):
    cid = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO calls (id, filename, status) "
            "VALUES (:i, 't.mp3', 'analyzed')"
        ), {"i": cid}); db.commit()
    finally:
        db.close()

    before_claim = _audit_count("hitl.claim")
    before_release = _audit_count("hitl.release")
    # Substitute actual paths from Step 0
    r = client.post(f"/hitl/calls/{cid}/claim",
                    json={"reviewer_id": "test-user"},
                    headers={"x-user-id": "test-user"})
    assert r.status_code in (200, 201)
    r = client.post(f"/hitl/calls/{cid}/release",
                    json={"reviewer_id": "test-user"},
                    headers={"x-user-id": "test-user"})
    assert r.status_code in (200, 204)

    assert _audit_count("hitl.claim") == before_claim + 1
    assert _audit_count("hitl.release") == before_release + 1
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Wire `record_audit()` in claim, release, lock_override handlers.** Actions: `hitl.claim`, `hitl.release`, `hitl.lock_override`. Payload includes `reviewer_id`.
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Commit**

```bash
git add backend/app/hitl_routes.py backend/tests/test_audit_coverage.py
git commit -m "feat(audit): record_audit() on hitl claim/release/lock_override"
```

---

### Task 8c: rules_routes.py

**Files:**
- Modify: `backend/app/rules_routes.py`
- Modify: `backend/tests/test_audit_coverage.py`

- [ ] **Step 0:** verify rule create/update/delete route paths + shape.
- [ ] **Step 1: Failing test** — append a test that POSTs/PATCHes/DELETEs a rule and asserts `_audit_count("rule.create")` etc. increment by 1 each.
- [ ] **Step 2: FAIL.** **Step 3:** wire `record_audit()` in each handler. Actions: `rule.create`, `rule.update`, `rule.delete`. **Step 4:** PASS. **Step 5:** commit `feat(audit): record_audit() on rule create/update/delete`.

---

### Task 8d: script_routes.py

Same shape as 8c. Actions: `script.create`, `script.version`. Commit `feat(audit): record_audit() on script create/version`.

---

### Task 8e: deals_routes.py

Same shape as 8c. Actions: `deal.verdict`, `deal.resolve`. Commit `feat(audit): record_audit() on deal verdict/resolve`.

---

## Task 9: OpenTofu Cloudflare-only IaC + Contabo runbook (Option B)

**Subagent writes files. Subagent does NOT run `tofu init` or `tofu import`. The human runs those locally with credentials.**

> **v3 pivot:** Production VPS is Contabo, not Hetzner. The Contabo Terraform
> provider is too thin to manage VM lifecycle (no firewall primitives,
> sparse instance ops). Wave 1 keeps **only** Cloudflare DNS as
> Terraform-managed; the Contabo VPS is documented as an SSH + Docker
> Compose runbook in `infrastructure/contabo/README.md`. There is no
> `main.tf` — VM lifecycle is not in IaC.

**Files:**
- Create: `infrastructure/contabo/versions.tf`
- Create: `infrastructure/contabo/variables.tf`
- Create: `infrastructure/contabo/.gitignore`
- Create: `infrastructure/contabo/README.md`

- [ ] **Step 1: Write `versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.40"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
```

- [ ] **Step 2: Write `variables.tf`**

```hcl
variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone.DNS edit"
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone id for the apex domain"
  type        = string
}

variable "vps_ipv4" {
  description = "Public IPv4 of the Contabo VPS hosting compliance.<domain>. Maintained manually — Contabo provider too thin to manage VM lifecycle."
  type        = string
}

variable "subdomain" {
  description = "Subdomain under the zone (e.g. \"compliance\" for compliance.example.com, or \"@\" for the apex)"
  type        = string
  default     = "compliance"
}
```

- [ ] **Step 3: Write `.gitignore`**

```
.terraform/
.terraform.lock.hcl
*.tfstate
*.tfstate.*
*.tfvars
*.tfvars.json
!terraform.tfvars.example
```

- [ ] **Step 4: Write `README.md`**

The README is a Contabo SSH + Docker Compose runbook (replaces `apt-get`
provisioning, cloudflared tunnel registration, repo clone + `docker compose
up -d`) plus the Cloudflare DNS import instructions for the single
Terraform-managed resource. See the actual file for full content.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/contabo/versions.tf infrastructure/contabo/variables.tf \
        infrastructure/contabo/.gitignore infrastructure/contabo/README.md
git commit -m "iac(scaffold): opentofu Cloudflare provider + Contabo runbook"
```

---

## Task 10: OpenTofu dns.tf (single Cloudflare record, no main.tf)

**Subagent writes the file. Subagent does NOT run `tofu init/import/plan`.**

> **v3 pivot:** No `main.tf`. The Contabo VPS is not Terraform-managed;
> `var.vps_ipv4` is the manually-maintained source of truth for the A
> record's content.

**Files:**
- Create: `infrastructure/contabo/dns.tf`

- [ ] **Step 1: Write `dns.tf`**

```hcl
# ⚠️ DANGER: This file manages live DNS records pointing customers at production.
# Always `tofu plan` before any merge. A bad apply can break compliance.<domain>
# until DNS propagation completes.

data "cloudflare_zone" "this" {
  zone_id = var.cloudflare_zone_id
}

resource "cloudflare_record" "compliance_apex" {
  zone_id = data.cloudflare_zone.this.id
  name    = var.subdomain
  type    = "A"
  content = var.vps_ipv4
  ttl     = 1
  proxied = true
  comment = "managed by opentofu"
}
```

- [ ] **Step 2: Commit**

```bash
git add infrastructure/contabo/dns.tf
git commit -m "iac(scaffold): dns.tf — Cloudflare A record pointing at Contabo VPS"
```

- [ ] **Step 3: Note for human**

After merge, follow `infrastructure/contabo/README.md` "First-time setup".
Do not declare Wave 1 done until `tofu plan` reports zero changes.

---

## Task 11: Branch protection docs note + PAT rotation flag

**Files:**
- Modify: `infrastructure/contabo/README.md` (append two sections)

- [ ] **Step 1: Append branch-protection section**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add infrastructure/contabo/README.md
git commit -m "docs(iac): branch protection + PAT rotation prereq"
```

---

## Verification — Wave 1 done when

- [ ] `test.yml`, `coverage.yml`, `touched-fns-gate.yml` all run on the Wave-1 PR and finish green.
- [ ] `pytest tests/test_failed_jobs.py tests/test_observability_routes_audit.py tests/test_audit_coverage.py` green locally.
- [ ] `GET <PREFIX>/audit?limit=10` returns recent rows; tamper-evident hash chain still intact (`SELECT prev_hash, this_hash FROM audit_log ORDER BY occurred_at DESC LIMIT 5`).
- [ ] `GET <PREFIX>/failed-jobs?limit=10` returns rows after a forced exhaustion drill.
- [ ] Human ran `cd infrastructure/contabo && tofu init && tofu import cloudflare_record.compliance_apex <zone_id>/<record_id> && tofu plan`; `tofu plan` reports zero changes.
- [ ] Human applied branch-protection rules in GitHub Settings.
- [ ] Human revoked the leaked PAT and issued a new one (Wave-5 prereq, can finish anytime before Wave 5).
- [ ] Probe PR confirmed `touched-fns-gate` blocks PRs with prod changes lacking test edits.
- [ ] `feature_list.json` IDs 6, 7, 13, 14 still `passes: false` (UI surfacing arrives in Wave 2; flip then).

## Self-Review

Spec coverage check:
- ✅ §9 Wave 1 W1a → Tasks 1 (already shipped), 2, 3, 4
- ✅ §9 Wave 1 W1b → Tasks 9, 10, 11
- ✅ §9 Wave 1 W1c → Tasks 5, 6, 7, 8a–8e
- ✅ §3 success criterion #3 → Tasks 8a–8e
- ✅ §3 success criterion #8 → Tasks 2, 3, 4
- ✅ §3 success criterion #9 → Task 11
- ✅ §3 success criterion #10 → Task 4
- ✅ §3 success criterion #7 → Task 9 + 10 (human-completed)
- ✅ §10.1 PAT rotation prereq → Task 11
- ⏭ §3 success criteria #1, #2, #4, #5, #6 → Waves 2–5 (separate plans)

Type / signature consistency:
- `record_failed_job(db, *, call_id, last_step, attempts, last_error=None)` — Tasks 5, 6.
- `_handle_exhausted_run(db, *, call_id, attempts)` — defined Task 6, called from existing exhaustion site.
- `record_audit(db, *, action, entity_type, entity_id, payload, organization_id, actor_id)` — already exists in `app/audit.py`; reused unchanged in T8a–e.

Placeholder scan:
- `<rev>` in migration filename — explicit "rename after generation" pattern.
- `<PREFIX>` in T7 — explicit Step 0 substitution.
- `<A|B>` in T6 commit message — depends on Outcome at Step 1; explicit branching.
- `<zone_id>/<record_id>` in T9/T10 README — explicit "discover before import" pattern; surfaced in README Step 3.

No issues to fix.
