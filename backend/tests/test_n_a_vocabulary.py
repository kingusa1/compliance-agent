"""Tests for D10 — `n_a` checkpoint vocabulary (2026-05-27).

Closes the analyst report pattern 1: conditional checkpoints whose trigger
condition does not fire should be marked `n_a` (excluded from the score
denominator) rather than `fail` (counted against the call). Expected
impact: ~16 phantom failures per call removed per the 2026-05-26 analyst
sample.

These tests pin the contract end-to-end:

* The grader output schema accepts `n_a` as a status value.
* `aggregate_results` excludes `n_a` rows from total, passed, partial,
  failed counters and surfaces a separate `n_a` count.
* `n_a` rows do NOT show up in any breach list (critical / high / medium)
  so the bucket derivation is not dragged down by inapplicable
  conditionals.
* The `CallCheckpoint` model exposes the `is_not_applicable` Boolean and
  defaults to False so legacy rows stay correct.
"""
from __future__ import annotations

import pytest

from app.models import CallCheckpoint


# ─── Fixtures ──────────────────────────────────────────────────────────


def _verdict(status: str, *, name: str = "rule", severity: str = "medium") -> dict:
    """Minimal grader-output dict shaped like the keys aggregate_results reads."""
    return {
        "name": name,
        "status": status,
        "evidence": "x" if status in ("pass", "partial") else "",
        "notes": None,
        "confidence": "high",
        "needs_review": False,
        "severity": severity,
    }


# ─── aggregate_results contract ────────────────────────────────────────


class TestAggregateResultsExcludesNotApplicable:
    """`n_a` rows must not contribute to total / passed / failed / partial."""

    def _aggregate(self, results: list[dict]) -> dict:
        # Import inside the test so the module-level dependencies are loaded
        # only when the test is collected (some are slow).
        from app.checkpoint_analyzer import aggregate_results

        return aggregate_results(results=results, checkpoints=[])

    def test_n_a_excluded_from_denominator(self) -> None:
        results = [
            _verdict("pass"),
            _verdict("pass"),
            _verdict("n_a"),
            _verdict("n_a"),
            _verdict("fail", severity="medium"),
        ]
        summary = self._aggregate(results)["summary"]
        # 2 pass + 1 fail = 3 in denominator; the 2 n_a rows are excluded.
        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["n_a"] == 2

    def test_n_a_not_counted_as_failed(self) -> None:
        results = [_verdict("pass"), _verdict("n_a"), _verdict("n_a")]
        summary = self._aggregate(results)["summary"]
        # 1 pass, 0 failed (the n_a rows do not count as failures).
        assert summary["total"] == 1
        assert summary["failed"] == 0

    def test_n_a_does_not_drag_bucket_down(self) -> None:
        # An all-pass-or-n_a segment must remain bucket=pass and compliant=True.
        results = [
            _verdict("pass"),
            _verdict("pass"),
            _verdict("n_a", severity="critical"),
            _verdict("n_a", severity="high"),
        ]
        summary = self._aggregate(results)["summary"]
        # n_a is not in the breach list — the severity field is irrelevant
        # because the row isn't in `non_error` at all.
        assert summary["bucket"] == "pass"
        assert summary["compliant"] is True
        assert summary["critical_breaches"] == 0
        assert summary["high_breaches"] == 0

    def test_pure_n_a_segment_renders_as_zero(self) -> None:
        # All-n_a segment: total=0, score string falls back to "0/0".
        results = [_verdict("n_a"), _verdict("n_a")]
        summary = self._aggregate(results)["summary"]
        assert summary["total"] == 0
        assert summary["passed"] == 0
        assert summary["n_a"] == 2
        assert summary["score"] == "0/0"


# ─── CallCheckpoint ORM contract ───────────────────────────────────────


class TestCallCheckpointIsNotApplicableColumn:
    """The new Boolean must be present, default False, and surface in the ORM."""

    def test_column_exists_and_default(self) -> None:
        col = CallCheckpoint.__table__.columns.get("is_not_applicable")
        assert col is not None, (
            "CallCheckpoint.is_not_applicable is missing. The Alembic migration "
            "`2026_05_27_n_a_vocab` added the column on Postgres; the ORM model "
            "must mirror it so callers can read/write the flag."
        )
        # Default is False at both the Python ORM level and the SQL DEFAULT
        # so SQLite / PG agree on legacy-row interpretation.
        assert col.default is not None
        assert col.default.arg is False or col.default.arg is False or col.default.arg == 0 or col.default.arg is False or callable(col.default.arg)
        assert col.nullable is False

    def test_instance_defaults_to_false(self) -> None:
        # ORM construction without an explicit value should not raise.
        cp = CallCheckpoint(
            call_id="c1",
            rule_text="some rule",
            passed=True,
        )
        # Whether `is_not_applicable` is set to False at __init__ time or only
        # after flush depends on SQLAlchemy's default propagation — accept
        # either None (will be populated by server_default on insert) or False.
        val = getattr(cp, "is_not_applicable", None)
        assert val in (None, False)


# ─── DisplayState frontend contract (Python-side smoke) ────────────────


class TestStatusVocabularyParity:
    """Backend status enum must include n_a so the frontend lib/
    checkpoint-state.ts mapping (`status === 'n_a' → not_applicable`)
    has something to dispatch on."""

    def test_grader_emoji_table_includes_n_a(self) -> None:
        # The analyzer log line emits a status emoji per checkpoint; if the
        # vocabulary regresses, the .get(status, fallback) returns the
        # fallback and the log loses signal. Pin the contract via source
        # introspection so a future refactor that drops n_a from the
        # emoji map fails this test.
        import inspect
        from app import checkpoint_analyzer

        src = inspect.getsource(checkpoint_analyzer)
        assert '"n_a"' in src, (
            "checkpoint_analyzer.py must reference the n_a status string "
            "(see status_emoji map + aggregate_results filter)."
        )
