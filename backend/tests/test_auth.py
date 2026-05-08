"""Tests for Supabase Auth JWT verification.

The ES256 keypair + `_make_jwt` helper live in `conftest.py` so every
auth-dependent test shares one keypair. `mock_jwks` (also in conftest)
monkeypatches `app.auth.PyJWKClient` to verify with the test public key —
no real Supabase JWKS endpoint is hit.
"""
import pytest
from fastapi import HTTPException

from app import auth
from tests.conftest import _make_jwt


@pytest.fixture(autouse=True)
def reset_jwks_client():
    auth._jwks_client = None
    yield
    auth._jwks_client = None


def test_verify_jwt_rejects_missing_header(mock_jwks):
    with pytest.raises(HTTPException) as exc:
        auth.verify_jwt(authorization=None)
    assert exc.value.status_code == 401


def test_verify_jwt_rejects_non_bearer(mock_jwks):
    with pytest.raises(HTTPException) as exc:
        auth.verify_jwt(authorization="Basic abc")
    assert exc.value.status_code == 401


def test_verify_jwt_accepts_valid_token(mock_jwks):
    token = _make_jwt("u-sarah")
    payload = auth.verify_jwt(authorization=f"Bearer {token}")
    assert payload["sub"] == "u-sarah"
    assert payload["aud"] == "authenticated"


def test_verify_jwt_rejects_expired(mock_jwks):
    token = _make_jwt("u-sarah", exp_offset=-10)
    with pytest.raises(HTTPException) as exc:
        auth.verify_jwt(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


def test_verify_jwt_rejects_wrong_audience(mock_jwks):
    token = _make_jwt("u-sarah", aud="wrong-audience")
    with pytest.raises(HTTPException) as exc:
        auth.verify_jwt(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


# ─── Integration tests for current_user + require_lead ──────────────────

def test_current_user_returns_profile_fields(mock_jwks, test_db, no_dev_admin):
    from app.models import Profile
    from app.auth import current_user
    db = test_db
    db.add(Profile(id="u-alice", email="alice@x.com", name="Alice", role="reviewer", active=True))
    db.commit()
    token_payload = {"sub": "u-alice", "aud": "authenticated", "exp": 99999999999}
    user = current_user(payload=token_payload, db=db)
    assert user["id"] == "u-alice"
    assert user["email"] == "alice@x.com"
    assert user["role"] == "reviewer"


def test_current_user_rejects_missing_profile(mock_jwks, test_db):
    from app.auth import current_user
    with pytest.raises(HTTPException) as exc:
        current_user(payload={"sub": "u-nobody"}, db=test_db)
    assert exc.value.status_code == 401


def test_current_user_rejects_inactive_profile(mock_jwks, test_db):
    from app.models import Profile
    from app.auth import current_user
    db = test_db
    db.add(Profile(id="u-bob", email="bob@x.com", name="Bob", role="reviewer", active=False))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        current_user(payload={"sub": "u-bob"}, db=db)
    assert exc.value.status_code == 401


def test_require_lead_accepts_lead(mock_jwks):
    from app.auth import require_lead
    user = {"id": "u", "email": "x@y", "name": "X", "role": "lead"}
    assert require_lead(user=user) == user


def test_require_lead_accepts_admin(mock_jwks):
    from app.auth import require_lead
    user = {"id": "u", "email": "x@y", "name": "X", "role": "admin"}
    assert require_lead(user=user) == user


def test_require_lead_rejects_reviewer(mock_jwks):
    from app.auth import require_lead
    user = {"id": "u", "email": "x@y", "name": "X", "role": "reviewer"}
    with pytest.raises(HTTPException) as exc:
        require_lead(user=user)
    assert exc.value.status_code == 403
