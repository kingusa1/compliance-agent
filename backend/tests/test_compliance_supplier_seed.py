"""Tests for app.watt_compliance.supplier_seed — the catalogue of 15
supplier scripts and the chunking helper.
"""
from __future__ import annotations

import pytest

from app.watt_compliance.supplier_seed import (
    CATALOGUE,
    chunk_script_markdown,
    metadata_for,
    script_id_for,
)
from app.watt_compliance.taxonomy import CallClass, ScriptType, Supplier


def test_catalogue_size():
    assert len(CATALOGUE) == 15


def test_catalogue_filenames_unique():
    names = [m.filename for m in CATALOGUE]
    assert len(set(names)) == len(names), "duplicate filenames in CATALOGUE"


def test_each_entry_has_well_formed_metadata():
    for m in CATALOGUE:
        assert isinstance(m.supplier, Supplier)
        assert isinstance(m.script_type, ScriptType)
        assert isinstance(m.call_class, CallClass)
        assert m.version, f"empty version on {m.filename}"
        # effective_from is YYYY-MM-DD or None
        if m.effective_from is not None:
            parts = m.effective_from.split("-")
            assert len(parts) == 3 and all(p.isdigit() for p in parts), (
                f"{m.filename} bad effective_from: {m.effective_from!r}"
            )


def test_eon_next_jan_2026_supersedes_undated():
    """When two entries cover the same (supplier, script_type, call_class),
    the older one MUST be marked deprecated. EON Next gas had an undated
    legacy script and the Jan 2026 update."""
    eon_gas = [m for m in CATALOGUE
               if m.supplier is Supplier.EON_NEXT
               and m.script_type is ScriptType.ACQUISITION
               and m.call_class is CallClass.GAS]
    # We expect exactly 2 entries: undated (deprecated=True) and Jan2026.
    assert len(eon_gas) == 2
    deprecated = [m for m in eon_gas if m.deprecated]
    active = [m for m in eon_gas if not m.deprecated]
    assert len(deprecated) == 1, "expected one deprecated EON gas entry"
    assert len(active) == 1, "expected one active EON gas entry"
    assert active[0].version == "Jan2026"


def test_bgl_v6_deprecated_by_v7():
    bgl = [m for m in CATALOGUE if m.supplier is Supplier.BGL]
    assert len(bgl) == 2
    deprecated = next(m for m in bgl if m.deprecated)
    assert deprecated.version == "V6"


def test_chunk_script_markdown_yields_chunks():
    md = """# Sample script

## Section 1

Lots of text here. Numbered item 1. Numbered item 2.

## Section 2

More text here. Item 3.
"""
    chunks = list(chunk_script_markdown(md))
    assert chunks, "should yield at least one chunk"
    assert all(isinstance(idx, int) and isinstance(text, str) for idx, text in chunks)


def test_chunk_respects_max_size_target():
    """A very large section is split on paragraph boundaries."""
    huge_para = "word " * 400
    md = f"## Section\n\n{huge_para}\n\n{huge_para}\n\n{huge_para}"
    chunks = list(chunk_script_markdown(md, max_chunk_chars=1500))
    # Should produce at least 2 chunks because the content is > max_chunk_chars.
    assert len(chunks) >= 2


def test_metadata_dict_round_trip():
    m = CATALOGUE[0]
    meta = metadata_for(m, chunk_idx=0)
    assert meta["supplier"] == m.supplier.value
    assert meta["script_type"] == m.script_type.value
    assert meta["call_class"] == m.call_class.value
    assert meta["version"] == m.version
    assert meta["deprecated"] == m.deprecated
    assert meta["namespace"].startswith("scripts:")


def test_script_id_is_stable_and_unique():
    ids = [script_id_for(m) for m in CATALOGUE]
    # Stable under repeated calls.
    ids2 = [script_id_for(m) for m in CATALOGUE]
    assert ids == ids2
    # Two BGL entries differ only by version — must produce distinct ids.
    bgl_ids = [script_id_for(m) for m in CATALOGUE if m.supplier is Supplier.BGL]
    assert len(set(bgl_ids)) == 2


@pytest.mark.parametrize("supplier", list(Supplier))
def test_every_in_scope_supplier_has_at_least_one_script(supplier: Supplier):
    assert any(m.supplier is supplier for m in CATALOGUE), (
        f"{supplier} has no scripts in the catalogue"
    )
