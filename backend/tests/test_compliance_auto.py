"""Tests for app.compliance.derive_compliance — pure-function auto-derivation
of `call.compliance_status` from the checkpoint_results JSON produced by the
pipeline.

Pure unit tests — no TestClient, no HTTP. Uses the `test_db` fixture from
conftest so fixture setup matches the rest of the suite.
"""
import json

from app.compliance import derive_compliance
from app.models import Call, CallSegment, ComplianceDecision


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


# ── Segments path (modern pipeline) regression tests ─────────────────────
# 2026-05-26: derive_compliance was demoting bucket-aggregator verdicts
# (compliant/pending/non_compliant set by pipeline._step_score) back to
# the V1 flat-list rules, producing inverted statuses. See compliance.py
# docstring for the full incident. These tests pin the segments path so
# the regression can't recur.


def _bucket_to_status(bucket: str) -> str:
    """Mirror of ``pipeline._step_score``'s bucket→status mapping for test
    fixtures only. Kept local so a future bucket addition forces a test
    update — the production mapping lives in ``pipeline.py``."""
    return {
        "pass": "compliant",
        "coaching": "compliant",
        "review": "pending",
        "blocked": "non_compliant",
    }[bucket]


def _add_segment(db, call_id, idx, stage, bucket, score):
    """Helper: stamp a CallSegment row so derive_compliance routes to the
    segments path."""
    db.add(CallSegment(
        call_id=call_id,
        idx=idx,
        stage=stage,
        bucket=bucket,
        score=score,
        compliant=(bucket == "pass"),
        compliance_status=_bucket_to_status(bucket),
    ))


def test_segments_path_preserves_coaching_as_compliant(test_db):
    """1-segment call with bucket=coaching (e.g. 21/26 medium issues only).

    _step_score sets compliance_status="compliant" and call.compliant=False
    (worst_bucket != "pass"). derive_compliance MUST NOT overwrite this
    with the V1 rule that demotes any non-pass checkpoint to non_compliant.

    Real production case 24e184ee: 1 medium-severity partial demoted the
    whole coaching-bucket call to non_compliant pre-fix.
    """
    c = Call(
        id="c_seg_coaching", filename="x.mp3", file_path="c1/x.mp3",
        transcript="...", duration_seconds=120,
        compliance_status="compliant",  # set by _step_score
        compliant=False,
        score="21/26",
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "pass", "confidence": "high", "severity": "medium"},
            {"id": "cp_2", "status": "partial", "confidence": "high", "severity": "medium"},
            {"id": "cp_3", "status": "fail", "confidence": "high", "severity": "medium"},
        ]),
    )
    test_db.add(c)
    test_db.flush()
    _add_segment(test_db, "c_seg_coaching", 0, "verbal", "coaching", "21/26")
    test_db.commit()

    result = derive_compliance(c, test_db)
    assert result == "compliant"
    assert c.compliance_status == "compliant"
    # Audit row written for the bucket aggregator's verdict
    cd = test_db.query(ComplianceDecision).filter_by(call_id="c_seg_coaching").one()
    assert cd.status == "compliant"
    assert c.compliance_source == "bucket_aggregator"


def test_segments_path_preserves_blocked_as_non_compliant(test_db):
    """Multi-segment call with worst_bucket=blocked → status stays non_compliant.

    Real production case 4c62d964: 3 segments (pre_sales=blocked,
    verbal=coaching, loa=review). _step_score correctly set
    compliance_status="non_compliant". derive_compliance MUST NOT revert
    this to "pending" just because some checkpoints had needs_review=True
    (V1 Rule 1 was overriding the correct verdict).
    """
    c = Call(
        id="c_seg_blocked", filename="x.mp3", file_path="c2/x.mp3",
        transcript="...", duration_seconds=570,
        compliance_status="non_compliant",  # set by _step_score
        compliant=False,
        score="69/125",
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "fail", "confidence": "high", "severity": "critical",
             "needs_review": True},  # would trip V1 Rule 1 → pending
            {"id": "cp_2", "status": "partial", "confidence": "low", "severity": "high"},
        ]),
    )
    test_db.add(c)
    test_db.flush()
    _add_segment(test_db, "c_seg_blocked", 0, "pre_sales", "blocked", "48/88")
    _add_segment(test_db, "c_seg_blocked", 1, "verbal", "coaching", "21/26")
    _add_segment(test_db, "c_seg_blocked", 2, "loa", "review", "0/11")
    test_db.commit()

    result = derive_compliance(c, test_db)
    assert result == "non_compliant"
    assert c.compliance_status == "non_compliant"
    cd = test_db.query(ComplianceDecision).filter_by(call_id="c_seg_blocked").one()
    assert cd.status == "non_compliant"
    # Failing checkpoints surfaced for audit
    assert cd.failing_checkpoints is not None
    failing = json.loads(cd.failing_checkpoints)
    assert "cp_1" in failing and "cp_2" in failing


def test_segments_path_preserves_pending_with_no_decision_row(test_db):
    """Segment in bucket=review keeps the call pending and writes NO
    ComplianceDecision row (matches V1 semantics — pending = awaiting
    human decision, no auto-decision recorded)."""
    c = Call(
        id="c_seg_pending", filename="x.mp3", file_path="c3/x.mp3",
        transcript="...", duration_seconds=60,
        compliance_status="pending",
        compliant=False,
        score="3/26",
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "fail", "confidence": "high", "severity": "medium"},
        ]),
    )
    test_db.add(c)
    test_db.flush()
    _add_segment(test_db, "c_seg_pending", 0, "verbal", "review", "3/26")
    test_db.commit()

    result = derive_compliance(c, test_db)
    assert result == "pending"
    assert c.compliance_status == "pending"
    assert test_db.query(ComplianceDecision).filter_by(call_id="c_seg_pending").count() == 0


def test_segments_path_recomputes_from_buckets_ignoring_stale_status(test_db):
    """Regression for the prod 2026-05-26 backfill: pre-fix
    derive_compliance had stamped call.compliance_status with the wrong
    value via V1 rules, so on the first repair pass we MUST recompute
    from CallSegment.bucket rather than trust the existing field.

    Setup mirrors prod call 4c62d964: 3 segments [pre_sales=blocked,
    verbal=coaching, loa=review]. The corrupt status on the Call row is
    "pending" (set by the old V1 path). The repaired derive_compliance
    must recompute worst_bucket=blocked → "non_compliant" and write it
    over the stale "pending".
    """
    c = Call(
        id="c_recompute", filename="x.mp3", file_path="cr/x.mp3",
        transcript="...", duration_seconds=570,
        compliance_status="pending",  # ← corrupt value from old V1 path
        compliant=False,
        score="69/125",
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "fail", "confidence": "high",
             "severity": "critical"},
        ]),
    )
    test_db.add(c)
    test_db.flush()
    _add_segment(test_db, "c_recompute", 0, "pre_sales", "blocked", "48/88")
    _add_segment(test_db, "c_recompute", 1, "verbal", "coaching", "21/26")
    _add_segment(test_db, "c_recompute", 2, "loa", "review", "0/11")
    test_db.commit()

    result = derive_compliance(c, test_db)
    # Recomputed from buckets, not preserved from the corrupt field.
    assert result == "non_compliant"
    assert c.compliance_status == "non_compliant"
    assert c.compliant is False  # only worst_bucket="pass" flips this True
    cd = test_db.query(ComplianceDecision).filter_by(call_id="c_recompute").one()
    assert cd.status == "non_compliant"


def test_segments_path_coaching_only_yields_compliant_with_compliant_false(test_db):
    """A coaching-only call (worst_bucket="coaching") sets
    compliance_status="compliant" (UI pill shows green) but
    call.compliant=False (strict /tracker Compliant tab excludes it
    so reviewers can still triage the medium issues)."""
    c = Call(
        id="c_coach_only", filename="x.mp3", file_path="cc/x.mp3",
        transcript="...", duration_seconds=120,
        compliance_status="non_compliant",  # ← corrupt: pre-fix demoted
        compliant=False,
        score="21/26",
    )
    test_db.add(c)
    test_db.flush()
    _add_segment(test_db, "c_coach_only", 0, "verbal", "coaching", "21/26")
    test_db.commit()

    result = derive_compliance(c, test_db)
    assert result == "compliant"
    assert c.compliance_status == "compliant"
    assert c.compliant is False  # strict: coaching ≠ clean pass


def test_rerun_keeps_single_is_current_decision(test_db):
    """Re-running derive_compliance on the same call must leave exactly
    one ComplianceDecision row with ``is_current=True``. Prior rows get
    their flag demoted to False by ``_write_decision_row``.

    Without this invariant, downstream queries that do
    ``.filter_by(is_current=True).first()`` would non-deterministically
    pick between duplicate live rows.
    """
    c = Call(
        id="c_rerun", filename="x.mp3", file_path="c1/x.mp3",
        transcript="...", duration_seconds=10,
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "fail", "confidence": "high"},
        ]),
    )
    test_db.add(c)
    test_db.commit()
    # First pass: V1 path writes a non_compliant decision row.
    derive_compliance(c, test_db)
    # Second pass: same data, same effective verdict.
    derive_compliance(c, test_db)
    rows = test_db.query(ComplianceDecision).filter_by(call_id="c_rerun").all()
    assert len(rows) == 2  # one demoted, one current
    current_rows = [r for r in rows if r.is_current]
    assert len(current_rows) == 1
    assert current_rows[0].status == "non_compliant"


def test_commit_false_defers_write_to_caller(test_db):
    """``commit=False`` lets a batch caller (e.g. the rederive backfill
    endpoint) accumulate multiple calls' verdicts into one outer
    transaction. derive_compliance still mutates the ORM object — the
    caller is responsible for the final commit."""
    c = Call(
        id="c_no_commit", filename="x.mp3", file_path="c1/x.mp3",
        transcript="...", duration_seconds=10,
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "pass", "confidence": "high"},
        ]),
    )
    test_db.add(c)
    test_db.commit()
    result = derive_compliance(c, test_db, commit=False)
    assert result == "compliant"
    assert c.compliance_status == "compliant"
    # Caller commits.
    test_db.commit()
    cd = test_db.query(ComplianceDecision).filter_by(call_id="c_no_commit").one()
    assert cd.status == "compliant"


def test_segments_path_does_not_demote_compliant_with_partials(test_db):
    """Regression for the inverted-verdict pattern: a call with a single
    coaching segment whose flat ``checkpoint_results`` contain partial /
    fail entries MUST NOT be downgraded. The bug was that V1 Rule 2
    (any non-pass → non_compliant) would clobber the bucket aggregator's
    correct ``compliant`` verdict every time the segment had even one
    medium-severity issue (which is the *defining* trait of the coaching
    bucket — by definition, coaching means medium issues only)."""
    c = Call(
        id="c_seg_partial", filename="x.mp3", file_path="c5/x.mp3",
        transcript="...", duration_seconds=120,
        compliance_status="compliant",
        compliant=False,
        score="23/26",
        checkpoint_results=json.dumps([
            {"id": "cp_1", "status": "pass", "confidence": "high", "severity": "medium"},
            {"id": "cp_2", "status": "partial", "confidence": "high", "severity": "medium"},
            {"id": "cp_3", "status": "fail", "confidence": "high", "severity": "medium"},
        ]),
    )
    test_db.add(c)
    test_db.flush()
    _add_segment(test_db, "c_seg_partial", 0, "verbal", "coaching", "23/26")
    test_db.commit()

    result = derive_compliance(c, test_db)
    # Stays compliant — segments path is authoritative; V1 demotion would
    # have flipped this to non_compliant.
    assert result == "compliant"
    assert c.compliance_status == "compliant"
