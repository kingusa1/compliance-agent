"""GET /api/rules — Pillar 2 rule catalog endpoint.

Loads `rules_catalog.json` once at module import (hot-reload happens
when the uvicorn worker restarts; that's the v2 demo budget). The
frontend uses this to render severity badges and the rule-detail drawer
in the v2 reviewer UI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_user

_CATALOG_PATH = Path(__file__).resolve().parent / "rules_catalog.json"


def _load_catalog() -> list[dict[str, Any]]:
    with _CATALOG_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return list(data.values())
    if isinstance(data, list):
        return data
    return []


_CATALOG: list[dict[str, Any]] = _load_catalog()
_CATALOG_INDEX: dict[str, dict[str, Any]] = {r["id"]: r for r in _CATALOG if "id" in r}


# Auth gate (2026-05-30 security audit): the rule catalog is internal compliance
# IP — require an authenticated user.
rules_router = APIRouter(
    prefix="/api/rules", tags=["rules"], dependencies=[Depends(current_user)]
)


@rules_router.get("")
def list_rules(q: str | None = None) -> dict[str, Any]:
    """List all rules, optionally filtered by case-insensitive substring
    match on `id` or `name`."""
    rules = list(_CATALOG)
    if q:
        needle = q.lower()
        rules = [
            r for r in rules
            if needle in str(r.get("name", "")).lower()
            or needle in str(r.get("id", "")).lower()
        ]
    return {"rules": rules, "total": len(rules)}


@rules_router.get("/{rule_id}")
def get_rule(rule_id: str) -> dict[str, Any]:
    rule = _CATALOG_INDEX.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return rule
