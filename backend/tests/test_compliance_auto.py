"""Tests for app.compliance.derive_compliance — pure-function auto-derivation
of `call.compliance_status` from the checkpoint_results JSON produced by the
pipeline.

Pure unit tests — no TestClient, no HTTP. Uses the `test_db` fixture from
conftest so fixture setup matches the rest of the suite.
"""
import json

from app.compliance import derive_compliance
from app.models import Call, ComplianceDecision


def test_empty_checkpoints_stays_pending(test_db):
    c = Call(id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="...", duration_seconds=10)
    test_db.add(c)
    test_db.commit()
    result = derive_compliance(c, test_db)
    assert result == "pending"
    assert c.compliance_status == "pending"


def test_all_pass_becomes_compliant(test_db):
    c = Call(
        id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="...", duration_seconds=10,
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "pass", "confidence": 0.95},
            {"id": "cp_2", "status": "pass", "confidence": 0.88},
        ]),
    )
    test_db.add(c)
    test_db.commit()
    result = derive_compliance(c, test_db)
    assert result == "compliant"
    assert c.compliance_status == "compliant"
    assert c.compliance_source == "auto"
    cd = test_db.query(ComplianceDecision).filter_by(call_id="c1").one()
    assert cd.status == "compliant"
    assert cd.actor_type == "system"
    assert cd.is_current is True


def test_any_low_confidence_stays_pending(test_db):
    c = Call(
        id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="...", duration_seconds=10,
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "pass", "confidence": 0.95},
            {"id": "cp_2", "status": "fail", "confidence": 0.35, "needs_review": True},
        ]),
    )
    test_db.add(c)
    test_db.commit()
    result = derive_compliance(c, test_db)
    assert result == "pending"
    assert c.compliance_status == "pending"
    # No decision row is written for pending — it's awaiting human judgment.
    assert test_db.query(ComplianceDecision).filter_by(call_id="c1").count() == 0


def test_any_fail_with_confidence_becomes_non_compliant(test_db):
    c = Call(
        id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="...", duration_seconds=10,
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "pass", "confidence": 0.95},
            {"id": "cp_2", "status": "fail", "confidence": 0.92},
        ]),
    )
    test_db.add(c)
    test_db.commit()
    result = derive_compliance(c, test_db)
    assert result == "non_compliant"
    assert c.compliance_status == "non_compliant"
    cd = test_db.query(ComplianceDecision).filter_by(call_id="c1").one()
    assert cd.status == "non_compliant"
    assert cd.failing_checkpoints is not None
    failing = json.loads(cd.failing_checkpoints)
    assert "cp_2" in failing


def test_reviewer_verdict_overrides_status(test_db):
    """If a reviewer has already overridden the verdict, use their verdict."""
    c = Call(
        id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="...", duration_seconds=10,
        checkpoint_results=json.dumps([
            {
                "id": "cp_1",
                "status": "fail",
                "verdict": "pass",
                "reviewer_verdict": "pass",
                "confidence": 0.95,
            },
        ]),
    )
    test_db.add(c)
    test_db.commit()
    result = derive_compliance(c, test_db)
    assert result == "compliant"  # reviewer's "pass" wins over AI's "fail"


def test_partial_is_not_compliant(test_db):
    """A partial verdict isn't pass — count as non_compliant if not needs_review."""
    c = Call(
        id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="...", duration_seconds=10,
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "pass", "confidence": 0.95},
            {"id": "cp_2", "status": "partial", "confidence": 0.88},
        ]),
    )
    test_db.add(c)
    test_db.commit()
    result = derive_compliance(c, test_db)
    assert result == "non_compliant"
