"""Rule catalog ingestion.

Reads backend/app/rules_catalog.json (the existing 21 L2 rules) and
embeds each rule as one chunk. Text = name + expected phrases +
description. Metadata captures rule id, category, severity. Idempotent
rebuild on every call.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RULES_CATALOG_PATH = Path(__file__).resolve().parents[1] / "rules_catalog.json"


def ingest_rules(db) -> int:
    """Embed every rule and write a RuleChunk row. Idempotent rebuild.

    Returns rows written. 0 if RuleChunk ORM missing or catalog not found.
    """
    try:
        from app.models import RuleChunk  # type: ignore
    except ImportError:
        log.warning("RuleChunk ORM not yet present; ingest_rules is a no-op stub.")
        return 0

    if not RULES_CATALOG_PATH.exists():
        log.warning("RULES_INGEST catalog not found at %s", RULES_CATALOG_PATH)
        return 0

    try:
        rules = json.loads(RULES_CATALOG_PATH.read_text())
    except Exception as e:
        log.warning("RULES_INGEST catalog parse failed: %s", e)
        return 0

    if not isinstance(rules, list) or not rules:
        return 0

    texts: list[str] = []
    for rule in rules:
        name = (rule.get("name") or "").strip()
        phrases = rule.get("expected_phrases") or []
        if not isinstance(phrases, list):
            phrases = []
        description = (rule.get("description") or "").strip()
        texts.append(f"{name}. {' '.join(phrases)}. {description}".strip())

    embeddings: list[Any] = [None] * len(texts)
    try:
        from app.rag.embed import embed_batch

        embeddings = embed_batch(texts)
    except EnvironmentError as e:
        log.warning("RULES embed skipped (no key): %s", e)
    except Exception as e:  # noqa: BLE001
        log.warning("RULES embed failed: %s", e)

    db.query(RuleChunk).delete()
    for rule, txt, emb in zip(rules, texts, embeddings):
        db.add(RuleChunk(
            rule_id=rule.get("id"),
            name=rule.get("name"),
            category=rule.get("category"),
            severity=rule.get("severity"),
            text=txt,
            embedding=emb,
        ))
    db.commit()

    embedded = embeddings and embeddings[0] is not None
    log.info(
        "RULES_INGEST rules=%d embedded=%s",
        len(rules), "yes" if embedded else "no",
    )
    return len(rules)
