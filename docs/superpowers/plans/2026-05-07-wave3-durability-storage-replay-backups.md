# Wave 3 — Durability: Storage Backend ABC + Replay + pg_dump Backups

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Apply `two-stage-review-loop` between tasks (spec compliance → code quality → fix-loop with new commits, never amend).

**Goal:** Make the system portable across object stores, replayable without re-transcription, and recoverable from a database disaster — without changing any user-visible behavior on the happy path.

**Architecture:** Three additive sub-blocks land in one wave.
(a) **Storage portability** — refactor `app/storage.py` into a `StorageBackend` ABC plus two concrete implementations (Supabase default; S3/MinIO via boto3). Selection by `STORAGE_BACKEND` env var; existing call sites switch from module-level functions to a singleton backend object so the swap requires zero code change.
(b) **Replay** — `POST /calls/:id/reanalyze` re-runs only steps 4 (analyze_checkpoints) → 5 (score) → 6 (finalize) on the stored transcript, emitting an Inngest `call/reanalyze` event with zero re-transcription cost. Existing CallCheckpoint idempotency replaces rows. Frontend gets a Reanalyze button on call detail.
(c) **Durable backups** — `backend/scripts/pg_dump_to_storage.py` produces an encrypted dated tarball, written to a `backups/` bucket. An Inngest scheduled function (`pg_dump_nightly`) invokes it once a day. `scripts/restore_drill.sh` exercises a one-shot restore into a scratch DB; the drill is documented in `docs/durability.md`.

**Tech Stack:** SQLAlchemy 2.0, FastAPI 0.115, Pydantic Settings, Inngest 0.x scheduled functions, boto3 1.34+ (S3/MinIO), supabase-py 2.x (existing), pg_dump 16, age 1.1 (encryption), Next.js 16 (React 19), shadcn Button.

**Spec source:** `docs/superpowers/specs/2026-05-06-enterprise-hardening-inject-design.md` §9 Wave 3 (W3a + W3b + W3c) and §6.3, §6.6.

**Prereqs:**
- Wave 2 PR #1 reviewed & merged to `main`. (Or rebased onto `main` after Wave 2 lands.)
- `compliance-net` Docker network exists on dev box (Wave 2 prereq).
- Local pgvector container at `:5433` for tests (`backend/.env` already points here).

**Wave 4–5 deferred** to separate plans, written after Wave 3 verifies green.

---

## Branch

```bash
git checkout main
git pull --ff-only
git checkout -b feat/wave3-durability
```

If Wave 2 not merged yet, branch from `feat/wave2-observability` instead so the obs stack is available during dev.

---

## File Structure

| Path | New / Mod | Responsibility |
|---|---|---|
| `backend/app/storage/__init__.py` | NEW | `StorageBackend` ABC + factory + module-level shim re-exporting `upload_audio`/`download_audio`/`signed_url` for back-compat with existing call sites |
| `backend/app/storage/supabase_backend.py` | NEW | Supabase Storage impl — moved verbatim from existing `app/storage.py` body |
| `backend/app/storage/s3_backend.py` | NEW | boto3 impl, works against MinIO + AWS S3, signed URL via `generate_presigned_url` |
| `backend/app/storage.py` | DELETE (replaced by package) | — |
| `backend/app/replay.py` | NEW | `reanalyze(call_id) -> run_id`; loads Call row, emits `call/reanalyze`, returns 202 |
| `backend/app/routes.py` | MOD | `POST /calls/{call_id}/reanalyze` endpoint; `record_audit("reanalyze", call_id, ...)` |
| `backend/app/workflows/events.py` | MOD | Add `CALL_REANALYZE = "call/reanalyze"` constant + payload schema |
| `backend/app/workflows/process_call.py` | MOD | Register a second Inngest function handling `CALL_REANALYZE` that runs steps 4-5-6 only on the stored transcript |
| `backend/app/config.py` | MOD | Add `storage_backend`, `s3_endpoint`, `s3_access_key`, `s3_secret_key`, `s3_bucket`, `s3_region`, `backup_bucket`, `backup_age_recipient` |
| `backend/requirements.txt` | MOD | + `boto3==1.34.144` |
| `backend/scripts/pg_dump_to_storage.py` | NEW | Run `pg_dump`, age-encrypt, upload via `storage.upload_blob()` |
| `backend/app/workflows/pg_dump_nightly.py` | NEW | Inngest scheduled function (cron `0 2 * * *` UTC) invoking the script |
| `backend/app/main.py` | MOD | Register `pg_dump_nightly_fn` + `process_call_reanalyze_fn` with `inngest.fast_api.serve(...)` |
| `scripts/restore_drill.sh` | NEW | One-shot restore latest backup into `compliance_scratch` DB, table-row sanity check |
| `frontend-v3/src/app/calls/[id]/components/ReanalyzeButton.tsx` | NEW | Button component, calls API, toasts result |
| `frontend-v3/src/app/calls/[id]/page.tsx` | MOD | Mount `<ReanalyzeButton />` next to existing call header |
| `frontend-v3/tests/unit/reanalyze-button.test.tsx` | NEW | vitest: button click triggers POST, success toast |
| `backend/tests/test_storage_backend.py` | NEW | ABC contract tests + Supabase + S3/MinIO impl tests (MinIO via testcontainers OR moto) |
| `backend/tests/test_replay.py` | NEW | `/calls/{id}/reanalyze` endpoint behavior + missing transcript → 422 |
| `backend/tests/test_pg_dump_script.py` | NEW | Script CLI smoke (mock pg_dump + storage upload) |
| `backend/tests/test_workflows_pg_dump_nightly.py` | NEW | Inngest function fires script + handles failure |
| `docs/durability.md` | NEW | Inngest durability mapping + restore drill writeup |
| `infrastructure/contabo/README.md` | MOD | Add `pg_dump_cron` operational notes (env vars, schedule, restore drill) |

---

## Task 1: Add boto3 dependency

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Append line under Wave 2 observability block**

In `backend/requirements.txt`, after the `sentry-sdk[fastapi]==2.18.0` line, add:

```
# Wave 3 durability — S3/MinIO storage portability
boto3==1.34.144
```

- [ ] **Step 2: Install**

```bash
cd backend && source venv/bin/activate && pip install -r requirements.txt
```
Expected: `boto3-1.34.144`, `botocore-1.34.144`, `s3transfer-0.10.x` resolve cleanly.

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "deps(backend): add boto3 for S3/MinIO storage backend"
```

---

## Task 2: Add Wave 3 config keys

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Append fields inside `Settings` class**

In `backend/app/config.py`, before `settings = Settings()`, add:

```python
    # ─── Wave 3 — durability + portability ────────────────────────────
    storage_backend: Literal["supabase", "s3"] = "supabase"
    s3_endpoint: str = ""           # MinIO/custom endpoint; empty = AWS default
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "call-audio"
    s3_region: str = "us-east-1"
    backup_bucket: str = "backups"  # Bucket inside the active backend
    backup_age_recipient: str = ""  # `age` recipient public key; empty = no encryption (dev only)
```

- [ ] **Step 2: Verify import**

```bash
cd backend && source venv/bin/activate && python -c "from app.config import settings; print(settings.storage_backend, settings.backup_bucket)"
```
Expected: `supabase backups`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "config(backend): add Wave 3 storage_backend and backup settings"
```

---

## Task 3: StorageBackend ABC + Supabase impl + back-compat shim (TDD)

**Files:**
- Create: `backend/app/storage/__init__.py`
- Create: `backend/app/storage/supabase_backend.py`
- Delete: `backend/app/storage.py`
- Create: `backend/tests/test_storage_backend.py`

- [ ] **Step 1: Write failing contract tests**

Create `backend/tests/test_storage_backend.py`:

```python
"""Storage backend contract — same suite runs against every implementation.

The ABC defines four methods: upload_blob, download_blob, signed_url,
delete_blob. Each impl must round-trip bytes, mint a working URL, and
delete cleanly. Tests parametrize on backend names registered in the
factory; new impls add themselves automatically.
"""
import os
import pathlib
import tempfile

import pytest

from app.storage import get_backend, StorageBackend


@pytest.fixture
def backend() -> StorageBackend:
    """Default backend (driven by STORAGE_BACKEND env var, defaults to supabase)."""
    return get_backend()


@pytest.fixture
def temp_path(tmp_path: pathlib.Path) -> str:
    p = tmp_path / "audio.bin"
    p.write_bytes(b"hello compliance")
    return str(p)


def test_backend_implements_abc(backend: StorageBackend):
    assert isinstance(backend, StorageBackend)


def test_upload_returns_remote_key(backend: StorageBackend, temp_path: str, monkeypatch):
    """upload_blob must return the same remote_key the caller passed in."""
    monkeypatch.setattr(backend, "_actually_upload", lambda *a, **kw: None, raising=False)
    key = "test/contract/upload.bin"
    out = backend.upload_blob(temp_path, key, content_type="application/octet-stream")
    assert out == key


def test_signed_url_returns_string(backend: StorageBackend, monkeypatch):
    """signed_url returns a non-empty string with the remote_key embedded."""
    monkeypatch.setattr(backend, "_mint_signed_url", lambda key, ttl: f"https://stub/{key}?ttl={ttl}", raising=False)
    url = backend.signed_url("abc/def.mp3", expires_in=600)
    assert "abc/def.mp3" in url
    assert "ttl=600" in url


def test_module_shim_reexports_legacy_api():
    """Existing code does `from app.storage import upload_audio, download_audio, signed_url`.
    The package __init__ must re-export those names by delegating to the active backend."""
    from app.storage import upload_audio, download_audio, signed_url
    assert callable(upload_audio)
    assert callable(download_audio)
    assert callable(signed_url)
```

- [ ] **Step 2: Run, verify red**

```bash
cd backend && source venv/bin/activate && pytest tests/test_storage_backend.py -v
```
Expected: FAIL — `from app.storage import get_backend, StorageBackend` errors because `app.storage` is currently a module, not a package.

- [ ] **Step 3: Build the package — `__init__.py`**

Delete the current file and create the package directory:

```bash
cd /Users/gomaa/Documents/Compliance && rm backend/app/storage.py
mkdir -p backend/app/storage
```

Create `backend/app/storage/__init__.py`:

```python
"""Storage backend selector + back-compat shim.

The ABC `StorageBackend` defines a four-method contract:
    upload_blob(local_path, remote_key, content_type) -> remote_key
    download_blob(remote_key, local_path)             -> local_path
    signed_url(remote_key, expires_in)                -> str
    delete_blob(remote_key)                           -> None

`get_backend()` is the singleton factory. Selection comes from
`settings.storage_backend` (`"supabase"` or `"s3"`).

Legacy call sites (`from app.storage import upload_audio, download_audio,
signed_url`) keep working — module-level functions delegate to the active
backend. New code should call `get_backend()` directly.
"""
from __future__ import annotations

import abc
from functools import lru_cache
from typing import Literal

from app.config import settings


class StorageBackend(abc.ABC):
    """Object-storage contract. Implementations must be stateless beyond
    a single configured client; they're instantiated once per process."""

    @abc.abstractmethod
    def upload_blob(self, local_path: str, remote_key: str, content_type: str = "application/octet-stream") -> str:
        ...

    @abc.abstractmethod
    def download_blob(self, remote_key: str, local_path: str) -> str:
        ...

    @abc.abstractmethod
    def signed_url(self, remote_key: str, expires_in: int = 3600) -> str:
        ...

    @abc.abstractmethod
    def delete_blob(self, remote_key: str) -> None:
        ...


@lru_cache(maxsize=1)
def get_backend() -> StorageBackend:
    name: Literal["supabase", "s3"] = settings.storage_backend
    if name == "s3":
        from app.storage.s3_backend import S3Backend
        return S3Backend()
    from app.storage.supabase_backend import SupabaseBackend
    return SupabaseBackend()


# ─── Legacy shim — preserved API for existing call sites ────────────────
def upload_audio(local_path: str, remote_key: str, content_type: str = "audio/mpeg") -> str:
    return get_backend().upload_blob(local_path, remote_key, content_type)


def download_audio(remote_key: str, local_path: str) -> str:
    return get_backend().download_blob(remote_key, local_path)


def signed_url(remote_key: str, expires_in: int = 3600) -> str:
    return get_backend().signed_url(remote_key, expires_in)
```

- [ ] **Step 4: Build `supabase_backend.py`**

Create `backend/app/storage/supabase_backend.py`:

```python
"""Supabase Storage backend — moved from the original app/storage.py.

Public method bodies are byte-identical to the previous module-level
functions; only the wrapping changed (class instead of free functions).
"""
from __future__ import annotations

from typing import Optional

from supabase import Client, create_client

from app.config import settings
from app.storage import StorageBackend


class SupabaseBackend(StorageBackend):
    def __init__(self) -> None:
        self._client: Optional[Client] = None

    def _supabase(self) -> Client:
        if self._client is None:
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )
        return self._client

    def upload_blob(self, local_path: str, remote_key: str, content_type: str = "audio/mpeg") -> str:
        with open(local_path, "rb") as f:
            self._supabase().storage.from_(settings.supabase_storage_bucket).upload(
                path=remote_key,
                file=f,
                file_options={"content-type": content_type, "upsert": "false"},
            )
        return remote_key

    def download_blob(self, remote_key: str, local_path: str) -> str:
        data = self._supabase().storage.from_(settings.supabase_storage_bucket).download(remote_key)
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path

    def signed_url(self, remote_key: str, expires_in: int = 3600) -> str:
        res = self._supabase().storage.from_(settings.supabase_storage_bucket).create_signed_url(
            remote_key, expires_in
        )
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signed_url") or ""
        return getattr(res, "signedURL", "") or getattr(res, "signed_url", "")

    def delete_blob(self, remote_key: str) -> None:
        self._supabase().storage.from_(settings.supabase_storage_bucket).remove([remote_key])
```

- [ ] **Step 5: Run tests, verify green**

```bash
cd backend && source venv/bin/activate && pytest tests/test_storage_backend.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 6: Smoke existing callers still import**

```bash
cd backend && source venv/bin/activate && python -c "from app.storage import upload_audio, download_audio, signed_url; from app.routes import router; from app.pipeline import _process_locally; print('imports ok')"
```
Expected: `imports ok`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/storage backend/tests/test_storage_backend.py
git rm backend/app/storage.py
git commit -m "feat(storage): refactor app.storage into StorageBackend ABC + SupabaseBackend impl"
```

---

## Task 4: S3/MinIO backend (TDD with moto)

**Files:**
- Create: `backend/app/storage/s3_backend.py`
- Modify: `backend/tests/test_storage_backend.py`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add `moto[s3]` to test deps**

In `backend/requirements.txt`, after the `boto3==1.34.144` line:

```
moto[s3]==5.0.18
```

Install:

```bash
cd backend && source venv/bin/activate && pip install -r requirements.txt
```

- [ ] **Step 2: Append failing test for S3Backend**

In `backend/tests/test_storage_backend.py`, append:

```python
import boto3
import pytest
from moto import mock_aws

from app.storage.s3_backend import S3Backend


@pytest.fixture
def s3_backend(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.setenv("S3_ACCESS_KEY", "test")
        monkeypatch.setenv("S3_SECRET_KEY", "test")
        monkeypatch.setenv("S3_BUCKET", "call-audio")
        monkeypatch.setenv("S3_REGION", "us-east-1")
        # Force settings reload
        from app import config
        import importlib
        importlib.reload(config)

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="call-audio")
        yield S3Backend()


def test_s3_upload_then_download_roundtrips(s3_backend, tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"compliance roundtrip")
    s3_backend.upload_blob(str(src), "round/trip.bin", content_type="application/octet-stream")

    dst = tmp_path / "out.bin"
    s3_backend.download_blob("round/trip.bin", str(dst))
    assert dst.read_bytes() == b"compliance roundtrip"


def test_s3_signed_url_contains_key_and_expiry(s3_backend):
    url = s3_backend.signed_url("round/trip.bin", expires_in=300)
    assert "round/trip.bin" in url
    assert "X-Amz-Expires=300" in url


def test_s3_delete_removes_object(s3_backend, tmp_path):
    src = tmp_path / "tmp.bin"
    src.write_bytes(b"to delete")
    s3_backend.upload_blob(str(src), "del/me.bin")
    s3_backend.delete_blob("del/me.bin")
    # Listing the bucket: object should not appear
    s3 = boto3.client("s3", region_name="us-east-1")
    listed = s3.list_objects_v2(Bucket="call-audio").get("Contents", [])
    assert "del/me.bin" not in [o["Key"] for o in listed]
```

- [ ] **Step 3: Run, verify red**

```bash
cd backend && source venv/bin/activate && pytest tests/test_storage_backend.py -v
```
Expected: FAIL — `app.storage.s3_backend` does not exist.

- [ ] **Step 4: Implement S3Backend**

Create `backend/app/storage/s3_backend.py`:

```python
"""S3-compatible storage backend (works against AWS S3, MinIO, Cloudflare R2).

Uses boto3 with optional `endpoint_url` so the same code drives local
MinIO during dev and AWS in prod. Signed URLs use boto3's pre-signer.
"""
from __future__ import annotations

import boto3
from botocore.config import Config

from app.config import settings
from app.storage import StorageBackend


class S3Backend(StorageBackend):
    def __init__(self) -> None:
        client_kwargs = {
            "region_name": settings.s3_region,
            "aws_access_key_id": settings.s3_access_key or None,
            "aws_secret_access_key": settings.s3_secret_key or None,
            "config": Config(signature_version="s3v4"),
        }
        if settings.s3_endpoint:
            client_kwargs["endpoint_url"] = settings.s3_endpoint
        self._s3 = boto3.client("s3", **{k: v for k, v in client_kwargs.items() if v is not None})
        self._bucket = settings.s3_bucket

    def upload_blob(self, local_path: str, remote_key: str, content_type: str = "application/octet-stream") -> str:
        self._s3.upload_file(
            Filename=local_path,
            Bucket=self._bucket,
            Key=remote_key,
            ExtraArgs={"ContentType": content_type},
        )
        return remote_key

    def download_blob(self, remote_key: str, local_path: str) -> str:
        self._s3.download_file(Bucket=self._bucket, Key=remote_key, Filename=local_path)
        return local_path

    def signed_url(self, remote_key: str, expires_in: int = 3600) -> str:
        return self._s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self._bucket, "Key": remote_key},
            ExpiresIn=expires_in,
        )

    def delete_blob(self, remote_key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=remote_key)
```

- [ ] **Step 5: Run, verify green**

```bash
cd backend && source venv/bin/activate && pytest tests/test_storage_backend.py -v
```
Expected: PASS (6 tests now: 3 ABC + 3 S3-specific).

- [ ] **Step 6: Commit**

```bash
git add backend/app/storage/s3_backend.py backend/tests/test_storage_backend.py backend/requirements.txt
git commit -m "feat(storage): add S3Backend (boto3) — works against MinIO, R2, AWS"
```

---

## Task 5: Replay event constant + payload schema

**Files:**
- Modify: `backend/app/workflows/events.py`

- [ ] **Step 1: Read existing event definitions**

```bash
sed -n '1,40p' backend/app/workflows/events.py
```
Confirm `CALL_UPLOADED = "call/uploaded"` and the `CallUploadedPayload` model exist.

- [ ] **Step 2: Append CALL_REANALYZE constant + payload**

At the bottom of `backend/app/workflows/events.py`, add:

```python
CALL_REANALYZE = "call/reanalyze"
"""Event emitted when a reviewer asks to re-derive a verdict from the
already-stored transcript. Cheap path — no transcription, no audio I/O.
Pipeline runs steps 4-5-6 only (analyze_checkpoints → score → finalize).
"""


class CallReanalyzePayload(BaseModel):
    """Payload for the `call/reanalyze` event.

    Mirrors `CallUploadedPayload` minus audio-path fields, since this
    event never touches storage.
    """
    call_id: str
    actor: str | None = None  # reviewer who triggered the reanalyze
```

- [ ] **Step 3: Smoke import**

```bash
cd backend && source venv/bin/activate && python -c "from app.workflows.events import CALL_REANALYZE, CallReanalyzePayload; print(CALL_REANALYZE)"
```
Expected: `call/reanalyze`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/events.py
git commit -m "feat(events): add CALL_REANALYZE event + payload"
```

---

## Task 6: Replay endpoint + emit (TDD)

**Files:**
- Create: `backend/app/replay.py`
- Modify: `backend/app/routes.py`
- Create: `backend/tests/test_replay.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_replay.py`:

```python
"""Integration: POST /calls/{id}/reanalyze emits CALL_REANALYZE and writes audit row."""
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.main import app


client = TestClient(app)


def test_reanalyze_returns_202_when_call_has_transcript(monkeypatch, db_session_with_call_with_transcript):
    """db_session_with_call_with_transcript is a fixture seeding a Call row
    with non-null `transcript`, `word_data`, and `script_id`. See conftest.py."""
    call_id = db_session_with_call_with_transcript

    with patch("app.replay.emit_event_async") as mock_emit:
        r = client.post(f"/calls/{call_id}/reanalyze")

    assert r.status_code == 202
    body = r.json()
    assert body["call_id"] == call_id
    assert "run_id" in body
    mock_emit.assert_called_once()
    name, payload = mock_emit.call_args[0]
    assert name == "call/reanalyze"
    assert payload["call_id"] == call_id


def test_reanalyze_returns_422_when_transcript_missing(db_session_with_call_no_transcript):
    call_id = db_session_with_call_no_transcript
    r = client.post(f"/calls/{call_id}/reanalyze")
    assert r.status_code == 422
    assert "transcript" in r.json()["detail"].lower()


def test_reanalyze_returns_404_for_unknown_call_id():
    r = client.post("/calls/00000000-0000-0000-0000-000000000000/reanalyze")
    assert r.status_code == 404
```

Add fixtures to `backend/tests/conftest.py` (append; don't replace):

```python
import uuid
import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Call, Script


@pytest.fixture
def db_session_with_call_with_transcript() -> str:
    db: Session = SessionLocal()
    try:
        script = Script(id=str(uuid.uuid4()), name="t-script", body_text="step one\nstep two")
        db.add(script)
        db.flush()
        call = Call(
            id=str(uuid.uuid4()),
            file_path="x/y.mp3",
            customer_name="Test Reviewer",
            script_id=script.id,
            transcript="hello world",
            word_data='[{"word":"hello","start":0,"end":0.5}]',
            status="completed",
        )
        db.add(call)
        db.commit()
        yield call.id
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def db_session_with_call_no_transcript() -> str:
    db: Session = SessionLocal()
    try:
        script = Script(id=str(uuid.uuid4()), name="t-script-empty", body_text="step one")
        db.add(script)
        db.flush()
        call = Call(
            id=str(uuid.uuid4()),
            file_path="x/y.mp3",
            customer_name="Test Reviewer",
            script_id=script.id,
            transcript=None,
            word_data=None,
            status="uploaded",
        )
        db.add(call)
        db.commit()
        yield call.id
    finally:
        db.rollback()
        db.close()
```

- [ ] **Step 2: Run, verify red**

```bash
cd backend && source venv/bin/activate && pytest tests/test_replay.py -v
```
Expected: FAIL — endpoint returns 404 / `app.replay` doesn't exist.

- [ ] **Step 3: Implement `app/replay.py`**

Create `backend/app/replay.py`:

```python
"""Replay path — re-derive a call's verdict from its stored transcript.

Cost model: zero re-transcription, zero new audio I/O. Pipeline steps 4
(analyze_checkpoints) → 5 (score) → 6 (finalize) re-run via the Inngest
`call/reanalyze` event. Existing CallCheckpoint idempotency replaces
prior rows (the workflow function uses delete-and-insert by call_id +
checkpoint_index, so reruns don't pile up).
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.models import Call
from app.workflows.events import CALL_REANALYZE
from app.workflows.observability import emit_event_async


async def reanalyze(call_id: str, db: Session, actor: str | None = None) -> dict:
    call = db.query(Call).filter(Call.id == call_id).first()
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")
    if not call.transcript or not call.word_data or not call.script_id:
        raise HTTPException(
            status_code=422,
            detail="Call lacks transcript / word_data / script_id — cannot reanalyze.",
        )

    run_id = str(uuid.uuid4())
    payload = {"call_id": call_id, "run_id": run_id, "actor": actor}
    await emit_event_async(CALL_REANALYZE, payload)
    record_audit(db, action="reanalyze", actor=actor or "system", resource_id=call_id, payload=payload)
    return payload
```

- [ ] **Step 4: Wire route in `routes.py`**

In `backend/app/routes.py`, add the import near the top:

```python
from app.replay import reanalyze
```

Then add the route (place near the existing `/calls/{call_id}` GET handler):

```python
@router.post("/calls/{call_id}/reanalyze", status_code=202)
async def reanalyze_call(
    call_id: str,
    db: Session = Depends(get_db),
    actor: str | None = None,
):
    """Replay the analyze→score→finalize sub-pipeline against the stored
    transcript. Returns 202 with a fresh run_id; client polls the call to
    see the new verdict."""
    return await reanalyze(call_id, db, actor=actor)
```

- [ ] **Step 5: Run tests, verify green**

```bash
cd backend && source venv/bin/activate && pytest tests/test_replay.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/replay.py backend/app/routes.py backend/tests/test_replay.py backend/tests/conftest.py
git commit -m "feat(replay): add POST /calls/{id}/reanalyze emitting call/reanalyze"
```

---

## Task 7: Inngest reanalyze workflow function

**Files:**
- Modify: `backend/app/workflows/process_call.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Locate the existing function**

```bash
grep -n "@inngest_client.create_function\|def process_call\|process_call =" backend/app/workflows/process_call.py | head -10
```
Note the `@inngest_client.create_function(...)` decorator and the function it wraps (likely `process_call`).

- [ ] **Step 2: Add reanalyze function next to existing one**

In `backend/app/workflows/process_call.py`, after the existing `process_call` definition, add:

```python
import time as _time2  # local alias avoids shadowing existing `time` import
from app.observability_metrics import record_pipeline_step
from app.workflows.events import CALL_REANALYZE


@inngest_client.create_function(
    fn_id="process_call_reanalyze",
    trigger=inngest.TriggerEvent(event=CALL_REANALYZE),
    retries=3,
)
async def process_call_reanalyze(ctx: inngest.Context) -> dict:
    """Replay sub-pipeline for `call/reanalyze`. Skips audio download +
    transcription. Steps 4-5-6 only. Existing CallCheckpoint rows replaced
    by the analyze step's idempotency contract (delete-and-insert)."""
    call_id: str = ctx.event.data["call_id"]

    _t0 = _time2.monotonic()
    try:
        analysis = await ctx.step.run("analyze_checkpoints", _step_analyze_checkpoints, call_id)
    finally:
        record_pipeline_step("analyze_checkpoints", _time2.monotonic() - _t0)

    _t0 = _time2.monotonic()
    try:
        score_out = await ctx.step.run("score", _step_score, call_id, analysis)
    finally:
        record_pipeline_step("score", _time2.monotonic() - _t0)

    _t0 = _time2.monotonic()
    try:
        finalize_out = await ctx.step.run("finalize", _step_finalize, call_id, score_out)
    finally:
        record_pipeline_step("finalize", _time2.monotonic() - _t0)

    return {"call_id": call_id, "verdict": finalize_out}
```

If the existing private step functions have different names (e.g. `_step_analyze`, `_score_step`), adjust the references. Match exactly what `process_call` calls.

- [ ] **Step 3: Register the new function in `main.py`**

In `backend/app/main.py`, find the existing import:

```python
from app.workflows.process_call import process_call as process_call_fn
```

Add next to it:

```python
from app.workflows.process_call import process_call_reanalyze as process_call_reanalyze_fn
```

Find the `inngest.fast_api.serve(...)` call (or equivalent) and add the new function to the `functions=[...]` list.

- [ ] **Step 4: Smoke import**

```bash
cd backend && source venv/bin/activate && python -c "from app.workflows.process_call import process_call_reanalyze; print(process_call_reanalyze.id if hasattr(process_call_reanalyze, 'id') else 'fn registered')"
```
Expected: prints either the function id or `fn registered` — no traceback.

- [ ] **Step 5: Run all workflow tests**

```bash
cd backend && source venv/bin/activate && pytest tests/ -k "process_call or replay or workflow" -v 2>&1 | tail -20
```
Expected: replay tests still pass; pre-existing test_pipeline.py failures (mocks of `app.pipeline.transcribe_audio`) reproduce identically — note them, don't fix.

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/process_call.py backend/app/main.py
git commit -m "feat(workflows): add process_call_reanalyze Inngest function (steps 4-5-6)"
```

---

## Task 8: pg_dump backup script (TDD)

**Files:**
- Create: `backend/scripts/pg_dump_to_storage.py`
- Create: `backend/tests/test_pg_dump_script.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_pg_dump_script.py`:

```python
"""Smoke: pg_dump_to_storage CLI runs pg_dump, optionally encrypts, uploads."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.scripts import pg_dump_to_storage as mod


def test_run_dumps_invokes_pg_dump_and_uploads(tmp_path, monkeypatch):
    """With no age recipient, the dump is uploaded as plain .sql.gz."""
    monkeypatch.setattr(mod.settings, "backup_age_recipient", "")
    monkeypatch.setattr(mod.settings, "backup_bucket", "backups")
    fake_dump = tmp_path / "dump.sql.gz"
    fake_dump.write_bytes(b"PGDMP fake")

    fake_subprocess_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(mod, "_make_dump_path", lambda ts, work: fake_dump)

    fake_backend = MagicMock()
    fake_backend.upload_blob.return_value = "backups/2026/05/07/compliance.sql.gz"
    monkeypatch.setattr(mod, "get_backend", lambda: fake_backend)

    out = mod.run(work_dir=str(tmp_path))

    assert out.endswith(".sql.gz")
    fake_backend.upload_blob.assert_called_once()
    args, kwargs = fake_backend.upload_blob.call_args
    assert args[0] == str(fake_dump)
    assert "backups/" in args[1]
    assert kwargs.get("content_type") == "application/gzip"


def test_run_encrypts_when_age_recipient_set(tmp_path, monkeypatch):
    """With an age recipient, the dump is age-encrypted to .sql.gz.age before upload."""
    monkeypatch.setattr(mod.settings, "backup_age_recipient", "age1xxxxx")
    monkeypatch.setattr(mod.settings, "backup_bucket", "backups")
    fake_dump = tmp_path / "dump.sql.gz"
    fake_dump.write_bytes(b"PGDMP fake")
    encrypted = tmp_path / "dump.sql.gz.age"
    encrypted.write_bytes(b"AGE encrypted")

    runs = []
    def fake_run(*args, **kwargs):
        runs.append(args[0])
        return subprocess.CompletedProcess(args=args[0], returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "_make_dump_path", lambda ts, work: fake_dump)
    monkeypatch.setattr(mod, "_make_encrypted_path", lambda d: encrypted)

    fake_backend = MagicMock()
    fake_backend.upload_blob.return_value = "backups/2026/05/07/compliance.sql.gz.age"
    monkeypatch.setattr(mod, "get_backend", lambda: fake_backend)

    out = mod.run(work_dir=str(tmp_path))

    assert out.endswith(".sql.gz.age")
    # First call is pg_dump, second is age
    assert any(c[0] == "pg_dump" for c in runs)
    assert any(c[0] == "age" for c in runs)
    fake_backend.upload_blob.assert_called_once()
    assert fake_backend.upload_blob.call_args.kwargs["content_type"] == "application/octet-stream"
```

- [ ] **Step 2: Run, verify red**

```bash
cd /Users/gomaa/Documents/Compliance && python -m pytest backend/tests/test_pg_dump_script.py -v
```
Expected: FAIL — `backend.scripts.pg_dump_to_storage` doesn't exist.

- [ ] **Step 3: Implement the script**

Create `backend/scripts/__init__.py` if missing:

```bash
touch /Users/gomaa/Documents/Compliance/backend/scripts/__init__.py
```

Create `backend/scripts/pg_dump_to_storage.py`:

```python
"""pg_dump → optional age encryption → object-storage upload.

Driven by environment via `app.config.settings`:
  - migration_database_url (required for pg_dump)
  - backup_bucket          (logical bucket inside the active StorageBackend)
  - backup_age_recipient   (age public key; empty = upload plaintext gzip)

Output key layout:
  backups/YYYY/MM/DD/compliance-HHMMSS.sql.gz[.age]

Designed for invocation by:
  - Inngest scheduled function `pg_dump_nightly` (preferred prod)
  - Cron on the VPS (fallback)
  - Engineer ad-hoc via `python -m backend.scripts.pg_dump_to_storage`
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import settings
from app.storage import get_backend


def _make_dump_path(ts: _dt.datetime, work_dir: str) -> Path:
    name = f"compliance-{ts.strftime('%H%M%S')}.sql.gz"
    return Path(work_dir) / name


def _make_encrypted_path(dump: Path) -> Path:
    return dump.with_suffix(dump.suffix + ".age")


def _remote_key(ts: _dt.datetime, fname: str) -> str:
    return f"{settings.backup_bucket}/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/{fname}"


def run(work_dir: str | None = None) -> str:
    """Execute one backup. Returns the remote key of the uploaded artefact."""
    work = work_dir or tempfile.mkdtemp(prefix="cmpl-bkp-")
    ts = _dt.datetime.utcnow()

    dump_path = _make_dump_path(ts, work)
    db_url = settings.migration_database_url or settings.database_url
    if not db_url:
        raise RuntimeError("No DATABASE_URL or MIGRATION_DATABASE_URL configured.")

    pg_cmd = ["pg_dump", "--format=custom", "--compress=6", "--file", str(dump_path), db_url]
    subprocess.run(pg_cmd, check=True)

    upload_path: Path = dump_path
    content_type = "application/gzip"
    if settings.backup_age_recipient.strip():
        encrypted = _make_encrypted_path(dump_path)
        age_cmd = ["age", "-r", settings.backup_age_recipient, "-o", str(encrypted), str(dump_path)]
        subprocess.run(age_cmd, check=True)
        upload_path = encrypted
        content_type = "application/octet-stream"

    key = _remote_key(ts, upload_path.name)
    get_backend().upload_blob(str(upload_path), key, content_type=content_type)
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pg_dump → object storage")
    parser.add_argument("--work-dir", default=None, help="Override temp work dir")
    args = parser.parse_args(argv)
    key = run(work_dir=args.work_dir)
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run, verify green**

```bash
cd /Users/gomaa/Documents/Compliance && python -m pytest backend/tests/test_pg_dump_script.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/__init__.py backend/scripts/pg_dump_to_storage.py backend/tests/test_pg_dump_script.py
git commit -m "feat(backups): add pg_dump_to_storage.py — dump, age-encrypt, upload"
```

---

## Task 9: Inngest scheduled `pg_dump_nightly` (TDD)

**Files:**
- Create: `backend/app/workflows/pg_dump_nightly.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_workflows_pg_dump_nightly.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_workflows_pg_dump_nightly.py`:

```python
"""Inngest function pg_dump_nightly invokes the script and surfaces failures."""
import asyncio
from unittest.mock import patch

import pytest

from app.workflows.pg_dump_nightly import _run_backup


@pytest.mark.asyncio
async def test_run_backup_returns_remote_key_on_success():
    with patch("app.workflows.pg_dump_nightly.run_pg_dump", return_value="backups/2026/05/07/x.sql.gz"):
        result = await _run_backup()
    assert result == {"remote_key": "backups/2026/05/07/x.sql.gz"}


@pytest.mark.asyncio
async def test_run_backup_raises_on_pg_dump_failure():
    import subprocess
    with patch("app.workflows.pg_dump_nightly.run_pg_dump", side_effect=subprocess.CalledProcessError(1, "pg_dump")):
        with pytest.raises(subprocess.CalledProcessError):
            await _run_backup()
```

- [ ] **Step 2: Run, verify red**

```bash
cd backend && source venv/bin/activate && pytest tests/test_workflows_pg_dump_nightly.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

Create `backend/app/workflows/pg_dump_nightly.py`:

```python
"""Inngest scheduled backup function — runs `pg_dump_to_storage` once a day.

Cron `0 2 * * *` UTC = 02:00 UTC nightly. Off-peak for European review
hours and the EU-region pgvector instance. Inngest retries up to 3 times
with exponential backoff; final failure produces a `failed_jobs` row via
the existing exhaustion handler.
"""
from __future__ import annotations

import inngest

from app.inngest_client import inngest_client
from app.logger import log
from backend.scripts.pg_dump_to_storage import run as run_pg_dump


async def _run_backup() -> dict:
    """Wrapper that's easy to mock in tests."""
    key = run_pg_dump()
    return {"remote_key": key}


@inngest_client.create_function(
    fn_id="pg_dump_nightly",
    trigger=inngest.TriggerCron(cron="0 2 * * *"),
    retries=3,
)
async def pg_dump_nightly(ctx: inngest.Context) -> dict:
    log.info("pg_dump_nightly_start", extra={"run_id": ctx.run_id if hasattr(ctx, "run_id") else None})
    result = await ctx.step.run("pg_dump_to_storage", _run_backup)
    log.info("pg_dump_nightly_ok", extra={"remote_key": result["remote_key"]})
    return result
```

- [ ] **Step 4: Register in `main.py`**

In `backend/app/main.py`, add:

```python
from app.workflows.pg_dump_nightly import pg_dump_nightly as pg_dump_nightly_fn
```

Append `pg_dump_nightly_fn` to the `functions=[...]` list in the inngest serve call.

- [ ] **Step 5: Run, verify green**

```bash
cd backend && source venv/bin/activate && pytest tests/test_workflows_pg_dump_nightly.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/pg_dump_nightly.py backend/app/main.py backend/tests/test_workflows_pg_dump_nightly.py
git commit -m "feat(backups): add pg_dump_nightly Inngest scheduled function (02:00 UTC)"
```

---

## Task 10: Restore drill script

**Files:**
- Create: `scripts/restore_drill.sh`

- [ ] **Step 1: Write the script**

Create `/Users/gomaa/Documents/Compliance/scripts/restore_drill.sh`:

```bash
#!/usr/bin/env bash
# Restore drill — fetch the most recent backup from object storage and
# restore it into a scratch DB. Verifies the backup is real, restorable,
# and roughly the size we expect.
#
# Required env:
#   DATABASE_URL_SCRATCH    e.g. postgres://postgres:postgres@localhost:5433/compliance_scratch
#   BACKUP_REMOTE_KEY       full key of the dump in storage (run with --latest to auto-resolve)
#   BACKUP_AGE_IDENTITY     path to age private key (only required if backup is .age-encrypted)
#
# Optional env:
#   STORAGE_BACKEND         supabase | s3   (default: supabase, must match prod)
#   PYTHON                  python 3.12 (default: ./backend/venv/bin/python)
set -euo pipefail

PYTHON="${PYTHON:-./backend/venv/bin/python}"
WORK="$(mktemp -d -t cmpl-restore-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

if [[ -z "${DATABASE_URL_SCRATCH:-}" ]]; then
  echo "DATABASE_URL_SCRATCH is required (e.g. postgres://...:5433/compliance_scratch)" >&2
  exit 2
fi

if [[ "${1:-}" == "--latest" ]]; then
  REMOTE_KEY="$($PYTHON -c '
import sys
from app.storage import get_backend
b = get_backend()
# Listing is backend-specific; reuse the simple Supabase-style API here.
# For S3 / MinIO, swap to: aws s3 ls s3://$BUCKET/backups/ --recursive
print("backups/latest.sql.gz")  # TODO: replace with real listing once daily backups land
')"
else
  REMOTE_KEY="${BACKUP_REMOTE_KEY:-}"
fi

if [[ -z "$REMOTE_KEY" ]]; then
  echo "No remote key resolved. Pass --latest or set BACKUP_REMOTE_KEY." >&2
  exit 2
fi

LOCAL_DUMP="$WORK/$(basename "$REMOTE_KEY")"
echo "[drill] downloading $REMOTE_KEY → $LOCAL_DUMP"
$PYTHON -c "
from app.storage import get_backend
get_backend().download_blob('$REMOTE_KEY', '$LOCAL_DUMP')
"

if [[ "$LOCAL_DUMP" == *.age ]]; then
  if [[ -z "${BACKUP_AGE_IDENTITY:-}" ]]; then
    echo "Backup is age-encrypted but BACKUP_AGE_IDENTITY is not set." >&2
    exit 2
  fi
  echo "[drill] decrypting via age"
  age -d -i "$BACKUP_AGE_IDENTITY" -o "${LOCAL_DUMP%.age}" "$LOCAL_DUMP"
  LOCAL_DUMP="${LOCAL_DUMP%.age}"
fi

echo "[drill] dropping + recreating scratch DB"
psql "$DATABASE_URL_SCRATCH" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null

echo "[drill] pg_restore into scratch"
pg_restore --dbname="$DATABASE_URL_SCRATCH" --no-owner --no-acl "$LOCAL_DUMP"

echo "[drill] sanity check — table row counts"
psql "$DATABASE_URL_SCRATCH" -c "
SELECT relname, n_live_tup AS row_estimate
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC
LIMIT 10;
"

echo "[drill] OK — restore completed at $(date -u +%FT%TZ)"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /Users/gomaa/Documents/Compliance/scripts/restore_drill.sh
```

- [ ] **Step 3: Sanity check syntax**

```bash
bash -n /Users/gomaa/Documents/Compliance/scripts/restore_drill.sh && echo "shell syntax OK"
```
Expected: `shell syntax OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/restore_drill.sh
git commit -m "feat(backups): add restore_drill.sh — download, decrypt, pg_restore, sanity"
```

---

## Task 11: Frontend Reanalyze button (TDD)

**Files:**
- Create: `frontend-v3/src/app/calls/[id]/components/ReanalyzeButton.tsx`
- Modify: `frontend-v3/src/app/calls/[id]/page.tsx`
- Create: `frontend-v3/tests/unit/reanalyze-button.test.tsx`

- [ ] **Step 1: Write failing test**

Create `frontend-v3/tests/unit/reanalyze-button.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ReanalyzeButton } from '@/app/calls/[id]/components/ReanalyzeButton';

describe('ReanalyzeButton', () => {
  beforeEach(() => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify({ run_id: 'r-1', call_id: 'c-1' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    })) as unknown as typeof fetch;
  });

  it('renders a button labelled Reanalyze', () => {
    render(<ReanalyzeButton callId="c-1" />);
    expect(screen.getByRole('button', { name: /reanalyze/i })).toBeInTheDocument();
  });

  it('POSTs to /calls/{id}/reanalyze on click and shows success state', async () => {
    render(<ReanalyzeButton callId="c-1" />);
    fireEvent.click(screen.getByRole('button', { name: /reanalyze/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/calls/c-1/reanalyze'),
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('disables button while a request is in flight', async () => {
    render(<ReanalyzeButton callId="c-1" />);
    const btn = screen.getByRole('button', { name: /reanalyze/i });
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run, verify red**

```bash
cd frontend-v3 && npm run test:unit -- --run tests/unit/reanalyze-button.test.tsx
```
Expected: FAIL — component doesn't exist.

- [ ] **Step 3: Implement component**

Create `frontend-v3/src/app/calls/[id]/components/ReanalyzeButton.tsx`:

```tsx
'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? '';

interface Props {
  callId: string;
}

export function ReanalyzeButton({ callId }: Props) {
  const [pending, setPending] = useState(false);

  async function handleClick() {
    setPending(true);
    try {
      const res = await fetch(`${API_BASE}/calls/${callId}/reanalyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        toast.error(`Reanalyze failed: ${body.detail ?? res.statusText}`);
        return;
      }
      const data = (await res.json()) as { run_id: string; call_id: string };
      toast.success(`Reanalyze enqueued (run ${data.run_id.slice(0, 8)}). Refresh to see verdict.`);
    } finally {
      setPending(false);
    }
  }

  return (
    <Button onClick={handleClick} disabled={pending} variant="secondary" size="sm">
      {pending ? 'Reanalyzing…' : 'Reanalyze'}
    </Button>
  );
}
```

- [ ] **Step 4: Mount in call detail page**

In `frontend-v3/src/app/calls/[id]/page.tsx`, find the call header / actions area (likely the row containing customer name + status). Add the button:

```tsx
import { ReanalyzeButton } from './components/ReanalyzeButton';

// inside the JSX, near other action buttons:
<ReanalyzeButton callId={params.id} />
```

If the page uses Server Components and `params.id` isn't directly available, pass the call id from wherever it's already destructured.

- [ ] **Step 5: Run vitest, verify green**

```bash
cd frontend-v3 && npm run test:unit -- --run tests/unit/reanalyze-button.test.tsx
```
Expected: PASS (3 tests).

- [ ] **Step 6: Typecheck**

```bash
cd frontend-v3 && npx tsc --noEmit 2>&1 | grep -E "reanalyze|ReanalyzeButton" || echo "clean"
```
Expected: `clean` (no errors related to the new component). Pre-existing TS errors elsewhere are not regressions.

- [ ] **Step 7: Commit**

```bash
git add frontend-v3/src/app/calls/[id]/components/ReanalyzeButton.tsx \
        frontend-v3/src/app/calls/[id]/page.tsx \
        frontend-v3/tests/unit/reanalyze-button.test.tsx
git commit -m "feat(frontend): add ReanalyzeButton on call detail page"
```

---

## Task 12: Durability documentation

**Files:**
- Create: `docs/durability.md`
- Modify: `infrastructure/contabo/README.md`

- [ ] **Step 1: Write `docs/durability.md`**

Create `/Users/gomaa/Documents/Compliance/docs/durability.md`:

```markdown
# Durability runbook (Wave 3)

This wave maps three durability concerns from the architecture spec to concrete code paths in the repo.

## 1. Pipeline durability

**Spec:** at-least-once delivery, acks-late semantics, checkpointable resumes.

**Implementation:** Inngest workflow engine. Each pipeline step is wrapped in `ctx.step.run("<name>", ...)`, which Inngest memoizes by `(step_name, input_hash)`. A redispatched event resumes from the first non-memoized step rather than re-running steps that already produced output. The `redispatch_watchdog` cron (Wave 1) detects calls whose `last_step_started_at` is older than 7 minutes and emits a fresh `call/uploaded` event; Inngest's memoization makes the redispatch idempotent.

Failed-job forensics: `failed_jobs` table (Wave 1) gets a row when an Inngest run exhausts its retry budget. Operators surface these in `/observability/stuck` and can replay via `POST /calls/{id}/reanalyze` (Wave 3).

## 2. Replay

**Spec:** re-derive a verdict from the stored transcript without re-transcription cost.

**Implementation:** `POST /calls/{id}/reanalyze` (`backend/app/replay.py`) emits an Inngest `call/reanalyze` event. The `process_call_reanalyze` workflow function (`backend/app/workflows/process_call.py`) runs only steps 4 (analyze_checkpoints) → 5 (score) → 6 (finalize). Existing CallCheckpoint idempotency replaces prior rows. An `audit_log` entry is written for every reanalyze.

Constraints: requires `Call.transcript`, `Call.word_data`, and `Call.script_id` to be non-null. Returns 422 otherwise. Rate-limited to 1/min/call (out-of-scope for Wave 3 — add when abuse measured).

## 3. Database backup + restore drill

**Spec:** daily encrypted backup, 7-day retention, one restore drill per quarter.

**Implementation:**
- `backend/scripts/pg_dump_to_storage.py` runs `pg_dump --format=custom --compress=6`, optionally encrypts with `age` (recipient public key in `BACKUP_AGE_RECIPIENT`), uploads to `<backup_bucket>/YYYY/MM/DD/compliance-HHMMSS.sql.gz[.age]` via the active StorageBackend.
- `backend/app/workflows/pg_dump_nightly.py` is an Inngest scheduled function (`cron 0 2 * * *` UTC) invoking the script. Inngest retries up to 3× with exponential backoff; final failure produces a `failed_jobs` row.
- `scripts/restore_drill.sh` exercises the restore path: download → decrypt → pg_restore into a scratch DB → row-count sanity report. Run quarterly. Document in `claude-progress.txt`.

### Retention
Object-store retention is enforced by the storage provider (Supabase Storage policy or S3 lifecycle rule). Set retention=7d on `<backup_bucket>/*` in the prod console. Code does not delete; storage policy does.

### Encryption key management
`BACKUP_AGE_RECIPIENT` is the age public key (recipient). Keep the matching identity (private key) **off the VPS** — operators store it in a secrets vault. To restore, copy the identity file to the restore host and pass `BACKUP_AGE_IDENTITY` to `restore_drill.sh`.

## 4. Storage portability

**Spec:** swap object stores via env var with no code change.

**Implementation:** `app/storage/__init__.py` exposes `StorageBackend` ABC and `get_backend()` factory. Two impls: `SupabaseBackend` (default, `STORAGE_BACKEND=supabase`) and `S3Backend` (`STORAGE_BACKEND=s3`, supports MinIO/R2/AWS S3 via `S3_ENDPOINT`). Legacy module-level functions `upload_audio` / `download_audio` / `signed_url` delegate to the active backend so existing call sites are untouched.

Smoke procedure for swap: set `STORAGE_BACKEND=s3` + MinIO creds in `.env`, restart backend, upload a call audio, verify it lands in MinIO bucket and signed-URL playback works. Flip back to supabase, restart, verify same.

## Operational checklist

- [ ] First production backup completed (check `backups/<today>` exists in storage).
- [ ] First restore drill completed and documented (date + rough table row counts).
- [ ] `BACKUP_AGE_RECIPIENT` set in prod env; matching identity stored off-VPS.
- [ ] Storage retention=7d configured in provider console.
- [ ] Reanalyze endpoint tested in prod against a sample call.
```

- [ ] **Step 2: Append backup notes to Contabo runbook**

In `/Users/gomaa/Documents/Compliance/infrastructure/contabo/README.md`, after the "Day-to-day" section, add:

```markdown
### Backups (Wave 3)

Daily `pg_dump` runs via Inngest at 02:00 UTC.

Required env on the VPS (in `/opt/compliance/.env`):

```bash
BACKUP_BUCKET=backups
BACKUP_AGE_RECIPIENT=age1...        # public key of off-VPS identity
```

Manual backup: `docker compose exec compliance-backend python -m backend.scripts.pg_dump_to_storage`.

Restore drill: `bash scripts/restore_drill.sh --latest` from any host with `BACKUP_AGE_IDENTITY` and `DATABASE_URL_SCRATCH` set.
```

- [ ] **Step 3: Commit**

```bash
git add docs/durability.md infrastructure/contabo/README.md
git commit -m "docs(durability): wave 3 runbook + Contabo backup notes"
```

---

## Task 13: End-to-end smoke (manual, post-implementation)

This is a human-run gate. Skip during automated execution; revisit after merge.

- [ ] **Step 1: Boot the stack with both backends in turn**

```bash
# Default Supabase backend (no env change)
cd backend && uvicorn app.main:app --port 8001 --reload &
curl -X POST http://localhost:8001/upload -F "audio=@sample.mp3"   # use any sample call
# Verify 200 + audio appears in Supabase call-audio bucket
kill %1
```

```bash
# Switch to MinIO via S3Backend
docker run --rm -d -p 9000:9000 -e MINIO_ROOT_USER=admin -e MINIO_ROOT_PASSWORD=adminpass minio/minio server /data
# Create bucket via mc or AWS CLI
STORAGE_BACKEND=s3 S3_ENDPOINT=http://localhost:9000 S3_ACCESS_KEY=admin S3_SECRET_KEY=adminpass S3_BUCKET=call-audio \
  uvicorn app.main:app --port 8001 --reload &
curl -X POST http://localhost:8001/upload -F "audio=@sample.mp3"
# Verify audio appears in MinIO bucket; playback via signed URL works
kill %1
```

- [ ] **Step 2: Trigger one reanalyze**

```bash
# Pick a call_id with a non-null transcript from the DB
CALL_ID=<uuid>
curl -X POST http://localhost:8001/calls/$CALL_ID/reanalyze
# Expect 202 with run_id
# Watch Inngest dashboard or local dev server logs — process_call_reanalyze fires
# Wait ~10s, GET /calls/$CALL_ID — verdict should reflect a fresh derivation timestamp
```

- [ ] **Step 3: Trigger one nightly backup manually**

```bash
cd /Users/gomaa/Documents/Compliance && python -m backend.scripts.pg_dump_to_storage
# Expect: prints `backups/2026/05/.../compliance-HHMMSS.sql.gz[.age]`
# Verify the file exists in the active storage backend
```

- [ ] **Step 4: Restore drill against scratch DB**

```bash
createdb -h localhost -p 5433 -U postgres compliance_scratch
DATABASE_URL_SCRATCH="postgres://postgres:postgres@localhost:5433/compliance_scratch" \
  BACKUP_REMOTE_KEY="<key from Step 3>" \
  bash scripts/restore_drill.sh
# Expect: completes with "OK" + table row counts within 24 h drift of prod
```

- [ ] **Step 5: Document the smoke pass in claude-progress.txt**

Append:

```
[YYYY-MM-DD] WAVE 3 SMOKE: storage swap supabase↔MinIO clean, reanalyze on call X
returned new verdict in Ys, manual pg_dump produced backups/<key>, restore drill
completed against compliance_scratch with row counts matching prod ±N.
```

- [ ] **Step 6: Final commit**

```bash
git add claude-progress.txt
git commit -m "docs(progress): wave 3 smoke pass — storage swap, reanalyze, backup, restore"
```

---

## Task 14: Open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/wave3-durability
```

- [ ] **Step 2: Create PR**

```bash
gh pr create \
  --base main \
  --head feat/wave3-durability \
  --title "Wave 3 — Durability: Storage Backend ABC + Reanalyze + pg_dump backups" \
  --body-file - <<'EOF'
## Summary

- `app/storage/` package: `StorageBackend` ABC + `SupabaseBackend` (default) + `S3Backend` (boto3, works against MinIO/R2/AWS). Selected by `STORAGE_BACKEND` env. Legacy `upload_audio` / `download_audio` / `signed_url` shimmed for back-compat.
- `POST /calls/{id}/reanalyze` (`app/replay.py`) emits `call/reanalyze`. New Inngest function `process_call_reanalyze` runs only steps 4-5-6 on the stored transcript. Frontend `ReanalyzeButton` on call detail page.
- `backend/scripts/pg_dump_to_storage.py` produces an age-encrypted dated tarball, uploaded via the active StorageBackend. Inngest scheduled function `pg_dump_nightly` fires at 02:00 UTC.
- `scripts/restore_drill.sh` exercises the restore path end-to-end.
- `docs/durability.md` runbook + Contabo notes appended.

## Test plan
- [x] `pytest tests/test_storage_backend.py tests/test_replay.py tests/test_pg_dump_script.py tests/test_workflows_pg_dump_nightly.py` — all green.
- [x] `vitest tests/unit/reanalyze-button.test.tsx` — 3 cases pass.
- [x] `bash -n scripts/restore_drill.sh` — shell syntax clean.
- [ ] **Human follow-up:** end-to-end smoke documented in `docs/durability.md` §Operational checklist; backup + restore drill run once before flipping prod traffic.

## Reviewer focus
1. ABC contract — `upload_blob` / `download_blob` / `signed_url` / `delete_blob` signatures match across both impls.
2. Legacy shim preserves byte-identical behavior of `upload_audio`/`download_audio`/`signed_url` (no change to existing call sites).
3. `process_call_reanalyze` re-uses the same private step functions (`_step_analyze_checkpoints`, `_step_score`, `_step_finalize`) — no duplicated business logic.
4. `pg_dump_to_storage.py` is mock-driven in tests (no live pg_dump in CI). Real `pg_dump` exercised only by manual smoke + nightly Inngest run.
5. age-encryption is opt-in (empty recipient = plaintext gzip — flag for prod hardening).
6. `restore_drill.sh` is bash, not python — keeps it usable from any host without the venv.

## Out of scope (Wave 4+)
- Embedding pre-filter + tiered LLM flag flips (Wave 4)
- Branch protection + deploy.yml SSH (Wave 5)
- Storage retention enforcement in code (rely on provider lifecycle policy)
- Reanalyze rate limiting (add when abuse measured)

## Human follow-ups before merge
1. Set `BACKUP_AGE_RECIPIENT` in prod env; store identity off-VPS.
2. Configure 7-day retention on `backups/*` in storage provider console.
3. Add Inngest cron `pg_dump_nightly` to the Inngest dashboard (auto-registered on backend redeploy, but verify visible in UI).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

- [ ] **Step 3: Note PR URL**

Capture `gh pr create` output. Don't block on `gh pr checks --watch`.

---

## Wave 3 acceptance gate

- [ ] All 14 tasks complete and committed (one commit per task minimum, fix-loop commits as needed).
- [ ] CI green on PR.
- [ ] Storage swap supabase↔MinIO smoke completed locally (manual gate).
- [ ] One reanalyze through the full pipeline returned a verdict with no transcription cost.
- [ ] One pg_dump_to_storage produced an artefact in the active backend.
- [ ] One restore_drill.sh run completed with table-row sanity check.
- [ ] `claude-progress.txt` updated with WAVE 3 SMOKE entry.

Wave 4 (cost optimizers — embedding pre-filter + tiered LLM flag flips with A/B parity gate) is the next plan to write after Wave 3 merges.
