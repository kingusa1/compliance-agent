import io
import json
import logging

from app.logger import setup_logger


def test_logger_emits_json_with_required_fields():
    """The compliance logger must emit single-line JSON containing the fields
    Promtail/Loki and Grafana dashboards rely on (asctime, levelname, name,
    message, plus any structured `extra` keys like job_id and step).

    We attach a temporary stream to the configured logger so we read the
    actual handler output instead of relying on pytest's caplog (which is
    bypassed by the production logger's propagate=False setting) or capsys
    (which can't intercept the handler's already-bound sys.stdout)."""
    log = setup_logger()
    assert log.handlers, "setup_logger must install at least one handler"

    handler = log.handlers[0]
    buffer = io.StringIO()
    original_stream = handler.stream
    handler.stream = buffer
    try:
        log.info("hello", extra={"job_id": "abc123", "step": "transcribe"})
    finally:
        handler.stream = original_stream

    output = buffer.getvalue().strip()
    assert output, "expected the logger handler to emit one line"

    # Must be a single line so Promtail treats it as one log event.
    assert output.count("\n") == 0, f"log output must be single-line JSON, got: {output!r}"

    payload = json.loads(output)
    assert payload["message"] == "hello"
    assert payload["job_id"] == "abc123"
    assert payload["step"] == "transcribe"
    assert payload["levelname"] == "INFO"
    assert "asctime" in payload
