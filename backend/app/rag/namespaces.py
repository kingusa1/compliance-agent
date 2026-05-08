"""L10 RAG namespace registry.

Single dispatch table mapping namespace key → (ORM class name, ingester
module). Search and admin-upload routes import this so they don't
hard-code the 7 namespace strings in multiple places.

ORM class names are *strings* (not the classes themselves) because
LoaChunk / SupplierDocChunk / GateChunk / RuleChunk / RejectionChunk are
added to app.models.py by the main session AFTER Lane D ships. Until
those classes exist, lookups via getattr(app.models, name, None) return
None and callers gracefully no-op.
"""
from __future__ import annotations

from typing import NamedTuple


class NamespaceConfig(NamedTuple):
    name: str
    table_orm_name: str  # name of ORM class in app.models
    ingester_module: str  # module path under app.rag


REGISTRY: dict[str, NamespaceConfig] = {
    "transcripts":   NamespaceConfig("transcripts",   "TranscriptChunk",  "ingest"),
    "scripts":       NamespaceConfig("scripts",       "ScriptChunk",      "ingest"),
    "loa_templates": NamespaceConfig("loa_templates", "LoaChunk",         "ingest_loa"),
    "supplier_docs": NamespaceConfig("supplier_docs", "SupplierDocChunk", "ingest_supplier_docs"),
    "gates":         NamespaceConfig("gates",         "GateChunk",        "ingest_gates"),
    "rule_catalog":  NamespaceConfig("rule_catalog",  "RuleChunk",        "ingest_rules"),
    "rejections":    NamespaceConfig("rejections",    "RejectionChunk",   "ingest_rejections"),
}


def get_namespaces() -> list[str]:
    """Return the list of registered namespace keys."""
    return list(REGISTRY.keys())
