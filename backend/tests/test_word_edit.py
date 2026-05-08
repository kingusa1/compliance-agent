"""Tests for POST /api/calls/{id}/edit-word.

Covers: word_data is mutated in place and a TranscriptEdit row is written,
single-checkpoint re-analysis flips the verdict (writes a new AI VerdictHistory
row with prior is_current demoted), agreement between old and new verdict
skips the history row, omitting checkpoint_id skips reanalysis entirely, a Call
without a linked Script skips reanalysis, unknown call → 404, out-of-range
word_index → 400, missing auth → 401.

Setup mirrors test_verdict.py / test_history.py: dedicated in-memory SQLite +
StaticPool, override `get_db` inside the autouse clean_db fixture so collection
order doesn't matter.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Profile, Script, TranscriptEdit, VerdictHistory


# Dedicated in-memory SQLite — see test_verdict.py for the StaticPool reasoning.
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


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    """Truncate all tables between tests + re-assert this file's get_db override.

    See test_verdict.py's clean_db fixture for the cross-file override rationale.
    """
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def seed_profiles_local():
    """Seed 4 profiles into the test SQLite. Keep in sync with
    conftest.seed_profiles."""
    db = TestSessionLocal()
    try:
        db.add_all([
            Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",   role="reviewer", active=True),
            Profile(id="mo",    email="mo@test.local",    name="Mo Ibrahim",  role="reviewer", active=True),
            Profile(id="layla", email="layla@test.local", name="Layla Said",  role="reviewer", active=True),
            Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
        ])
        db.commit()
    finally:
        db.close()


# Words spanning both speakers. Index 1 ("yeah") is the word most tests edit.
WORDS = [
    {"word": "hi",   "start": 0,    "end": 200,  "confidence": 0.95, "speaker": "agent"},
    {"word": "yeah", "start": 300,  "end": 500,  "confidence": 0.72, "speaker": "customer"},
    {"word": "sure", "start": 600,  "end": 800,  "confidence": 0.91, "speaker": "customer"},
]

# Script-side checkpoint definition (the re-analysis input). Note `id="cp_1"`
# so the lookup path matches both the explicit id AND the cp_{i} fallback.
SCRIPT_CHECKPOINTS = [
    {
        "id": "cp_1",
        "name": "Confirm consent",
        "required_text": "do you agree",
        "key_phrases": ["agree", "yes"],
        "mode": "meaning_for_meaning",
    },
]

# Stored AI result: originally "fail". Reanalysis in most tests flips this to "pass".
CALL_CHECKPOINT_RESULTS = [
    {
        "id": "cp_1",
        "name": "Confirm consent",
        "status": "fail",
        "verdict": "fail",
        "confidence": 0.55,
        "reasoning": "unclear",
        "evidence": "yeah sure",
    },
]


@pytest.fixture
def seed_call_with_script():
    """Seed a Call + linked Script row so re-analysis has a script to load from."""
    db = TestSessionLocal()
    try:
        db.add(Script(
            id="s1",
            supplier_name="Test",
            script_name="Test",
            mode="meaning_for_meaning",
            checkpoints=json.dumps(SCRIPT_CHECKPOINTS),
            active=True,
        ))
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="hi yeah sure",
            duration_seconds=10,
            word_data=json.dumps(WORDS),
            checkpoint_results=json.dumps(CALL_CHECKPOINT_RESULTS),
            script_id="s1",
            detected_supplier="Test",
        ))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seed_call_without_script():
    """Seed a Call with NO script_id → reanalysis should short-circuit."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="hi yeah sure",
            duration_seconds=10,
            word_data=json.dumps(WORDS),
            checkpoint_results=json.dumps(CALL_CHECKPOINT_RESULTS),
            detected_supplier="Test",
        ))
        db.commit()
    finally:
        db.close()


def _claim(user: str, auth) -> str:
    """Helper: POST /claim for the given reviewer, return the review_session_id."""
    r = client.post("/api/calls/c1/claim", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()["review_session_id"]


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_word_edit_stores_edit_and_patches_word_data(
    mock_jwks, seed_profiles_local, seed_call_with_script, auth
):
    """Happy path: POST edit-word patches WORDS[index]['word'] and writes one
    TranscriptEdit row with triggered_reanalysis=True."""
    _claim("sarah", auth)

    mock_result = {
        "results": [{
            "id": "cp_1",
            "status": "pass",
            "verdict": "pass",
            "confidence": 0.88,
            "reasoning": "now clear",
        }],
    }

    with patch(
        "app.hitl_routes.analyze_all_checkpoints",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        r = client.post(
            "/api/calls/c1/edit-word",
            headers=auth("sarah"),
            json={
                "word_index": 1,
                "old_text": "yeah",
                "new_text": "yes",
                "checkpoint_id": "cp_1",
            },
        )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        words = json.loads(call.word_data)
        assert words[1]["word"] == "yes"

        edits = db.query(TranscriptEdit).filter_by(call_id="c1").all()
        assert len(edits) == 1
        e = edits[0]
        assert e.old_text == "yeah"
        assert e.new_text == "yes"
        assert e.word_index == 1
        assert e.triggered_checkpoint_id == "cp_1"
        assert e.triggered_reanalysis is True
    finally:
        db.close()


def test_word_edit_stores_start_time_in_milliseconds(
    mock_jwks, seed_profiles_local, auth
):
    """bug_010 regression: word.start (seconds, fractional) must be stored
    as milliseconds in TranscriptEdit.word_start_ms, not raw seconds."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="hi there",
            duration_seconds=60,
            word_data=json.dumps([
                {"word": "hi", "start": 0.5, "end": 0.8, "speaker": "agent"},
                {"word": "there", "start": 12.345, "end": 12.9, "speaker": "agent"},
            ]),
            checkpoint_results="[]",
            detected_supplier="Test",
        ))
        db.commit()
    finally:
        db.close()

    _claim("sarah", auth)
    r = client.post(
        "/api/calls/c1/edit-word",
        headers=auth("sarah"),
        json={"word_index": 1, "old_text": "there", "new_text": "here"},
    )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        edit = db.query(TranscriptEdit).filter_by(call_id="c1").one()
        # 12.345 seconds → 12345 milliseconds (not 12)
        assert edit.word_start_ms == 12345, (
            f"expected 12345ms, got {edit.word_start_ms}"
        )
    finally:
        db.close()


def test_word_edit_reanalysis_changes_verdict(
    mock_jwks, seed_profiles_local, seed_call_with_script, auth
):
    """Prior verdict was fail, reanalysis returns pass → verdict_changed=True,
    call.checkpoint_results[0].verdict=='pass', and a new AI VerdictHistory row
    lands with is_current=True."""
    _claim("sarah", auth)

    mock_result = {
        "results": [{
            "id": "cp_1",
            "status": "pass",
            "verdict": "pass",
            "confidence": 0.88,
            "reasoning": "now clear",
        }],
    }

    with patch(
        "app.hitl_routes.analyze_all_checkpoints",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        r = client.post(
            "/api/calls/c1/edit-word",
            headers=auth("sarah"),
            json={
                "word_index": 1,
                "old_text": "yeah",
                "new_text": "yes",
                "checkpoint_id": "cp_1",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict_changed"] is True
    assert body["new_verdict"] == "pass"

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        cps = json.loads(call.checkpoint_results)
        assert cps[0]["verdict"] == "pass"

        ai_rows = (
            db.query(VerdictHistory)
            .filter_by(call_id="c1", checkpoint_id="cp_1", actor_type="ai")
            .all()
        )
        assert len(ai_rows) == 1
        assert ai_rows[0].is_current is True
        assert ai_rows[0].verdict == "pass"
    finally:
        db.close()


def test_word_edit_reanalysis_same_verdict_no_history_row(
    mock_jwks, seed_profiles_local, seed_call_with_script, auth
):
    """Reanalysis returns the same 'fail' verdict → verdict_changed=False and
    NO new VerdictHistory rows are written (reviewer didn't invoke /verdict,
    so the table stays empty)."""
    _claim("sarah", auth)

    mock_result = {
        "results": [{
            "id": "cp_1",
            "status": "fail",
            "verdict": "fail",
            "confidence": 0.55,
            "reasoning": "still unclear",
        }],
    }

    with patch(
        "app.hitl_routes.analyze_all_checkpoints",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        r = client.post(
            "/api/calls/c1/edit-word",
            headers=auth("sarah"),
            json={
                "word_index": 1,
                "old_text": "yeah",
                "new_text": "yes",
                "checkpoint_id": "cp_1",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["verdict_changed"] is False

    db = TestSessionLocal()
    try:
        rows = db.query(VerdictHistory).filter_by(call_id="c1").all()
        assert len(rows) == 0
    finally:
        db.close()


def test_word_edit_without_checkpoint_id_skips_reanalysis(
    mock_jwks, seed_profiles_local, seed_call_with_script, auth
):
    """No checkpoint_id in payload → analyze_all_checkpoints isn't called and
    triggered_reanalysis=False. Still persists the word change + edit row."""
    _claim("sarah", auth)

    with patch(
        "app.hitl_routes.analyze_all_checkpoints",
        new_callable=AsyncMock,
    ) as mock_analyze:
        r = client.post(
            "/api/calls/c1/edit-word",
            headers=auth("sarah"),
            json={
                "word_index": 1,
                "old_text": "yeah",
                "new_text": "yes",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["verdict_changed"] is False
    mock_analyze.assert_not_called()

    db = TestSessionLocal()
    try:
        edit = db.query(TranscriptEdit).filter_by(call_id="c1").one()
        assert edit.triggered_reanalysis is False
    finally:
        db.close()


def test_word_edit_without_script_skips_reanalysis(
    mock_jwks, seed_profiles_local, seed_call_without_script, auth
):
    """Call has no script_id → reanalysis can't run → skipped gracefully.
    Edit is still saved, response reports verdict_changed=False, mock not called."""
    _claim("sarah", auth)

    with patch(
        "app.hitl_routes.analyze_all_checkpoints",
        new_callable=AsyncMock,
    ) as mock_analyze:
        r = client.post(
            "/api/calls/c1/edit-word",
            headers=auth("sarah"),
            json={
                "word_index": 1,
                "old_text": "yeah",
                "new_text": "yes",
                "checkpoint_id": "cp_1",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["verdict_changed"] is False
    mock_analyze.assert_not_called()

    db = TestSessionLocal()
    try:
        edits = db.query(TranscriptEdit).filter_by(call_id="c1").all()
        assert len(edits) == 1
    finally:
        db.close()


def test_word_edit_unknown_call_returns_404(
    mock_jwks, seed_profiles_local, auth
):
    r = client.post(
        "/api/calls/nope/edit-word",
        headers=auth("sarah"),
        json={"word_index": 0, "old_text": "x", "new_text": "y"},
    )
    assert r.status_code == 404


def test_word_edit_out_of_range_index_returns_400(
    mock_jwks, seed_profiles_local, seed_call_with_script, auth
):
    r = client.post(
        "/api/calls/c1/edit-word",
        headers=auth("sarah"),
        json={"word_index": 99, "old_text": "x", "new_text": "y"},
    )
    assert r.status_code == 400


def test_word_edit_without_auth_returns_401(seed_profiles_local, seed_call_with_script):
    r = client.post(
        "/api/calls/c1/edit-word",
        json={"word_index": 1, "old_text": "yeah", "new_text": "yes"},
    )
    assert r.status_code == 401
