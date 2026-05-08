"""W4.3 — GET /api/scripts/{id}/lines test suite.

Mirrors test_rejections.py setup: in-memory SQLite + StaticPool, autouse
clean_db, shared mock_jwks/auth fixtures from conftest.py.

The migration ``c2e5g8h3i4j5_w4_script_line_mappings`` self-seeds 15 rows
on Postgres. Tests run on a fresh in-memory SQLite each time, so the
clean_db fixture re-seeds those 15 rows manually.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Profile, Script, ScriptLineMapping

# Re-use the same 15-row seed as the migration so tests stay in sync.
# alembic.versions isn't a package, so load the file directly.
import importlib.util as _ilu
import pathlib as _pl

_mig_path = _pl.Path(__file__).parent.parent / "alembic" / "versions" / "c2e5g8h3i4j5_w4_script_line_mappings.py"
_spec = _ilu.spec_from_file_location("_w4_seed_mig", _mig_path)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
SEED_ROWS = _mod.SEED_ROWS


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
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    # Re-seed the 15 mapping rows so tests reflect production state.
    db = TestSessionLocal()
    try:
        for supplier, section, line_no, name, key in SEED_ROWS:
            db.add(ScriptLineMapping(
                id=uuid.uuid4(),
                supplier=supplier,
                script_section=section,
                line_number=line_no,
                checkpoint_name=name,
                internal_key=key,
            ))
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def seed_profile():
    db = TestSessionLocal()
    try:
        db.add(Profile(
            id="sarah", email="sarah@test.local", name="Sarah Ali",
            role="reviewer", active=True,
        ))
        db.commit()
    finally:
        db.close()


def _make_script(supplier_name: str, script_name: str, checkpoints: list[dict]) -> str:
    """Insert a Script row directly (avoids needing auth on POST /api/scripts)."""
    db = TestSessionLocal()
    try:
        sid = str(uuid.uuid4())
        db.add(Script(
            id=sid,
            supplier_name=supplier_name,
            script_name=script_name,
            version="1.0",
            mode="meaning_for_meaning",
            checkpoints=json.dumps(checkpoints),
            active=True,
        ))
        db.commit()
        return sid
    finally:
        db.close()


# ─── 1. Auth required ───────────────────────────────────────────────────


def test_lines_endpoint_requires_auth(mock_jwks, seed_profile):
    sid = _make_script("E.ON", "EON Verbal", [])
    r = client.get(f"/api/scripts/{sid}/lines")
    assert r.status_code == 401, r.text


# ─── 2. Empty script returns just the rendered header lines ─────────────


def test_lines_endpoint_empty_script_returns_header_only(mock_jwks, seed_profile, auth):
    sid = _make_script("Acme Energy", "Standard Script", [])
    r = client.get(f"/api/scripts/{sid}/lines", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()

    # `checkpoints_to_markdown` always emits a header (title + mode + intro
    # + ---). We assert: list is non-empty, every row has line_number=int,
    # text=str, and no mapping joins (Acme Energy has zero seeded rows).
    assert len(body) > 0
    for row in body:
        assert isinstance(row["line_number"], int)
        assert isinstance(row["text"], str)
        assert row["checkpoint_name"] is None
        assert row["internal_key"] is None


# ─── 3. Known supplier returns numbered + mapped lines ──────────────────


def test_lines_endpoint_known_supplier_joins_mappings(mock_jwks, seed_profile, auth):
    """E.ON + 'EON Verbal' has 6 line-numbered seed rows (lines 11,12,13,14,17,20).

    We pad the script with 25 numbered "filler" checkpoints so the
    rendered markdown has > 20 lines — guaranteeing line 20 exists in
    the output.
    """
    checkpoints = [
        {
            "section": i,
            "name": f"Filler {i}",
            "required": "x",
            "key_phrases": [],
            "customer_response_required": False,
            "strictness": "mandatory",
        }
        for i in range(1, 30)
    ]
    sid = _make_script("E.ON", "EON Verbal", checkpoints)

    r = client.get(f"/api/scripts/{sid}/lines", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()

    by_line = {row["line_number"]: row for row in body if row["line_number"] is not None}

    # Mandatory: line 17, 20 in seed are EON Verbal
    assert by_line[17]["internal_key"] == "eon_verbal_l17_vat_ccl_disclosure"
    assert by_line[17]["checkpoint_name"] == "Prices EXCLUDE VAT, CCL, Green Deal"
    assert by_line[20]["internal_key"] == "eon_verbal_l20_microbusiness_status"

    # Lines without a seed mapping have null checkpoint fields
    assert by_line[1]["checkpoint_name"] is None
    assert by_line[1]["internal_key"] is None


# ─── 4. Unknown supplier returns numbered lines without mappings ────────


def test_lines_endpoint_unknown_supplier_no_mappings(mock_jwks, seed_profile, auth):
    sid = _make_script("Wholly Made Up Supplier", "Random Script", [
        {
            "section": 1, "name": "Test", "required": "x",
            "key_phrases": [], "customer_response_required": False,
            "strictness": "mandatory",
        }
    ])
    r = client.get(f"/api/scripts/{sid}/lines", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()

    # Lines exist but every mapping field is null
    assert len(body) > 0
    assert all(row["checkpoint_name"] is None for row in body)
    assert all(row["internal_key"] is None for row in body)
    # And no line_number=null tail rows (no section-wide mappings either)
    assert all(row["line_number"] is not None for row in body)


# ─── 5. Section-wide mappings (line_number=null) appended as tail rows ──


def test_lines_endpoint_section_wide_mappings_appended(mock_jwks, seed_profile, auth):
    """E.ON + 'LOA' has 6 section-wide rows (line_number IS NULL) plus
    one line-5 row. The endpoint should append the 6 NULL-line rows after
    the numbered lines."""
    sid = _make_script("E.ON", "LOA", [
        {
            "section": 1, "name": "Intro", "required": "x",
            "key_phrases": [], "customer_response_required": False,
            "strictness": "mandatory",
        }
    ])
    r = client.get(f"/api/scripts/{sid}/lines", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()

    tail = [row for row in body if row["line_number"] is None]
    # 6 LOA section-wide mappings (broker_independence, company_name_match,
    # charity_number, company_number, dob, industry_database)
    assert len(tail) == 6
    keys = {row["internal_key"] for row in tail}
    assert "loa_dob_confirmation" in keys
    assert "broker_independence_disclaimer" in keys


# ─── 6. 404 on unknown script id ───────────────────────────────────────


def test_lines_endpoint_404_for_unknown_script(mock_jwks, seed_profile, auth):
    r = client.get("/api/scripts/nonexistent-id/lines", headers=auth("sarah"))
    assert r.status_code == 404
