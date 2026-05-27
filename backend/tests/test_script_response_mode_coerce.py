"""Regression: ScriptResponse must accept mode=None and coerce to default.

Wave-24c regression hot-fix (2026-05-27 14:57 UTC) — a raw-SQL INSERT in
the wave-24 migration left scripts.mode = NULL on the Pozitive Preamble
row, which made the entire ``GET /api/scripts`` endpoint return 500 for
every reviewer. The fix coerces None → 'meaning_for_meaning' in the
Pydantic ScriptResponse before-validator so this class of bug can never
take down the list endpoint again.

These tests lock the coercion contract.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.schemas import ScriptResponse, ScriptListResponse, ScriptVersionResponse


def _row(mode):
    """Build a minimal valid Script row with the given mode."""
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "supplier_name": "Pozitive",
        "script_name": "Pozitive Preamble (Pass-Through Disclosure)",
        "version": "v1",
        "mode": mode,
        "checkpoints": "[]",
        "active": True,
        "created_at": datetime(2026, 5, 27, 14, 0, 0),
        "updated_at": None,
    }


def test_mode_none_coerces_to_default():
    sr = ScriptResponse(**_row(None))
    assert sr.mode == "meaning_for_meaning"


def test_mode_empty_string_coerces_to_default():
    sr = ScriptResponse(**_row(""))
    assert sr.mode == "meaning_for_meaning"


def test_mode_explicit_value_preserved():
    sr = ScriptResponse(**_row("verbatim"))
    assert sr.mode == "verbatim"


def test_script_list_response_with_null_mode_row_doesnt_500():
    """The exact failure shape from prod — a list with one mode=NULL row."""
    payload = ScriptListResponse(scripts=[_row(None), _row("verbatim")], total=2)
    assert payload.scripts[0].mode == "meaning_for_meaning"
    assert payload.scripts[1].mode == "verbatim"
    assert payload.total == 2


def test_mode_none_via_orm_attributes_path():
    """The actual prod path: ORM object → ScriptResponse.model_validate.

    The Pydantic field_validator(mode='before') must fire whether the
    input comes from a dict OR from an ORM attribute (Script model with
    from_attributes=True). This test mirrors the path Pydantic takes
    when /api/scripts hands a list of Script ORM rows to ScriptResponse.
    """
    orm_obj = SimpleNamespace(**_row(None))
    sr = ScriptResponse.model_validate(orm_obj)
    assert sr.mode == "meaning_for_meaning"


def test_script_version_response_mode_snapshot_null_coerce():
    """Symmetry: ScriptVersionResponse.mode_snapshot must coerce identically.

    Same class of bug could break GET /api/scripts/{id}/versions if a
    version row ever lands with mode_snapshot=NULL.
    """
    sv = ScriptVersionResponse(
        id="22222222-2222-2222-2222-222222222222",
        script_id="11111111-1111-1111-1111-111111111111",
        version_number=1,
        checkpoints_snapshot="[]",
        mode_snapshot=None,
        created_at=datetime(2026, 5, 27, 14, 0, 0),
    )
    assert sv.mode_snapshot == "meaning_for_meaning"
