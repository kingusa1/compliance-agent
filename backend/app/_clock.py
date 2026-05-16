"""Single-source UTC clock helper.

Python 3.12 deprecated ``datetime.utcnow()`` and 3.14 removes it. The
replacement is ``datetime.now(timezone.utc)`` which returns an AWARE
datetime — but most of this codebase's SQLAlchemy columns are
``DateTime`` (no ``timezone=True``) so swapping naive→aware would break
comparisons (``aware < naive`` raises TypeError) and serialization.

This helper returns a UTC-aligned NAIVE datetime — identical semantics to
the legacy ``datetime.utcnow()`` — but without the DeprecationWarning.

Audit 2026-05-16 P1-6: every call-site in the backend should ``from
app._clock import utcnow`` and use ``utcnow()`` instead of
``datetime.utcnow()``. The system prompt's "no half-finished work" rule
applies — if a file still calls ``datetime.utcnow()`` after this commit,
it's a regression.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """UTC-aligned naive datetime, identical to legacy ``datetime.utcnow()``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
