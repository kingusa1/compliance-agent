import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Call, CallCheckpoint
from app.reviewers import current_reviewer
from app.routes import router

# Setup test app with in-memory SQLite using StaticPool so all connections share same DB
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)
TestSessionLocal = sessionmaker(bind=engine)

app = FastAPI()
app.include_router(router)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
# 2026-05-14 audit added `Depends(current_reviewer)` to /retry + several
# other write endpoints. Tests don't pass a Bearer token, so the dep
# would 401 every request before the route logic ran. Override it to
# return a fake admin so the tests assert against the real response
# code (404 / 400 / 200) instead of the auth gate's 401.
app.dependency_overrides[current_reviewer] = lambda: {
    "id": "test-reviewer",
    "email": "test@compliance-agent.local",
    "role": "admin",
}
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # 2026-05-27 wave-15 — bust the /api/stats TTL cache so each test
    # gets a fresh DB read (the cache is module-level and would otherwise
    # leak state across tests).
    from app.routes import _STATS_CACHE
    _STATS_CACHE.clear()
    yield


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_get_calls_empty():
    response = client.get("/api/calls")
    assert response.status_code == 200
    data = response.json()
    assert data["calls"] == []
    assert data["total"] == 0


def test_get_stats_empty():
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_calls"] == 0
    assert data["compliance_rate"] == 0.0


def test_get_call_not_found():
    response = client.get("/api/calls/nonexistent")
    assert response.status_code == 404


def test_get_stats_with_data():
    db = TestSessionLocal()
    for i in range(5):
        call = Call(
            id=str(uuid.uuid4()),
            filename=f"call_{i}.mp3",
            file_path=f"/uploads/call_{i}.mp3",
            file_size=1024,
            status="completed",
            compliant=(i < 3),
            reason="test" if i >= 3 else None,
        )
        db.add(call)
    db.commit()
    db.close()

    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_calls"] == 5
    assert data["compliant_count"] == 3
    assert data["non_compliant_count"] == 2
    assert data["compliance_rate"] == 60.0


# 2026-05-27 wave-15 (perf P0) regression tests --------------------------

def test_get_stats_cache_hit_returns_stale_within_ttl():
    """Wave-15: /api/stats caches its result for ~10s. A second call BEFORE
    the TTL expires should return the same payload even if rows were
    inserted in between — proves the cache is wired."""
    # First call → populates cache with empty state
    r1 = client.get("/api/stats")
    assert r1.status_code == 200
    assert r1.json()["total_calls"] == 0

    # Insert a row directly while the cache is warm
    db = TestSessionLocal()
    db.add(Call(
        id=str(uuid.uuid4()),
        filename="cache_test.mp3",
        file_path="/uploads/cache_test.mp3",
        file_size=1024,
        status="completed",
        compliant=True,
    ))
    db.commit()
    db.close()

    # Second call within TTL → SHOULD still return cached empty state.
    # If the cache wasn't wired, this would return total_calls=1 and the
    # test would fail.
    r2 = client.get("/api/stats")
    assert r2.status_code == 200
    assert r2.json()["total_calls"] == 0, (
        "TTL cache hit failed — /api/stats should return stale cached value "
        "within the 10s window"
    )

    # Force-clear the cache and re-call — now should see the new row.
    from app.routes import _STATS_CACHE
    _STATS_CACHE.clear()
    r3 = client.get("/api/stats")
    assert r3.status_code == 200
    assert r3.json()["total_calls"] == 1, "post-clear call should see fresh data"


def test_get_stats_single_round_trip_consolidation():
    """Wave-15: the 7 sequential COUNT queries were collapsed into TWO
    multi-aggregate queries (one over calls, one over call_checkpoints).
    Verify by counting the SQL statements emitted for one /api/stats call.
    This locks the contract against a regression that re-splits the
    aggregate into per-status COUNT(*) queries."""
    from sqlalchemy import event

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        # Skip SQLAlchemy's own connection-init pings + transaction frames.
        sql = statement.strip().lower()
        if sql.startswith("select"):
            statements.append(sql)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        # Cache may be warm from prior test; force a real DB hit.
        from app.routes import _STATS_CACHE
        _STATS_CACHE.clear()
        r = client.get("/api/stats")
        assert r.status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # Wave-15 contract: stats endpoint issues AT MOST 2 SELECT statements
    # (one over calls, one over call_checkpoints). The prior implementation
    # issued 7. We assert ≤3 to allow one defensive extra (e.g. a future
    # JOIN or pre-flight check) without becoming brittle, but reject any
    # regression that goes back toward the original 7.
    assert len(statements) <= 3, (
        f"Wave-15 regression: /api/stats issued {len(statements)} SELECTs "
        f"(was 7 before, now expected ≤3): {statements}"
    )


def test_get_call_with_checkpoints():
    db = TestSessionLocal()
    call_id = str(uuid.uuid4())
    call = Call(
        id=call_id,
        filename="call.mp3",
        file_path="/uploads/call.mp3",
        file_size=1024,
        status="completed",
        compliant=True,
    )
    db.add(call)
    db.commit()

    # Use explicit IDs with predictable sort order so the relationship
    # (ordered by CallCheckpoint.id) returns cp1 before cp2.
    cp1 = CallCheckpoint(
        id="aaaaaaaa-0000-0000-0000-000000000001",
        call_id=call_id,
        rule_text="Agent states company is a third party",
        passed=True,
        excerpt="we are a third party",
    )
    cp2 = CallCheckpoint(
        id="aaaaaaaa-0000-0000-0000-000000000002",
        call_id=call_id,
        rule_text="Agent states NOT an energy supplier",
        passed=False,
        excerpt=None,
    )
    db.add_all([cp1, cp2])
    db.commit()
    db.close()

    response = client.get(f"/api/calls/{call_id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["checkpoints"]) == 2
    assert data["checkpoints"][0]["rule_text"] == "Agent states company is a third party"
    assert data["checkpoints"][0]["passed"] is True
    assert data["checkpoints"][0]["excerpt"] == "we are a third party"
    assert data["checkpoints"][1]["passed"] is False


def test_list_calls_summary_shape():
    """List endpoint returns lightweight CallSummary rows (no checkpoints,
    transcript, or word_data) — those are payload-bloating columns and must
    only be returned by /api/calls/{id}. See app/schemas.py:CallSummary."""
    db = TestSessionLocal()
    call_id = str(uuid.uuid4())
    call = Call(
        id=call_id,
        filename="call.mp3",
        file_path="/uploads/call.mp3",
        file_size=1024,
        status="completed",
    )
    db.add(call)
    db.commit()
    db.add(CallCheckpoint(
        call_id=call_id,
        rule_text="Test checkpoint",
        passed=True,
        excerpt="test evidence",
    ))
    db.commit()
    db.close()

    response = client.get("/api/calls")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    # CallSummary intentionally omits checkpoints to keep list payload small.
    assert "checkpoints" not in data["calls"][0]
    assert data["calls"][0]["id"] == call_id
    assert data["calls"][0]["filename"] == "call.mp3"


def test_upload_invalid_type(tmp_path):
    fake_file = tmp_path / "test.txt"
    fake_file.write_bytes(b"not audio")
    with open(str(fake_file), "rb") as f:
        response = client.post(
            "/api/calls/upload",
            files={"file": ("test.txt", f, "text/plain")},
        )
    assert response.status_code == 400
    # Wave-era message changed from "Invalid file type" to enumerate the
    # supported audio extensions (see SUPPORTED_AUDIO_EXTENSIONS).
    assert "Unsupported audio format" in response.json()["detail"]


# --- Retry endpoint tests ---

def test_retry_call_not_found():
    response = client.post("/api/calls/nonexistent-id/retry")
    assert response.status_code == 404


def test_retry_call_processing_recent_blocked():
    """Retry blocks an in-flight call when status='processing' and the row
    was created less than 5 minutes ago — protects against double-dispatch
    on the rapid re-click path. Older 'processing' rows (likely orphaned by
    a backend crash) are allowed to retry."""
    from datetime import datetime
    db = TestSessionLocal()
    call_id = str(uuid.uuid4())
    call = Call(
        id=call_id,
        filename="call.mp3",
        file_path="/uploads/call.mp3",
        file_size=1024,
        status="processing",
        created_at=datetime.utcnow(),
    )
    db.add(call)
    db.commit()
    db.close()

    response = client.post(f"/api/calls/{call_id}/retry")
    assert response.status_code == 400
    assert "already processing" in response.json()["detail"]


def test_retry_call_resets_state_and_clears_checkpoints():
    db = TestSessionLocal()
    call_id = str(uuid.uuid4())
    call = Call(
        id=call_id,
        filename="call.mp3",
        file_path="/uploads/call.mp3",
        file_size=1024,
        status="failed",
        compliant=False,
        reason="Something went wrong",
        checkpoint_results='[{"section": 1, "status": "fail"}]',
        score="0/3",
        transcript="Hello world transcript",
    )
    db.add(call)
    db.commit()

    cp = CallCheckpoint(
        call_id=call_id,
        rule_text="Agent states company is a third party",
        passed=False,
        excerpt=None,
    )
    db.add(cp)
    db.commit()
    db.close()

    response = client.post(f"/api/calls/{call_id}/retry")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "processing"
    assert data["compliant"] is None
    assert data["reason"] is None
    assert data["checkpoint_results"] is None
    assert data["score"] is None
    # Transcript should be preserved
    assert data["transcript"] == "Hello world transcript"
    # Checkpoints should be cleared
    assert data["checkpoints"] == []


def test_retry_call_with_error_status():
    db = TestSessionLocal()
    call_id = str(uuid.uuid4())
    call = Call(
        id=call_id,
        filename="call.mp3",
        file_path="/uploads/call.mp3",
        file_size=1024,
        status="error",
        reason="Connection timeout",
    )
    db.add(call)
    db.commit()
    db.close()

    response = client.post(f"/api/calls/{call_id}/retry")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert data["reason"] is None


# 2026-05-27 wave-17 — speaker-label backfill endpoint regression tests

class TestBackfillSpeakerLabels:
    """Wave-17: POST /api/admin/backfill-speaker-labels re-derives
    `Call.transcript` using the current `_detect_agent_speaker`
    heuristic (the post wave-16 logic). Contract:
      - idempotent (re-running on a clean dataset returns updated=0)
      - bounded by `limit` query param
      - optional `call_id` query param narrows scope to one row
      - audit-logged only on at-least-one update
    """

    def _setup_lead_override(self):
        """Install a stub lead-role user so require_lead passes the test."""
        from app.auth import require_lead

        _stub_lead = {
            "id": "test-lead",
            "email": "lead@compliance-agent.local",
            "role": "lead",
        }
        app.dependency_overrides[require_lead] = lambda: _stub_lead
        return _stub_lead

    def _teardown_lead_override(self):
        from app.auth import require_lead
        app.dependency_overrides.pop(require_lead, None)

    def _seed_call_with_old_labels(self, call_id: str, *, old_transcript: str,
                                    word_data: list[dict]) -> None:
        import json as _json
        db = TestSessionLocal()
        try:
            db.add(Call(
                id=call_id,
                filename=f"{call_id}.mp3",
                file_path=f"/uploads/{call_id}.mp3",
                file_size=1024,
                status="completed",
                transcript=old_transcript,
                word_data=_json.dumps(word_data),
            ))
            db.commit()
        finally:
            db.close()

    def test_backfill_updates_stale_transcript(self):
        """Seed a Call with a transcript that doesn't match what the
        current `format_diarized_transcript` would emit; verify the
        backfill rewrites it."""
        self._setup_lead_override()
        try:
            # Word data with two speakers — heuristic picks A as agent
            # (has the broker signals) but transcript field stores the
            # WRONG labels (manually corrupted to simulate the wave-16
            # bug carry-forward).
            call_id = str(uuid.uuid4())
            word_data = [
                {"word": "We", "speaker": "A", "start": 0.0, "end": 0.2},
                {"word": "are", "speaker": "A", "start": 0.2, "end": 0.4},
                {"word": "a", "speaker": "A", "start": 0.4, "end": 0.5},
                {"word": "third", "speaker": "A", "start": 0.5, "end": 0.7},
                {"word": "party", "speaker": "A", "start": 0.7, "end": 1.0},
                {"word": "broker", "speaker": "A", "start": 1.0, "end": 1.3},
                {"word": "calling", "speaker": "A", "start": 1.3, "end": 1.6},
                {"word": "from", "speaker": "A", "start": 1.6, "end": 1.8},
                {"word": "Watt", "speaker": "A", "start": 1.8, "end": 2.0},
                {"word": "Utilities", "speaker": "A", "start": 2.0, "end": 2.3},
                {"word": "Okay", "speaker": "B", "start": 3.0, "end": 3.3},
            ]
            self._seed_call_with_old_labels(
                call_id,
                old_transcript="[00:00] Customer: We are a third party broker",
                word_data=word_data,
            )

            response = client.post(
                f"/api/admin/backfill-speaker-labels?call_id={call_id}"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["updated"] == 1
            assert data["scanned"] == 1
            assert data["call_id"] == call_id

            # Verify the persisted transcript now matches the heuristic
            db = TestSessionLocal()
            try:
                row = db.query(Call).filter_by(id=call_id).first()
                # Speaker A scored on broker signals — should now be labeled Agent
                assert "Agent:" in row.transcript
                assert "We are a third party broker" in row.transcript
            finally:
                db.close()
        finally:
            self._teardown_lead_override()

    def test_backfill_is_idempotent(self):
        """Running the endpoint twice on the same call returns
        updated=0 on the second invocation."""
        self._setup_lead_override()
        try:
            call_id = str(uuid.uuid4())
            word_data = [
                {"word": "We", "speaker": "A", "start": 0.0, "end": 0.2},
                {"word": "are", "speaker": "A", "start": 0.2, "end": 0.4},
                {"word": "third", "speaker": "A", "start": 0.4, "end": 0.6},
                {"word": "party", "speaker": "A", "start": 0.6, "end": 0.8},
                {"word": "broker", "speaker": "A", "start": 0.8, "end": 1.1},
                {"word": "Hi", "speaker": "B", "start": 2.0, "end": 2.2},
            ]
            self._seed_call_with_old_labels(
                call_id,
                old_transcript="ignore me — first call will rewrite",
                word_data=word_data,
            )

            # First call: rewrites
            r1 = client.post(
                f"/api/admin/backfill-speaker-labels?call_id={call_id}"
            )
            assert r1.status_code == 200
            assert r1.json()["updated"] == 1

            # Second call: no-op (idempotent — transcript already matches)
            r2 = client.post(
                f"/api/admin/backfill-speaker-labels?call_id={call_id}"
            )
            assert r2.status_code == 200
            assert r2.json()["updated"] == 0, (
                f"Wave-17 regression: second invocation should be a no-op. "
                f"Got {r2.json()}"
            )
            assert r2.json()["skipped_unchanged"] == 1
        finally:
            self._teardown_lead_override()

    def test_backfill_skips_calls_without_word_data(self):
        """A Call row with word_data=NULL should be silently skipped,
        not raise an exception."""
        self._setup_lead_override()
        try:
            call_id = str(uuid.uuid4())
            db = TestSessionLocal()
            try:
                db.add(Call(
                    id=call_id,
                    filename="no_words.mp3",
                    file_path="/uploads/no_words.mp3",
                    file_size=1024,
                    status="completed",
                    transcript="stale",
                    word_data=None,  # ← explicitly missing
                ))
                db.commit()
            finally:
                db.close()

            response = client.post(
                f"/api/admin/backfill-speaker-labels?call_id={call_id}"
            )
            # The query filter `word_data.isnot(None)` excludes it →
            # scanned=0, updated=0. No exception raised.
            assert response.status_code == 200
            data = response.json()
            assert data["scanned"] == 0
            assert data["updated"] == 0
        finally:
            self._teardown_lead_override()

    def test_backfill_handles_corrupt_word_data_gracefully(self):
        """A Call row with un-parseable word_data JSON should be
        counted in skipped_parse, not crash the batch."""
        self._setup_lead_override()
        try:
            call_id = str(uuid.uuid4())
            db = TestSessionLocal()
            try:
                db.add(Call(
                    id=call_id,
                    filename="corrupt.mp3",
                    file_path="/uploads/corrupt.mp3",
                    file_size=1024,
                    status="completed",
                    transcript="stale text",
                    word_data="{this is not valid json",  # ← corrupt
                ))
                db.commit()
            finally:
                db.close()

            response = client.post(
                f"/api/admin/backfill-speaker-labels?call_id={call_id}"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["skipped_parse"] == 1
            assert data["updated"] == 0
            # Verify the transcript was NOT modified
            db = TestSessionLocal()
            try:
                row = db.query(Call).filter_by(id=call_id).first()
                assert row.transcript == "stale text"
            finally:
                db.close()
        finally:
            self._teardown_lead_override()

    def test_backfill_rejects_malformed_call_id_with_422(self):
        """Wave-17 v2 (security-reviewer MEDIUM) — call_id must be a
        valid UUID. Malformed input returns 422, not a 500 that could
        leak schema details if Call.id ever migrates to a UUID column."""
        self._setup_lead_override()
        try:
            response = client.post(
                "/api/admin/backfill-speaker-labels?call_id=not-a-uuid"
            )
            assert response.status_code == 422
            assert "uuid" in response.json()["detail"].lower()
        finally:
            self._teardown_lead_override()

    def test_backfill_respects_limit_parameter(self):
        """The `limit` query param caps the number of rows scanned.
        Clamped to [1, 5000]."""
        self._setup_lead_override()
        try:
            # Seed 3 calls, request limit=2 → only 2 scanned.
            import json as _json
            for i in range(3):
                db = TestSessionLocal()
                try:
                    db.add(Call(
                        id=str(uuid.uuid4()),
                        filename=f"limit_{i}.mp3",
                        file_path=f"/uploads/limit_{i}.mp3",
                        file_size=1024,
                        status="completed",
                        transcript=f"stale_{i}",
                        word_data=_json.dumps([
                            {"word": "broker", "speaker": "A",
                             "start": 0.0, "end": 0.3},
                            {"word": "ok", "speaker": "B",
                             "start": 1.0, "end": 1.2},
                        ]),
                    ))
                    db.commit()
                finally:
                    db.close()

            response = client.post(
                "/api/admin/backfill-speaker-labels?limit=2"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["scanned"] == 2, (
                f"Wave-17 regression: limit=2 should cap scanned to 2; "
                f"got {data['scanned']}"
            )
        finally:
            self._teardown_lead_override()
