"""Compatibility shim — identity now flows through Supabase Auth JWT (see app.auth).

Downstream code still imports `current_reviewer` / `require_lead` from here;
both are re-exported from `app.auth` with the same signatures.
"""
from app.auth import current_user as current_reviewer  # noqa: F401
from app.auth import require_lead  # noqa: F401
