"""Tests for app.watt_compliance.persist.persist_watt_analysis.

Pins the Watt-JSON → DB-rows mapping so the dashboard always shows the
same data the LLM produced. In-memory SQLite, careful teardown."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    import app.models  # noqa: F401  — register tables
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def _make_call(db, **overrides):
    from app.models import Call
    cid = overrides.get("id") or str(uuid.uuid4())
    defaults = dict(
        id=cid,
        filename="test.mp3",
        # file_path is NOT NULL on the Call schema; in production it's
        # the Supabase Storage key. Test uses a synthetic path.
        file_path=f"{cid}/test.mp3",
        status="processing",
        compliance_status="pending",
        risk_tags=[],
    )
    defaults.update(overrides)
    c = Call(**defaults)
    db.add(c)
    db.flush()
    return c


def test_persist_pass_verdict_marks_compliant(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    analysis = {
        "verdict": "PASS",
        "score": 95,
        "compliance_status": "compliant",
        "rejections": [],
        "risk_tags": [],
        "summary": "Clean call, all standards met.",
        "supplier_detected": "eon_next",
        "call_type_detected": "lead_gen",
    }
    out = persist_watt_analysis(call=call, analysis=analysis, db=db)
    db.commit()

    assert call.compliance_status == "compliant"
    assert call.score == "95/100"
    assert call.reason == "Clean call, all standards met."
    assert out["rejections_written"] == 0
    assert out["rejections_skipped"] == 0


def test_persist_block_with_rejections(db):
    from app.watt_compliance.persist import persist_watt_analysis
    from app.models import Rejection

    call = _make_call(db)
    analysis = {
        "verdict": "BLOCK",
        "score": 30,
        "compliance_status": "non_compliant",
        "rejections": [
            {
                "reason_code": "R01",
                "category": "COMPLIANCE_ISSUE",
                "severity": "CRITICAL",
                "evidence_quote": "Hi, this is Sarah calling about your gas...",
                "fix_required": "Please state Watt Utilities at start of call.",
            },
            {
                "reason_code": "R09",
                "category": "COMPLIANCE_ISSUE",
                "severity": "CRITICAL",
                "evidence_quote": "I can guarantee this is the cheapest...",
                "fix_required": "Remove guarantee phrase from script.",
            },
        ],
        "risk_tags": ["mis_selling_risk"],
        "summary": "Two critical breaches — identity + price guarantee.",
        "supplier_detected": "eon_next",
    }
    out = persist_watt_analysis(call=call, analysis=analysis, db=db)
    db.commit()

    assert call.compliance_status == "non_compliant"
    assert call.score == "30/100"
    assert call.risk_tags == ["mis_selling_risk"]
    assert out["rejections_written"] == 2
    rows = db.query(Rejection).filter(Rejection.call_id == call.id).all()
    assert {r.category for r in rows} == {"COMPLIANCE_ISSUE"}
    reasons = sorted(r.rejection_reason for r in rows)
    assert reasons[0].startswith("R01"), "R-code prefix must be in the reason text"
    assert "Identity" in reasons[0]  # title from REJECTION_REASONS spec
    assert reasons[1].startswith("R09")
    fix_required_set = {r.fix_required for r in rows}
    assert "Please state Watt Utilities at start of call." in fix_required_set


def test_persist_idempotent_replaces_prior_rejections(db):
    from app.watt_compliance.persist import persist_watt_analysis
    from app.models import Rejection

    call = _make_call(db)
    first = {
        "verdict": "BLOCK",
        "score": 20,
        "rejections": [
            {"reason_code": "R01", "category": "COMPLIANCE_ISSUE",
             "severity": "CRITICAL",
             "evidence_quote": "x", "fix_required": "fix1"},
            {"reason_code": "R02", "category": "COMPLIANCE_ISSUE",
             "severity": "HIGH",
             "evidence_quote": "x", "fix_required": "fix2"},
        ],
        "risk_tags": [],
        "summary": "first",
    }
    persist_watt_analysis(call=call, analysis=first, db=db)
    db.commit()
    assert db.query(Rejection).filter(Rejection.call_id == call.id).count() == 2

    second = {
        "verdict": "REVIEW",
        "score": 60,
        "rejections": [
            {"reason_code": "R09", "category": "COMPLIANCE_ISSUE",
             "severity": "HIGH",
             "evidence_quote": "y", "fix_required": "fix3"},
        ],
        "risk_tags": [],
        "summary": "second",
    }
    out = persist_watt_analysis(call=call, analysis=second, db=db)
    db.commit()
    rows = db.query(Rejection).filter(Rejection.call_id == call.id).all()
    assert len(rows) == 1
    assert rows[0].rejection_reason.startswith("R09")
    assert out["rejections_deleted"] == 2
    assert out["rejections_written"] == 1


def test_persist_unknown_category_skipped(db):
    from app.watt_compliance.persist import persist_watt_analysis
    from app.models import Rejection

    call = _make_call(db)
    analysis = {
        "verdict": "REVIEW", "score": 65,
        "rejections": [
            {"reason_code": "Rxx", "category": "TOTAL_NONSENSE",
             "severity": "HIGH", "evidence_quote": "x", "fix_required": "x"},
            {"reason_code": "R03", "category": "COMPLIANCE_ISSUE",
             "severity": "HIGH", "evidence_quote": "y", "fix_required": "y"},
        ],
        "risk_tags": [],
        "summary": "mixed",
    }
    out = persist_watt_analysis(call=call, analysis=analysis, db=db)
    db.commit()
    assert out["rejections_written"] == 1
    assert out["rejections_skipped"] == 1
    rows = db.query(Rejection).filter(Rejection.call_id == call.id).all()
    assert len(rows) == 1
    assert rows[0].category == "COMPLIANCE_ISSUE"


def test_score_clamped_to_0_100(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    persist_watt_analysis(call=call, analysis={
        "verdict": "PASS", "score": 250, "rejections": [], "risk_tags": [],
        "summary": "x",
    }, db=db)
    db.commit()
    assert call.score == "100/100"

    persist_watt_analysis(call=call, analysis={
        "verdict": "BLOCK", "score": -10, "rejections": [], "risk_tags": [],
        "summary": "y",
    }, db=db)
    db.commit()
    assert call.score == "0/100"


def test_score_string_input_coerced(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    persist_watt_analysis(call=call, analysis={
        "verdict": "PASS", "score": "85", "rejections": [], "risk_tags": [],
        "summary": "x",
    }, db=db)
    db.commit()
    assert call.score == "85/100"


def test_score_garbage_input_left_alone(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    call.score = "5/7"
    db.flush()
    persist_watt_analysis(call=call, analysis={
        "verdict": "PASS", "score": "abc", "rejections": [], "risk_tags": [],
        "summary": "x",
    }, db=db)
    db.commit()
    assert call.score == "5/7", "garbage score must not stomp existing value"


def test_verdict_only_drives_compliance_status_when_field_missing(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    persist_watt_analysis(call=call, analysis={
        "verdict": "BLOCK", "score": 20, "rejections": [], "risk_tags": [],
        "summary": "x",
        # compliance_status omitted on purpose
    }, db=db)
    db.commit()
    assert call.compliance_status == "non_compliant"


def test_risk_tag_coercion(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    persist_watt_analysis(call=call, analysis={
        "verdict": "REVIEW", "score": 65,
        "rejections": [],
        "risk_tags": ["misselling", "Ombudsman", "garbage_tag"],
        "summary": "x",
    }, db=db)
    db.commit()
    assert sorted(call.risk_tags) == ["mis_selling_risk", "ombudsman_risk"]


def test_rejected_at_override(db):
    from app.watt_compliance.persist import persist_watt_analysis
    from app.models import Rejection
    call = _make_call(db)
    fixed_time = datetime(2026, 5, 9, 10, 30, tzinfo=timezone.utc)
    persist_watt_analysis(call=call, analysis={
        "verdict": "BLOCK", "score": 30,
        "rejections": [{"reason_code": "R01", "category": "COMPLIANCE_ISSUE",
                        "severity": "CRITICAL", "evidence_quote": "x",
                        "fix_required": "x"}],
        "risk_tags": [],
        "summary": "x",
    }, db=db, rejected_at=fixed_time)
    db.commit()
    row = db.query(Rejection).filter(Rejection.call_id == call.id).first()
    # SQLite drops tz-info when storing — compare wall-clock time only.
    assert row.rejected_at.replace(tzinfo=None) == fixed_time.replace(tzinfo=None)


def test_none_call_raises():
    from app.watt_compliance.persist import persist_watt_analysis
    with pytest.raises(ValueError, match="call must not be None"):
        persist_watt_analysis(call=None, analysis={}, db=None)  # type: ignore[arg-type]


def test_non_dict_analysis_raises(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    with pytest.raises(TypeError, match="analysis must be a dict"):
        persist_watt_analysis(call=call, analysis="not a dict", db=db)  # type: ignore[arg-type]


def test_returns_summary_with_counts(db):
    from app.watt_compliance.persist import persist_watt_analysis
    call = _make_call(db)
    out = persist_watt_analysis(call=call, analysis={
        "verdict": "BLOCK", "score": 30, "compliance_status": "non_compliant",
        "rejections": [
            {"reason_code": "R01", "category": "COMPLIANCE_ISSUE",
             "severity": "CRITICAL", "evidence_quote": "x", "fix_required": "x"},
        ],
        "risk_tags": ["complaint_risk"],
        "summary": "x",
        "supplier_detected": "edf",
    }, db=db)
    db.commit()
    assert out["call_id"] == call.id
    assert out["verdict"] == "BLOCK"
    assert out["compliance_status"] == "non_compliant"
    assert out["score"] == "30/100"
    assert out["rejections_written"] == 1
    assert out["risk_tags"] == ["complaint_risk"]
