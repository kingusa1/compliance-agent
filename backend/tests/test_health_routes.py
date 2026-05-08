from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_healthz_returns_200():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_returns_200_when_db_reachable():
    r = client.get("/readyz")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "ready"
    assert payload["checks"]["db"] == "ok"


def test_readyz_returns_503_when_db_unreachable():
    with patch("app.main.engine.connect", side_effect=RuntimeError("simulated outage")):
        r = client.get("/readyz")
    assert r.status_code == 503
    payload = r.json()
    assert payload["status"] == "degraded"
    assert payload["checks"]["db"].startswith("fail:")


def test_metrics_endpoint_exposes_prometheus_text():
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    # Default instrumentator metrics
    assert "http_requests_total" in body or "http_request_duration_seconds" in body
    # Custom metric registered at import time
    assert "pipeline_step_duration_seconds" in body
