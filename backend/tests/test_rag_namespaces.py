"""L10 namespace registry + dispatch tests.

These run before main session adds the 5 new ORM classes (LoaChunk,
SupplierDocChunk, GateChunk, RuleChunk, RejectionChunk). Search and
ingest helpers must skip gracefully when the ORM class is missing.
"""
from __future__ import annotations

from app.rag.namespaces import REGISTRY, get_namespaces


def test_namespace_registry_has_7_entries():
    assert len(REGISTRY) == 7
    assert set(REGISTRY.keys()) == {
        "transcripts", "scripts", "loa_templates", "supplier_docs",
        "gates", "rule_catalog", "rejections",
    }


def test_get_namespaces_returns_known_keys():
    keys = get_namespaces()
    assert isinstance(keys, list)
    assert "transcripts" in keys
    assert "loa_templates" in keys
    assert "rejections" in keys
    assert len(keys) == 7


def test_namespace_config_shape():
    cfg = REGISTRY["loa_templates"]
    assert cfg.name == "loa_templates"
    assert cfg.table_orm_name == "LoaChunk"
    assert cfg.ingester_module == "ingest_loa"


def test_search_namespace_dispatch_skips_missing_orm(test_db):
    """When ORM class is missing AND no API key, search returns []."""
    import os
    # Make sure embed_one fails fast (no key) so we exercise the early return.
    os.environ.pop("OPENAI_API_KEY", None)

    from app.rag import search as rag_search

    # No API key → embed returns None → search returns [] regardless of namespace.
    out = rag_search.search(
        query="cancellation rights",
        namespace="loa_templates",
        db=test_db,
    )
    assert out == []


def test_search_all_namespace_no_key(test_db):
    """`namespace='all'` also returns [] when embeddings are unavailable."""
    import os
    os.environ.pop("OPENAI_API_KEY", None)

    from app.rag import search as rag_search

    out = rag_search.search(query="anything", namespace="all", db=test_db)
    assert out == []
