"""Supabase Auth JWT verification.

Deviates from plan: uses JWKS asymmetric verification (ECC P-256) instead of
HS256 shared-secret. Our Supabase project's current signing key is asymmetric;
PyJWKClient fetches and caches the JWKS automatically and handles rotation.
"""
from __future__ import annotations

import jwt
from jwt import PyJWKClient, InvalidTokenError, ExpiredSignatureError
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Profile


# Cache a single JWKS client — it internally caches keys and refreshes on kid miss.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not settings.supabase_url:
            raise RuntimeError("SUPABASE_URL must be set for JWT verification")
        jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
    return _jwks_client


def verify_jwt(authorization: str | None = Header(default=None)) -> dict:
    """Verify a Supabase-issued JWT using JWKS. Returns the decoded claims."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            options={"verify_aud": True, "verify_exp": True},
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    return payload


def current_user(
    payload: dict = Depends(verify_jwt),
    db: Session = Depends(get_db),
) -> dict:
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="Token missing sub claim")
    # 2026-05-27 PERF — Use the module-level profile cache that's already
    # pre-loaded at FastAPI startup with a 5-minute TTL
    # (`app.profile_cache.refresh_profile_cache` in main.py:263). The
    # previous implementation did a fresh `db.query(Profile)` on EVERY
    # authenticated request; at Railway↔Supabase cross-region latency
    # (~205ms per RTT) this added ~200ms to every API call. Heavy admin
    # pages make 5-10 sequential authed requests on first paint, so the
    # cache wire-up shaves 1-2 seconds off perceived page load.
    #
    # On cache hit (the common case once startup pre-load fires): zero
    # DB work — the cache is an in-process dict keyed on profile.id.
    # On cache miss (new profile signed up since the last refresh): fall
    # through to the direct query so the new user isn't locked out for
    # up to 5 minutes.
    try:
        from app.profile_cache import get_profile_dict
        cached = get_profile_dict(db).get(uid)
    except Exception:  # noqa: BLE001 — cache layer must never block auth
        cached = None
    if cached and cached.get("is_active"):
        role = "admin" if settings.dev_all_admin else cached.get("role")
        return {
            "id": cached["id"],
            "email": cached.get("email"),
            "name": cached.get("name"),
            "role": role,
        }

    # Cache miss path (new user or invalidation race). One-shot retry on
    # a Supavisor idle-killed SSL connection: rollback to release the
    # broken DBAPI handle, then re-issue. pool_pre_ping handles cold
    # checkouts but not a connection that died mid-request.
    from sqlalchemy.exc import OperationalError
    try:
        profile = db.query(Profile).filter_by(id=uid).first()
    except OperationalError:
        db.rollback()
        profile = db.query(Profile).filter_by(id=uid).first()
    if not profile or not profile.active:
        raise HTTPException(status_code=401, detail="Profile not found or inactive")
    # Dev convenience: when DEV_ALL_ADMIN=true, every authenticated user is
    # treated as admin regardless of their stored role. Lets engineers see
    # every page without seeding a separate admin account locally.
    role = "admin" if settings.dev_all_admin else profile.role
    return {
        "id": profile.id,
        "email": profile.email,
        "name": profile.name,
        "role": role,
    }


def require_lead(user: dict = Depends(current_user)) -> dict:
    if user["role"] not in ("lead", "admin"):
        raise HTTPException(status_code=403, detail="Lead role required")
    return user
