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
