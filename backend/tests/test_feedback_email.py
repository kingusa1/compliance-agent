"""Tests for app.notifications.feedback_email — pure rendering +
graceful skip behaviour. The actual HTTPS call is mocked.
"""
from __future__ import annotations

import pytest

from app.notifications.feedback_email import (
    FeedbackEmailPayload,
    _render_body,
    _render_subject,
    build_feedback_payload_from_analysis,
    send_feedback_email,
)


def _payload(verdict: str = "BLOCK", rejections=None, score: int = 50) -> FeedbackEmailPayload:
    return FeedbackEmailPayload(
        to="agent@example.com",
        customer_name="Crosby Grange Property Management Company Limited",
        call_id="abc123",
        rejections=rejections if rejections is not None else [{
            "reason_code": "R01",
            "category": "COMPLIANCE_ISSUE",
            "severity": "CRITICAL",
            "fix_required": "Please state Watt Utilities at the start of the call.",
        }],
        overall_verdict=verdict,
        score=score,
    )


def test_subject_block_format():
    p = _payload()
    assert _render_subject(p) == (
        "[BLOCK] Crosby Grange Property Management Company Limited "
        "— compliance issues need amendment"
    )


def test_subject_pass_format():
    p = _payload(verdict="PASS", score=95, rejections=[])
    assert _render_subject(p).startswith("[PASS]")


def test_body_block_includes_fix_required_text():
    p = _payload()
    body = _render_body(p)
    assert "Watt Utilities at the start of the call" in body
    assert "R01" in body
    assert "CRITICAL" in body


def test_body_pass_skips_rejection_list():
    p = _payload(verdict="PASS", rejections=[], score=98)
    body = _render_body(p)
    assert "passed compliance" in body
    assert "98" in body
    # No bulleted rejection lines.
    assert "- [" not in body


@pytest.mark.asyncio
async def test_send_skipped_when_no_credentials():
    """Pipeline must keep running when SMTP creds aren't configured."""
    p = _payload()
    sent = await send_feedback_email(p)  # no api_key
    assert sent is False


@pytest.mark.asyncio
async def test_send_uses_httpx_post(monkeypatch):
    """Verify the POST body shape so vendor swaps stay easy."""
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = ""

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.notifications.feedback_email.httpx.AsyncClient", FakeClient)

    p = _payload()
    sent = await send_feedback_email(
        p,
        api_endpoint="https://api.fake-mailer.test/send",
        api_key="test-key",
        from_address="compliance@watt.test",
    )
    assert sent is True
    assert captured["url"] == "https://api.fake-mailer.test/send"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["from"] == "compliance@watt.test"
    assert captured["json"]["to"] == ["agent@example.com"]
    # Tag list shape — used by Resend / Postmark / SendGrid.
    tags = {t["name"]: t["value"] for t in captured["json"]["tags"]}
    assert tags["verdict"] == "block"
    assert tags["call_id"] == "abc123"


@pytest.mark.asyncio
async def test_send_returns_false_on_http_error(monkeypatch):
    class FakeResponse:
        status_code = 500
        text = "internal server error"

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            return FakeResponse()

    monkeypatch.setattr("app.notifications.feedback_email.httpx.AsyncClient", FakeClient)
    p = _payload()
    sent = await send_feedback_email(p, api_endpoint="x", api_key="y")
    assert sent is False


def test_build_from_analysis_round_trip():
    analysis_result = {
        "verdict": "block",
        "score": "30",  # str on purpose — must coerce
        "rejections": [{"reason_code": "R09", "severity": "CRITICAL",
                        "category": "COMPLIANCE_ISSUE",
                        "fix_required": "Remove guarantee phrase."}],
        "risk_tags": ["mis_selling_risk"],
    }
    p = build_feedback_payload_from_analysis(
        to="agent@example.com",
        customer_name="Acme",
        call_id="xyz",
        analysis_result=analysis_result,
    )
    assert p.overall_verdict == "BLOCK"
    assert p.score == 30
    assert len(p.rejections) == 1
    assert p.rejections[0]["reason_code"] == "R09"
