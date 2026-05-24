import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import current_user
from app.database import Base, get_db
from app.models import Script, ScriptVersion
from app.reviewers import current_reviewer, require_lead
from app.script_routes import script_router

# ---------------------------------------------------------------------------
# In-memory SQLite test app
# ---------------------------------------------------------------------------

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)
TestSessionLocal = sessionmaker(bind=engine)

app = FastAPI()
app.include_router(script_router)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
# 2026-05-24 — script_routes mutations now require_lead; GETs current_reviewer.
_STUB_LEAD = {
    "id": "test-lead",
    "email": "lead@compliance-agent.local",
    "name": "Test Lead",
    "role": "lead",
}
app.dependency_overrides[current_user] = lambda: _STUB_LEAD
app.dependency_overrides[current_reviewer] = lambda: _STUB_LEAD
app.dependency_overrides[require_lead] = lambda: _STUB_LEAD
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHECKPOINT_PAYLOAD = [
    {
        "section": 1,
        "name": "Intro",
        "required": "Agent introduces themselves",
        "key_phrases": ["hello", "welcome"],
        "customer_response_required": False,
        "strictness": "mandatory",
    }
]

SCRIPT_PAYLOAD = {
    "supplier_name": "Acme Energy",
    "script_name": "Standard Script",
    "version": "1.0",
    "mode": "meaning_for_meaning",
    "checkpoints": CHECKPOINT_PAYLOAD,
}


def create_script_via_api():
    resp = client.post("/api/scripts", json=SCRIPT_PAYLOAD)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests: create_script creates initial version
# ---------------------------------------------------------------------------

def test_create_script_creates_initial_version():
    script = create_script_via_api()
    script_id = script["id"]

    db = TestSessionLocal()
    versions = db.query(ScriptVersion).filter(ScriptVersion.script_id == script_id).all()
    db.close()

    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert versions[0].mode_snapshot == "meaning_for_meaning"
    parsed = json.loads(versions[0].checkpoints_snapshot)
    assert parsed[0]["name"] == "Intro"


def test_create_script_version_number_starts_at_1():
    script = create_script_via_api()
    script_id = script["id"]

    db = TestSessionLocal()
    v = db.query(ScriptVersion).filter(ScriptVersion.script_id == script_id).first()
    db.close()

    assert v.version_number == 1


# ---------------------------------------------------------------------------
# Tests: update_script with checkpoint changes creates new version
# ---------------------------------------------------------------------------

def test_update_script_checkpoints_creates_new_version():
    script = create_script_via_api()
    script_id = script["id"]

    updated_payload = {
        **SCRIPT_PAYLOAD,
        "checkpoints": [
            {
                **CHECKPOINT_PAYLOAD[0],
                "name": "Updated Intro",
            }
        ],
    }
    resp = client.put(f"/api/scripts/{script_id}", json=updated_payload)
    assert resp.status_code == 200

    db = TestSessionLocal()
    versions = (
        db.query(ScriptVersion)
        .filter(ScriptVersion.script_id == script_id)
        .order_by(ScriptVersion.version_number)
        .all()
    )
    db.close()

    # Should now have 2 versions: the initial + the one created on update
    assert len(versions) == 2
    assert versions[1].version_number == 2


def test_update_script_version_snapshots_old_checkpoints():
    """The new version row should contain the OLD checkpoints, not the new ones."""
    script = create_script_via_api()
    script_id = script["id"]

    updated_payload = {
        **SCRIPT_PAYLOAD,
        "checkpoints": [
            {
                **CHECKPOINT_PAYLOAD[0],
                "name": "Updated Intro",
            }
        ],
    }
    client.put(f"/api/scripts/{script_id}", json=updated_payload)

    db = TestSessionLocal()
    versions = (
        db.query(ScriptVersion)
        .filter(ScriptVersion.script_id == script_id)
        .order_by(ScriptVersion.version_number)
        .all()
    )
    db.close()

    # version 2 snapshots the old checkpoints (before the update)
    old_snapshot = json.loads(versions[1].checkpoints_snapshot)
    assert old_snapshot[0]["name"] == "Intro"


def test_update_script_mode_change_creates_new_version():
    script = create_script_via_api()
    script_id = script["id"]

    updated_payload = {**SCRIPT_PAYLOAD, "mode": "verbatim"}
    resp = client.put(f"/api/scripts/{script_id}", json=updated_payload)
    assert resp.status_code == 200

    db = TestSessionLocal()
    count = db.query(ScriptVersion).filter(ScriptVersion.script_id == script_id).count()
    db.close()

    assert count == 2


def test_update_script_no_change_does_not_create_version():
    """Updating only supplier_name/script_name (same checkpoints & mode) should NOT create a new version."""
    script = create_script_via_api()
    script_id = script["id"]

    # same checkpoints & mode, different supplier_name
    updated_payload = {**SCRIPT_PAYLOAD, "supplier_name": "New Supplier"}
    client.put(f"/api/scripts/{script_id}", json=updated_payload)

    db = TestSessionLocal()
    count = db.query(ScriptVersion).filter(ScriptVersion.script_id == script_id).count()
    db.close()

    # Still only the initial version
    assert count == 1


def test_multiple_updates_increment_version_number():
    script = create_script_via_api()
    script_id = script["id"]

    for i in range(3):
        payload = {
            **SCRIPT_PAYLOAD,
            "checkpoints": [
                {
                    **CHECKPOINT_PAYLOAD[0],
                    "name": f"Checkpoint v{i + 2}",
                }
            ],
        }
        resp = client.put(f"/api/scripts/{script_id}", json=payload)
        assert resp.status_code == 200

    db = TestSessionLocal()
    versions = (
        db.query(ScriptVersion)
        .filter(ScriptVersion.script_id == script_id)
        .order_by(ScriptVersion.version_number)
        .all()
    )
    db.close()

    # initial (1) + 3 updates = 4
    assert len(versions) == 4
    assert [v.version_number for v in versions] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Tests: GET /api/scripts/{id}/versions endpoint
# ---------------------------------------------------------------------------

def test_list_versions_endpoint_returns_initial_version():
    script = create_script_via_api()
    script_id = script["id"]

    resp = client.get(f"/api/scripts/{script_id}/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["versions"]) == 1
    assert data["versions"][0]["version_number"] == 1
    assert data["versions"][0]["script_id"] == script_id


def test_list_versions_endpoint_after_updates():
    script = create_script_via_api()
    script_id = script["id"]

    client.put(f"/api/scripts/{script_id}", json={**SCRIPT_PAYLOAD, "mode": "verbatim"})
    client.put(f"/api/scripts/{script_id}", json={**SCRIPT_PAYLOAD, "mode": "meaning_for_meaning"})

    resp = client.get(f"/api/scripts/{script_id}/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    version_numbers = [v["version_number"] for v in data["versions"]]
    assert version_numbers == [1, 2, 3]


def test_list_versions_endpoint_404_for_unknown_script():
    resp = client.get("/api/scripts/nonexistent-id/versions")
    assert resp.status_code == 404


def test_list_versions_includes_snapshot_fields():
    script = create_script_via_api()
    script_id = script["id"]

    resp = client.get(f"/api/scripts/{script_id}/versions")
    v = resp.json()["versions"][0]

    assert "checkpoints_snapshot" in v
    assert "mode_snapshot" in v
    assert "created_at" in v
    parsed = json.loads(v["checkpoints_snapshot"])
    assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# Tests: script_version_id on Call model
# ---------------------------------------------------------------------------

def test_call_model_has_script_version_id_column():
    """Verify the column exists in the DB schema."""
    from app.models import Call
    assert hasattr(Call, "script_version_id")


def test_call_can_reference_script_version():
    """A Call row can store a script_version_id FK value."""
    db = TestSessionLocal()

    # Create script & version via API
    script = create_script_via_api()
    script_id = script["id"]

    sv = db.query(ScriptVersion).filter(ScriptVersion.script_id == script_id).first()

    from app.models import Call
    call = Call(
        id=str(uuid.uuid4()),
        filename="test.mp3",
        file_path="/uploads/test.mp3",
        script_id=script_id,
        script_version_id=sv.id,
    )
    db.add(call)
    db.commit()

    fetched = db.query(Call).filter_by(id=call.id).first()
    assert fetched.script_version_id == sv.id
    db.close()
