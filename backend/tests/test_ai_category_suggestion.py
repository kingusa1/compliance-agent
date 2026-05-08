"""W4.7 (v3-watt-coverage): AI-suggested category + remediation_action
on auto-created rejections.

These tests target the ``auto_create_rejection_for_verdict`` decision
logic — the single fork that decides whether the AI's suggested bucket
or the keyword heuristic wins. Six branches:

  1. AI suggestion with confidence ≥ 0.7  → rejection inherits ai_category
  2. AI suggestion with confidence  < 0.7  → falls back to infer_category
  3. Missing AI fields entirely           → falls back to infer_category
  4. Invalid AI category (not in enum)    → falls back to infer_category
  5. Invalid AI fix_required (not in enum)→ keeps category, drops fix_required
  6. Logging emits AI_SUGGESTION vs HEURISTIC_FALLBACK appropriately

Plus a parity test that the analyzer's Watt vocabulary stays in sync
with the rejections-routes vocabulary (catches the next time someone
edits one constant set without the other).

Setup mirrors test_rejections.py — in-memory SQLite + StaticPool, autouse
clean_db fixture overrides ``get_db``.
"""
from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Rejection
from app.rejections_routes import (
    AI_CATEGORY_MIN_CONFIDENCE,
    REJECTION_CATEGORIES,
    REMEDIATION_ACTIONS,
    _resolve_ai_suggestion,
    auto_create_rejection_for_verdict,
)


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_db():
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    app.dependency_overrides.pop(get_db, None)


class _ComplianceListHandler(logging.Handler):
    """Capture records emitted on the named 'compliance' logger.

    The Wave-2 logger sets ``propagate=False``, which means pytest's
    ``caplog`` fixture (which attaches at the root logger) never sees
    these records. Tests that need to assert log content attach this
    handler directly to the named logger and read ``.records``.
    """

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        self.records.append(record)


@pytest.fixture
def compliance_caplog():
    """Returns a list-handler attached to the ``compliance`` logger so
    tests can assert against ``handler.records`` (mirrors caplog API but
    works around propagate=False)."""
    from app.logger import log as compliance_logger

    handler = _ComplianceListHandler()
    compliance_logger.addHandler(handler)
    try:
        yield handler
    finally:
        compliance_logger.removeHandler(handler)


def _seed_call(db, supplier="E.ON Next Energy") -> Call:
    """Seed a single Call row + return the ORM object so tests can pass it
    directly into auto_create_rejection_for_verdict (no HTTP layer needed —
    we're unit-testing the decision logic, not the route)."""
    c = Call(
        id="c-w4-" + uuid.uuid4().hex[:8],
        filename="x.mp3",
        file_path="x/x.mp3",
        duration_seconds=10.0,
        transcript="...",
        detected_supplier=supplier,
        agent_name="Sammie",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ─── _resolve_ai_suggestion (pure helper) ───────────────────────────────


def test_resolve_ai_suggestion_strips_invalid_enums():
    """Invalid enum values collapse to None so the caller's >=0.7 gate
    naturally falls through to the heuristic."""
    cat, fix, conf = _resolve_ai_suggestion(
        {
            "suggested_category": "BOGUS_BUCKET",
            "suggested_fix_required": "AMENDMENT_CALL",
            "category_confidence": 0.95,
        }
    )
    assert cat is None
    assert fix == "AMENDMENT_CALL"
    # Confidence dropped because no valid category to attach it to.
    assert conf is None


def test_resolve_ai_suggestion_clips_out_of_range_confidence():
    """confidence > 1.0 or < 0.0 gets discarded — defensive, the analyzer
    side already clamps but we don't trust upstream blindly."""
    cat, fix, conf = _resolve_ai_suggestion(
        {
            "suggested_category": "ADMIN_ERROR",
            "suggested_fix_required": "AMENDMENT_CALL",
            "category_confidence": 2.5,
        }
    )
    assert cat == "ADMIN_ERROR"
    assert conf is None


def test_resolve_ai_suggestion_reads_orm_attribute_path():
    """The same helper must work whether called with a dict result row
    (JSON in checkpoint_results) or an ORM CallCheckpoint row (the W4.7
    columns). Use a plain object stand-in to avoid building the full
    SQLAlchemy lifecycle for a unit test."""

    class _CP:
        ai_category = "PRICING_ISSUE"
        ai_fix_required = "PRICE_RECHECK"
        ai_category_confidence = 0.82

    cat, fix, conf = _resolve_ai_suggestion(_CP())
    assert cat == "PRICING_ISSUE"
    assert fix == "PRICE_RECHECK"
    assert conf == pytest.approx(0.82)


# ─── auto_create_rejection_for_verdict — AI vs heuristic fork ───────────


def test_ai_suggestion_above_threshold_wins(compliance_caplog):
    """W4.7 happy path — confidence ≥ 0.7 → rejection inherits AI category
    AND the AI's recommended fix_required, AND the path is logged as
    AI_SUGGESTION so prod monitoring can sort by it."""
    db = TestSessionLocal()
    try:
        call = _seed_call(db)
        cp = {
            "name": "VAT exclusion",
            "status": "fail",
            "rule_id": "VAT_EXCLUSION",
            "suggested_category": "COMPLIANCE_ERROR",
            "suggested_fix_required": "AMENDMENT_CALL",
            "category_confidence": 0.85,
        }
        r = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="sarah",
            verdict_action="FAIL",
            reason="agent skipped the VAT clause entirely",
            rule_id="VAT_EXCLUSION",
            checkpoint=cp,
        )
        db.commit()

        assert r is not None
        assert r.category == "COMPLIANCE_ERROR"
        assert r.fix_required == "AMENDMENT_CALL"
        messages = [rec.getMessage() for rec in compliance_caplog.records]
        assert any("path=AI_SUGGESTION" in m for m in messages), messages
        assert not any("path=HEURISTIC_FALLBACK" in m for m in messages)
    finally:
        db.close()


def test_ai_suggestion_below_threshold_falls_back(compliance_caplog):
    """confidence < 0.7 → ignore AI, run keyword heuristic; the AI's
    suggested_fix_required is also dropped (we don't half-trust)."""
    db = TestSessionLocal()
    try:
        call = _seed_call(db)
        cp = {
            "name": "Something",
            "status": "fail",
            "suggested_category": "DOCUSIGN_ERROR",
            "suggested_fix_required": "NEW_DOCUSIGN",
            "category_confidence": 0.45,  # below threshold
        }
        r = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="sarah",
            verdict_action="FAIL",
            reason="VAT clause missing — Green Deal section",
            checkpoint=cp,
        )
        db.commit()
        # Heuristic mapped "VAT" + "Green Deal" → COMPLIANCE_ERROR.
        assert r is not None
        assert r.category == "COMPLIANCE_ERROR"
        # fix_required dropped — heuristic doesn't suggest one.
        assert r.fix_required is None
        messages = [rec.getMessage() for rec in compliance_caplog.records]
        assert any("path=HEURISTIC_FALLBACK" in m for m in messages), messages
    finally:
        db.close()


def test_missing_ai_fields_falls_back(compliance_caplog):
    """No AI fields on the checkpoint dict at all → heuristic only.
    Verifies the helper doesn't blow up on a pre-W4.7 checkpoint."""
    db = TestSessionLocal()
    try:
        call = _seed_call(db)
        cp = {"name": "Old checkpoint", "status": "fail"}  # no AI keys
        r = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="sarah",
            verdict_action="FAIL",
            reason="BACS rejected three times",
            checkpoint=cp,
        )
        db.commit()
        assert r is not None
        assert r.category == "PROCESS_FAILURE"  # 'BACS' keyword
        assert r.fix_required is None
        messages = [rec.getMessage() for rec in compliance_caplog.records]
        assert any("path=HEURISTIC_FALLBACK" in m for m in messages), messages
    finally:
        db.close()


def test_invalid_ai_category_falls_back():
    """AI hallucinated a bucket name not in the enum → fall back even if
    confidence was high. Defensive — protects the rejection table from
    free-text categories that would later fail FK / CHECK constraints."""
    db = TestSessionLocal()
    try:
        call = _seed_call(db)
        cp = {
            "name": "Mistyped",
            "status": "fail",
            "suggested_category": "MISTYPED_BUCKET",  # not in enum
            "suggested_fix_required": "AMENDMENT_CALL",
            "category_confidence": 0.99,
        }
        r = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="sarah",
            verdict_action="FAIL",
            reason="wrong name on the LOA — typo on signup",
            checkpoint=cp,
        )
        db.commit()
        # Heuristic kicked in: keyword 'name'/'wrong'/'typo' → ADMIN_ERROR.
        assert r is not None
        assert r.category == "ADMIN_ERROR"
        # fix_required dropped (heuristic path always nulls it).
        assert r.fix_required is None
    finally:
        db.close()


def test_invalid_ai_fix_required_keeps_category_drops_fix():
    """Category is in enum + confidence is high → use it, but the AI's
    suggested_fix_required is bogus → keep category, drop fix_required.
    Mirrors how reviewers can later PATCH fix_required on the rejection
    page if needed."""
    db = TestSessionLocal()
    try:
        call = _seed_call(db)
        cp = {
            "name": "Mixed",
            "status": "fail",
            "suggested_category": "PRICING_ISSUE",  # valid
            "suggested_fix_required": "RE_QUOTE_CUSTOMER",  # NOT in enum
            "category_confidence": 0.92,
        }
        r = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="sarah",
            verdict_action="FAIL",
            reason="rate ceiling exceeded",
            checkpoint=cp,
        )
        db.commit()
        assert r is not None
        assert r.category == "PRICING_ISSUE"  # AI category preserved
        assert r.fix_required is None  # invalid fix dropped
    finally:
        db.close()


def test_ai_threshold_constant_is_seven_tenths():
    """Document the threshold value — change this test on purpose if the
    accuracy benchmark moves it. Plain-English tripwire so a silent edit
    to the constant is visible in code review."""
    assert AI_CATEGORY_MIN_CONFIDENCE == 0.7


# ─── analyzer ↔ rejections-routes vocabulary parity ─────────────────────


def test_analyzer_vocab_matches_rejections_routes_vocab():
    """The Watt vocabulary embedded in the checkpoint analyzer prompt MUST
    be the exact same set as the API-validation enums in rejections_routes.
    If someone adds a 9th category in one place but not the other the
    auto-create path would silently demote AI suggestions — this test
    fails loudly instead."""
    from app.checkpoint_analyzer import (
        WATT_REJECTION_CATEGORIES,
        WATT_REMEDIATION_ACTIONS,
    )

    analyzer_cats = {name for name, _ in WATT_REJECTION_CATEGORIES}
    analyzer_fixes = {name for name, _ in WATT_REMEDIATION_ACTIONS}

    assert analyzer_cats == REJECTION_CATEGORIES, (
        "checkpoint_analyzer.WATT_REJECTION_CATEGORIES drifted from "
        "rejections_routes.REJECTION_CATEGORIES — keep them in sync."
    )
    assert analyzer_fixes == REMEDIATION_ACTIONS, (
        "checkpoint_analyzer.WATT_REMEDIATION_ACTIONS drifted from "
        "rejections_routes.REMEDIATION_ACTIONS — keep them in sync."
    )
