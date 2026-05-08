"""L10 admin-only RAG document upload endpoint.

Admins POST a multipart upload here per namespace. Reviewers and other
roles get a 403. Build-time-only namespaces (gates / rule_catalog /
rejections) reject uploads at this layer — they ingest from repo files
via the corresponding ingester functions, not from user uploads.
"""
from __future__ import annotations

import importlib
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import current_user
from app.database import get_db
from app.rag.namespaces import REGISTRY

log = logging.getLogger(__name__)

rag_admin_router = APIRouter(prefix="/api/rag/admin", tags=["rag_admin"])


@rag_admin_router.post("/upload")
async def upload_doc(
    namespace: str = Form(...),
    supplier: str | None = Form(None),
    doc_type: str | None = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
    db=Depends(get_db),
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    if namespace not in REGISTRY:
        raise HTTPException(status_code=422, detail=f"unknown namespace {namespace}")

    cfg = REGISTRY[namespace]
    ingester = importlib.import_module(f"app.rag.{cfg.ingester_module}")
    contents = await file.read()

    if namespace == "loa_templates":
        if not supplier:
            raise HTTPException(status_code=422, detail="supplier required for loa_templates")
        rows = ingester.ingest_loa(supplier, contents, db)
    elif namespace == "supplier_docs":
        if not supplier or not doc_type:
            raise HTTPException(status_code=422, detail="supplier and doc_type required")
        rows = ingester.ingest_supplier_docs(supplier, doc_type, contents, db)
    else:
        raise HTTPException(
            status_code=422,
            detail=f"namespace {namespace} not user-uploadable (build-time only)",
        )

    log.info("RAG_ADMIN_UPLOAD ns=%s supplier=%s doc_type=%s rows=%d",
             namespace, supplier, doc_type, rows)
    return {"chunks_written": rows, "namespace": namespace}
