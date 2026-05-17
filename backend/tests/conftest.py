"""Shared test fixtures for the backend test suite.

Owns the test-only ES256 keypair used by every auth-dependent test. The
`auth()` fixture returns a callable that produces a `{Authorization: Bearer ...}`
header dict for a given reviewer id, and `mock_jwks` monkeypatches
`app.auth.PyJWKClient` to verify tokens against the test public key.

Why centralized: previously both conftest and `test_auth.py` each generated
their own keypair, which meant every file imported `cryptography` and paid the
key-gen cost again. Now one keypair is shared and re-exported for downstream
tests (see `test_auth.py` which imports `_make_jwt` from here).
"""
import os
import tempfile
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base


# ─── Test ES256 keypair (one instance reused across tests for speed) ────────
_test_priv = ec.generate_private_key(ec.SECP256R1())
_test_pub = _test_priv.public_key()
_priv_pem = _test_priv.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def _make_jwt(sub: str, exp_offset: int = 3600, aud: str = "authenticated") -> str:
    """Sign a JWT with the shared test ES256 private key."""
    return pyjwt.encode(
        {"sub": sub, "aud": aud, "exp": int(time.time()) + exp_offset},
        _priv_pem,
        algorithm="ES256",
        headers={"kid": "test-kid"},
    )


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def test_db():
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    os.unlink(path)


@pytest.fixture
def upload_dir(tmp_path):
    """Temporary upload directory."""
    d = tmp_path / "uploads"
    d.mkdir()
    return str(d)


@pytest.fixture
def auth():
    """Return a helper that builds an Authorization header for a reviewer id.

    Usage in tests:
        def test_foo(auth):
            r = client.post("/api/...", headers=auth("sarah"))
    """
    def _auth(reviewer_id: str) -> dict:
        return {"Authorization": f"Bearer {_make_jwt(reviewer_id)}"}
    return _auth


@pytest.fixture
def mock_jwks(monkeypatch):
    """Replace `app.auth.PyJWKClient` so tests verify with the test ES256 key."""
    from app import auth as auth_module

    class FakeSigningKey:
        def __init__(self, key):
            self.key = key

    class FakeJWKClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_signing_key_from_jwt(self, token):
            return FakeSigningKey(_test_pub)

    monkeypatch.setattr(auth_module, "PyJWKClient", FakeJWKClient)
    auth_module._jwks_client = None
    yield
    auth_module._jwks_client = None


@pytest.fixture
def no_dev_admin(monkeypatch):
    """Disable the DEV_ALL_ADMIN override so role-gated tests see the
    user's stored role (reviewer/lead/admin) rather than a forced `admin`.

    Wave 4 added a `DEV_ALL_ADMIN` flag in `app/auth.py:current_user` that
    rewrites every authenticated user's role to `admin` when set. Tests that
    exercise role rejection paths (403/forbidden) need this disabled.
    """
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture(autouse=True)
def _reset_dependency_overrides_after_test():
    """Aggressively clear ``app.dependency_overrides`` after every test.

    Many test files install ``app.dependency_overrides[get_db]`` to point
    at their own in-memory SQLite engine inside an autouse fixture but
    never tear it down. The earlier "snapshot + restore" implementation
    captured the override DURING setup (when the test file's autouse
    fixture had already installed it) and then "restored" the polluted
    state — so the get_db override leaked into subsequent test files
    that expected to hit the real Postgres (test_audit_coverage et al.).

    Aggressive clear is safe because every test file's own autouse
    ``clean_db`` (or equivalent) re-installs the override it needs on
    each test's setup phase. The few tests that want NO override (e.g.
    test_audit_coverage relies on real Postgres) get a clean slate."""
    yield
    from app.main import app as _app
    _app.dependency_overrides.clear()
    # ``profile_cache._PROFILE_CACHE`` is module-level and persists across
    # tests. Without invalidation, the first test to call
    # ``get_profile_names`` populates the cache from its private SQLite;
    # subsequent tests that re-seed Profile rows in their own SQLite see
    # the cache return stale data → e.g. test_queue leaderboard returns
    # IDs ("mo") instead of names ("Mo Ibrahim").
    try:
        from app.profile_cache import invalidate_profile_cache
        invalidate_profile_cache()
    except Exception:
        pass


@pytest.fixture
def seed_profiles(test_db):
    """Seed 4 test profiles (3 reviewers + 1 lead). Call from tests that need identity.

    If you change this seed list, also update seed_profiles_local in
    tests/test_claim.py (test_claim uses its own SQLite engine so it needs
    a session-bound variant).
    """
    from app.models import Profile

    test_db.add_all([
        Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",   role="reviewer", active=True),
        Profile(id="mo",    email="mo@test.local",    name="Mo Ibrahim",  role="reviewer", active=True),
        Profile(id="layla", email="layla@test.local", name="Layla Said",  role="reviewer", active=True),
        Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
    ])
    test_db.commit()


# ─── W3-T6 replay fixtures ──────────────────────────────────────────────────
import uuid as _uuid

from sqlalchemy.orm import Session as _Session

from app.database import SessionLocal as _SessionLocal


@pytest.fixture
def db_session_with_call_with_transcript() -> str:
    """Seed a Call row with non-null transcript/word_data/script_id so the
    replay endpoint accepts it. Yields the call_id; cleans up on teardown."""
    from app.models import Call as _Call, Script as _Script

    db: _Session = _SessionLocal()
    script_id = str(_uuid.uuid4())
    call_id = str(_uuid.uuid4())
    try:
        script = _Script(
            id=script_id,
            supplier_name="t-supplier",
            script_name="t-script",
            checkpoints="[]",
        )
        db.add(script)
        db.flush()
        call = _Call(
            id=call_id,
            filename="y.mp3",
            file_path="x/y.mp3",
            customer_name="Test Reviewer",
            script_id=script.id,
            transcript="hello world",
            word_data='[{"word":"hello","start":0,"end":0.5}]',
            status="completed",
        )
        db.add(call)
        db.commit()
        yield call_id
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        # Best-effort teardown so reruns don't accumulate test rows.
        try:
            db.query(_Call).filter(_Call.id == call_id).delete()
            db.query(_Script).filter(_Script.id == script_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()


@pytest.fixture
def db_session_with_call_no_transcript() -> str:
    """Seed a Call row that lacks transcript/word_data so the replay
    endpoint must return 422. Yields the call_id; cleans up on teardown."""
    from app.models import Call as _Call, Script as _Script

    db: _Session = _SessionLocal()
    script_id = str(_uuid.uuid4())
    call_id = str(_uuid.uuid4())
    try:
        script = _Script(
            id=script_id,
            supplier_name="t-supplier-empty",
            script_name="t-script-empty",
            checkpoints="[]",
        )
        db.add(script)
        db.flush()
        call = _Call(
            id=call_id,
            filename="y.mp3",
            file_path="x/y.mp3",
            customer_name="Test Reviewer",
            script_id=script.id,
            transcript=None,
            word_data=None,
            status="uploaded",
        )
        db.add(call)
        db.commit()
        yield call_id
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        try:
            db.query(_Call).filter(_Call.id == call_id).delete()
            db.query(_Script).filter(_Script.id == script_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()
