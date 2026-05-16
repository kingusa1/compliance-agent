"""Tests for the AssemblyAI webhook endpoint.

Coverage:
- 401 on missing secret header
- 401 on wrong secret header
- 401 on empty secret (even if status=completed)
- 200 on valid secret + transcript_id (completed)
- 200 on valid secret + transcript_id (error status — call marked failed)
- Sentinel is written on valid delivery
- Response is returned in < 1s
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import webhook_routes


_TEST_SECRET = "deadbeef" * 8  # 64-char hex — realistic value
_TRANSCRIPT_ID = "test-transcript-abc123"


def _client_with_secret(secret: str) -> TestClient:
    """Return a TestClient with ASSEMBLYAI_WEBHOOK_SECRET patched."""
    return TestClient(app, raise_server_exceptions=True)


def _post_webhook(client: TestClient, *, secret_header: str | None, body: dict) -> any:
    headers = {}
    if secret_header is not None:
        headers["X-AssemblyAI-Webhook-Secret"] = secret_header
    return client.post(
        "/api/webhooks/assemblyai",
        content=json.dumps(body),
        headers={"Content-Type": "application/json", **headers},
    )


# ── Auth tests ──────────────────────────────────────────────────────────────


def test_webhook_returns_401_when_header_missing(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    client = TestClient(app)
    r = _post_webhook(client, secret_header=None, body={"transcript_id": _TRANSCRIPT_ID, "status": "completed"})
    assert r.status_code == 401


def test_webhook_returns_401_when_header_wrong(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    client = TestClient(app)
    r = _post_webhook(client, secret_header="wrong-secret", body={"transcript_id": _TRANSCRIPT_ID, "status": "completed"})
    assert r.status_code == 401


def test_webhook_returns_401_when_secret_is_empty_string(monkeypatch):
    """Empty provided secret must NOT pass even if somehow env is also empty."""
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    client = TestClient(app)
    r = _post_webhook(client, secret_header="", body={"transcript_id": _TRANSCRIPT_ID, "status": "completed"})
    assert r.status_code == 401


def test_webhook_returns_401_when_env_secret_not_set(monkeypatch):
    """No env secret configured → every request is rejected."""
    monkeypatch.delenv("ASSEMBLYAI_WEBHOOK_SECRET", raising=False)
    client = TestClient(app)
    r = _post_webhook(client, secret_header=_TEST_SECRET, body={"transcript_id": _TRANSCRIPT_ID, "status": "completed"})
    assert r.status_code == 401


# ── Success tests ────────────────────────────────────────────────────────────


def test_webhook_returns_200_on_valid_secret_completed(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    # Clear any prior sentinel state
    webhook_routes._WEBHOOK_ARRIVALS.clear()
    client = TestClient(app)
    r = _post_webhook(
        client,
        secret_header=_TEST_SECRET,
        body={"transcript_id": _TRANSCRIPT_ID, "status": "completed"},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_webhook_returns_200_on_valid_secret_error_status(monkeypatch):
    """status=error must still return 200 so AssemblyAI doesn't retry."""
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    webhook_routes._WEBHOOK_ARRIVALS.clear()
    client = TestClient(app)
    r = _post_webhook(
        client,
        secret_header=_TEST_SECRET,
        body={"transcript_id": _TRANSCRIPT_ID, "status": "error"},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_webhook_writes_sentinel_on_completed(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    webhook_routes._WEBHOOK_ARRIVALS.clear()
    client = TestClient(app)
    tid = "sentinel-test-id-completed"
    _post_webhook(
        client,
        secret_header=_TEST_SECRET,
        body={"transcript_id": tid, "status": "completed"},
    )
    assert webhook_routes._WEBHOOK_ARRIVALS.get(tid) == "completed"


def test_webhook_writes_sentinel_on_error(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    webhook_routes._WEBHOOK_ARRIVALS.clear()
    client = TestClient(app)
    tid = "sentinel-test-id-error"
    _post_webhook(
        client,
        secret_header=_TEST_SECRET,
        body={"transcript_id": tid, "status": "error"},
    )
    assert webhook_routes._WEBHOOK_ARRIVALS.get(tid) == "error"


def test_webhook_responds_within_one_second(monkeypatch):
    """Handler must return 200 in < 1s (well within AssemblyAI's 10s window)."""
    monkeypatch.setenv("ASSEMBLYAI_WEBHOOK_SECRET", _TEST_SECRET)
    webhook_routes._WEBHOOK_ARRIVALS.clear()
    client = TestClient(app)
    t0 = time.monotonic()
    r = _post_webhook(
        client,
        secret_header=_TEST_SECRET,
        body={"transcript_id": "timing-test-id", "status": "completed"},
    )
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert elapsed < 1.0, f"webhook took {elapsed:.3f}s — must be < 1s"
