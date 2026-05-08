"""Cascade tests — AI writes never override human / xlsx_import.

These tests don't need a DB session: ``can_overwrite`` and ``set_source``
operate on plain objects with a ``field_sources`` dict, so synthetic
``CustomerDeal`` instances are sufficient.
"""
import pytest

from app.field_sources import set_source, get_source, can_overwrite
from app.models import CustomerDeal


def test_human_edit_blocks_ai_overwrite():
    deal = CustomerDeal(customer_name="Old Name", supplier="E.ON Next")
    deal.field_sources = {}
    set_source(deal, "supplier", "human")

    assert can_overwrite(deal, "supplier", "ai") is False
    assert get_source(deal, "supplier") == "human"


def test_ai_can_fill_blank():
    deal = CustomerDeal(customer_name="X")
    deal.field_sources = {}
    assert can_overwrite(deal, "supplier", "ai") is True


def test_xlsx_blocks_ai_but_not_human():
    deal = CustomerDeal(customer_name="X")
    deal.field_sources = {}
    set_source(deal, "supplier", "xlsx_import")
    assert can_overwrite(deal, "supplier", "ai") is False
    assert can_overwrite(deal, "supplier", "human") is True
