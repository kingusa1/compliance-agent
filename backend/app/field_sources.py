"""field_sources helpers.

Each editable row carries a JSONB map of {field_name: source_enum}. AI
writes never overwrite human/xlsx_import/integration writes.
"""
from __future__ import annotations

from typing import Any


SOURCE_PRIORITY = {
    "placeholder": 0,
    "ai": 1,
    "integration": 2,
    "xlsx_import": 3,
    "human": 4,
}


def get_source(obj: Any, field: str) -> str:
    return (obj.field_sources or {}).get(field, "placeholder")


def can_overwrite(obj: Any, field: str, new_source: str) -> bool:
    """True iff `new_source` has priority >= existing source for `field`."""
    existing = get_source(obj, field)
    return SOURCE_PRIORITY.get(new_source, 0) >= SOURCE_PRIORITY.get(existing, 0)


def set_source(obj: Any, field: str, source: str) -> None:
    """Stamp a field's provenance.

    Always overwrites — caller should call can_overwrite() first if they
    want to respect priority. Mutates obj.field_sources in place.
    """
    if obj.field_sources is None:
        obj.field_sources = {}
    obj.field_sources = {**obj.field_sources, field: source}
