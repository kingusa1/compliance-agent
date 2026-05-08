import pytest

from app.field_sources import set_source, get_source, can_overwrite, SOURCE_PRIORITY


def test_set_and_get_source():
    obj = type("X", (), {"field_sources": {}})()
    set_source(obj, "supplier", "ai")
    assert get_source(obj, "supplier") == "ai"


def test_can_overwrite_respects_priority():
    obj = type("X", (), {"field_sources": {"supplier": "human"}})()
    assert can_overwrite(obj, "supplier", "ai") is False
    assert can_overwrite(obj, "supplier", "human") is True


def test_can_overwrite_when_field_missing():
    obj = type("X", (), {"field_sources": {}})()
    assert can_overwrite(obj, "supplier", "ai") is True


def test_priority_order():
    assert SOURCE_PRIORITY["human"] > SOURCE_PRIORITY["xlsx_import"]
    assert SOURCE_PRIORITY["xlsx_import"] > SOURCE_PRIORITY["ai"]
    assert SOURCE_PRIORITY["ai"] > SOURCE_PRIORITY["placeholder"]
