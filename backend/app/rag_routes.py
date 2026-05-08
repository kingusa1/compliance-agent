"""L6 RAG retrieval API.

POST /api/rag/search
  body: {query, namespace?, call_id?, supplier?, top_k?}
  resp: {results: [...], embeddings_available: bool}

`embeddings_available` is False when OPENAI_API_KEY is unset; the L4
/agents/[name] drill-down 'Similar Failures' panel uses this to render
the 'embeddings unavailable' banner instead of an empty list.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.rag import search as rag_search
from app.rag.namespaces import REGISTRY as _NS_REGISTRY

rag_router = APIRouter(tags=["rag"])

# L10: 7 RAG namespaces (transcripts, scripts, loa_templates, supplier_docs,
# gates, rule_catalog, rejections) + legacy 'directives' + 'all'.
_VALID_NAMESPACES = set(_NS_REGISTRY.keys()) | {"directives", "all"}

_NAMESPACE_LABELS = {
    "transcripts":   "Call transcripts",
    "scripts":       "Compliance scripts",
    "loa_templates": "LOA templates",
    "supplier_docs": "Supplier docs",
    "gates":         "Compliance gates",
    "rule_catalog":  "Rule catalog",
    "rejections":    "Past rejections",
}


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    # transcripts | scripts | directives | loa_templates | supplier_docs |
    # gates | rule_catalog | rejections | all
    namespace: str = "all"
    call_id: str | None = None
    supplier: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)


@rag_router.post("/api/rag/search")
def rag_search_endpoint(payload: RagSearchRequest, db: Session = Depends(get_db)):
    embeddings_available = bool(os.getenv("OPENAI_API_KEY", "").strip())

    ns = payload.namespace if payload.namespace in _VALID_NAMESPACES else "all"
    results = rag_search.search(
        query=payload.query,
        namespace=ns,  # type: ignore[arg-type]
        call_id=payload.call_id,
        supplier=payload.supplier,
        top_k=payload.top_k,
        db=db,
    )
    return {
        "results": [
            {
                "namespace": r.namespace,
                "ref_id": r.ref_id,
                "text": r.text,
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
        ],
        "embeddings_available": embeddings_available,
    }


@rag_router.get("/api/rag/namespaces")
def rag_namespaces(db: Session = Depends(get_db)):
    """Return the 7 RAG namespaces with row counts for the chat UI dropdown.

    Counts gracefully fall back to 0 when the ORM class or table doesn't
    exist yet (Lane D ships before main adds the new ORM classes).
    """
    out = []
    for key, cfg in _NS_REGISTRY.items():
        count = 0
        try:
            from app import models as _m

            Orm = getattr(_m, cfg.table_orm_name, None)
            if Orm is not None:
                count = db.query(Orm).count()
        except Exception:
            count = 0
        out.append({
            "key": key,
            "label": _NAMESPACE_LABELS.get(key, key),
            "count": count,
        })
    return {"namespaces": out}
