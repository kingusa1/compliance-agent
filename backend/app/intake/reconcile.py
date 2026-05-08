"""Manual-vs-auto metadata reconciliation for L7 structured intake.

Entry point: :func:`reconcile_metadata`. Given a manual dict (typed by the
reviewer at intake) and an auto dict (filled by the pipeline post-upload),
returns a per-field map of ``{value, source}`` where ``source`` is one of:

  * ``manual`` — reviewer typed it; auto blank
  * ``auto``   — pipeline detected it; reviewer left blank
  * ``both``   — both filled, values agree
  * ``mismatch`` — both filled, values disagree

When any field falls into the ``mismatch`` bucket, this module also emits
a :class:`Flag` ORM row with ``rule_id='METADATA_MISMATCH'`` and
``severity='high'`` per the L7 contract. The Flag surfaces in the
reviewer UX as a warning chip on the call-detail header (L4 reads it).

The DB write happens via :func:`emit_mismatch_flag` so callers can
reconcile in pure-function mode (for tests) and only emit Flags when a
real :class:`Session` is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

# Imported lazily inside :func:`emit_mismatch_flag` so unit tests that only
# exercise the pure-function path don't need a live SQLAlchemy session.
# (See ``backend/tests/test_intake.py``.)


METADATA_MISMATCH_RULE_ID = "METADATA_MISMATCH"


@dataclass
class ReconciledField:
    """Per-field reconciliation result.

    ``value`` is whichever side won (manual on agreement, manual on
    mismatch — manual is always "ground truth" for write — auto when
    manual was blank).

    ``source`` is the discriminator persisted to the matching
    ``_source_*`` column on customers / customer_deals / calls.
    """

    value: Any
    source: str  # manual | auto | both | mismatch


def _is_blank(v: Any) -> bool:
    """Treat ``None``, empty string, empty container, and the empty
    Decimal as 'not provided'. ``False`` is a valid value for booleans
    (e.g. ``vulnerable_customer_flag=False``) and is NOT blank.
    """
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    if isinstance(v, (list, dict, tuple)) and len(v) == 0:
        return True
    return False


def _values_match(a: Any, b: Any) -> bool:
    """Loose equality across the field types we reconcile.

    String comparison is case-insensitive and whitespace-trimmed because
    auto extraction often returns "british gas core" while a reviewer
    types "British Gas Core" — they should agree.

    Decimal/numeric comparison normalizes through ``Decimal`` so
    ``"123"`` (str from form) and ``Decimal("123.00")`` (auto) agree.
    """
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    if isinstance(a, (int, float, Decimal)) or isinstance(b, (int, float, Decimal)):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return a == b
    return a == b


def reconcile_metadata(
    manual: Dict[str, Any],
    auto: Dict[str, Any],
) -> Dict[str, ReconciledField]:
    """Reconcile manual + auto field maps into a per-field source map.

    Pure function — no DB writes. Callers who need to persist
    METADATA_MISMATCH flags should walk the result and call
    :func:`emit_mismatch_flag` for each field with ``source='mismatch'``.

    Returns a dict keyed by every field that appears in *either* input,
    so callers can iterate and apply ``_source_<field>`` columns directly.
    """
    out: Dict[str, ReconciledField] = {}
    keys = set(manual.keys()) | set(auto.keys())
    for k in keys:
        m = manual.get(k)
        a = auto.get(k)
        m_blank = _is_blank(m)
        a_blank = _is_blank(a)
        if m_blank and a_blank:
            # Both empty — nothing to record. Skip rather than emit
            # source=auto with a None value, which would be misleading.
            continue
        if m_blank and not a_blank:
            out[k] = ReconciledField(value=a, source="auto")
        elif a_blank and not m_blank:
            out[k] = ReconciledField(value=m, source="manual")
        else:
            # Both populated.
            if _values_match(m, a):
                out[k] = ReconciledField(value=m, source="both")
            else:
                # Mismatch — manual wins as ground truth, but we emit
                # METADATA_MISMATCH so the reviewer knows to double-check.
                out[k] = ReconciledField(value=m, source="mismatch")
    return out


def find_mismatches(
    reconciled: Dict[str, ReconciledField],
    manual: Dict[str, Any],
    auto: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return a list of mismatch evidence rows ready to feed
    :func:`emit_mismatch_flag`. Each row carries field name + both values
    so the resulting Flag has actionable context for the reviewer.
    """
    rows: List[Dict[str, Any]] = []
    for field, rf in reconciled.items():
        if rf.source != "mismatch":
            continue
        rows.append(
            {
                "field": field,
                "manual": manual.get(field),
                "auto": auto.get(field),
                "evidence": f"Manual: {manual.get(field)!r}, Auto: {auto.get(field)!r}",
            }
        )
    return rows


def emit_mismatch_flag(
    db: Any,  # SQLAlchemy Session — lazy-typed so tests don't need import
    call_id: str,
    field: str,
    manual_value: Any,
    auto_value: Any,
) -> Optional[Any]:
    """Persist a single METADATA_MISMATCH Flag for a single field.

    Severity is fixed to ``high`` per L7 contract. ``risk_tag`` is left
    null because mismatch is a procedural concern, not one of the four
    Watt risk_tag categories. Returns the created Flag (or None if the
    Flag model isn't importable in the current environment — e.g. unit
    tests that don't load SQLAlchemy).
    """
    try:
        from app.models import Flag
    except ImportError:
        return None

    flag = Flag(
        call_id=call_id,
        rule_id=METADATA_MISMATCH_RULE_ID,
        severity="high",
        reason=f"Manual {field} disagrees with auto-detected {field}",
        evidence=f"Manual: {manual_value!r}, Auto: {auto_value!r}",
        risk_tag=None,
        source="auto",
    )
    db.add(flag)
    return flag
