"""One-shot bootstrap that ingests the 14 supplier scripts into RAG.

Reads the markdown extracts in `.planning/phase2-docs/supplier_scripts__*.md`
(produced by `scripts/extract_phase2_docs.py`), parses the supplier /
script_type / call_class / version metadata from the filename + content,
chunks per numbered item / page, and writes ScriptChunk rows via the
existing `app.rag.ingest.embed_batch` pathway.

Usage (one-time after first deploy):

    cd backend
    ./venv/bin/python -m scripts.seed_compliance_data

This script is idempotent — re-runs are safe because the existing
`ingest_script` upserts on (script_id, version, checkpoint_idx).

Note: this module only does the metadata + parsing. The actual embedding
+ DB write happens via the existing `app/rag/ingest.py` API. We do not
duplicate the OpenAI-embed code path here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.watt_compliance.taxonomy import CallClass, ScriptType, Supplier


# Source-of-truth mapping from generated markdown filename → metadata.
# Filenames are produced by `extract_phase2_docs.py:slug()`.
@dataclass(frozen=True)
class SupplierScriptMeta:
    filename: str
    supplier: Supplier
    script_type: ScriptType
    call_class: CallClass
    version: str
    effective_from: str | None  # ISO date or None for undated/legacy
    deprecated: bool


CATALOGUE: tuple[SupplierScriptMeta, ...] = (
    # ── BGL ──────────────────────────────────────────────────────
    SupplierScriptMeta(
        "supplier_scripts__bgl_broker_acquisition_script_v7_.md",
        Supplier.BGL, ScriptType.ACQUISITION, CallClass.DUAL, "V7", None, False),
    SupplierScriptMeta(
        "supplier_scripts__correct_-_bgl_acquisition_script.md",
        Supplier.BGL, ScriptType.ACQUISITION, CallClass.DUAL, "V6", None, True),  # deprecated by V7

    # ── British Gas (core) ───────────────────────────────────────
    SupplierScriptMeta(
        "supplier_scripts__british_gas__broker_acquisition_script_v0.2_1.md",
        Supplier.BRITISH_GAS, ScriptType.ACQUISITION, CallClass.DUAL, "V0.2", None, False),
    SupplierScriptMeta(
        "supplier_scripts__british_gas_broker_upgrade_renewals_deemed_script_v03_1.md",
        Supplier.BRITISH_GAS, ScriptType.RENEWAL, CallClass.DUAL, "V03", "2023-07-01", False),

    # ── EDF ──────────────────────────────────────────────────────
    SupplierScriptMeta(
        "supplier_scripts__edf_h3083_tpi_fixed_for_business_online_acqusition_script_aw1_v11.md",
        Supplier.EDF, ScriptType.ACQUISITION, CallClass.DUAL, "V11", "2024-08-01", False),
    SupplierScriptMeta(
        "supplier_scripts__edf_pre_amble_script_to_be_read_.md",
        Supplier.EDF, ScriptType.PREAMBLE, CallClass.ANY, "v1", None, False),

    # ── E.ON Next ────────────────────────────────────────────────
    SupplierScriptMeta(
        "supplier_scripts__eon_next_elec_verbal_contract_script.md",
        Supplier.EON_NEXT, ScriptType.ACQUISITION, CallClass.ELEC, "undated", None, True),  # deprecated by Jan 26
    SupplierScriptMeta(
        "supplier_scripts__eon_next_gas_verbal_contract_script.md",
        Supplier.EON_NEXT, ScriptType.ACQUISITION, CallClass.GAS, "undated", None, True),
    SupplierScriptMeta(
        "supplier_scripts__eon_next_gas_verbal_contract_script_tpi_-_jan_26.md",
        Supplier.EON_NEXT, ScriptType.ACQUISITION, CallClass.GAS, "Jan2026", "2026-01-01", False),
    SupplierScriptMeta(
        "supplier_scripts__eon_next_nhh_&_hh_verbal_contract_script_tpi_-_jan_26.md",
        Supplier.EON_NEXT, ScriptType.ACQUISITION, CallClass.NHH, "Jan2026", "2026-01-01", False),
    SupplierScriptMeta(
        "supplier_scripts__eon_tpi_verbal_loa_script_2.md",
        Supplier.EON_NEXT, ScriptType.LOA, CallClass.ANY, "V2", None, False),

    # ── Pozitive ────────────────────────────────────────────────
    SupplierScriptMeta(
        "supplier_scripts__pozitive_verbal_contract_script_pe.md",
        Supplier.POZITIVE, ScriptType.ACQUISITION, CallClass.DUAL, "PE", None, False),

    # ── Scottish Power ──────────────────────────────────────────
    SupplierScriptMeta(
        "supplier_scripts__scottish_power_for_business_acq_script_-_tpi_october_24.md",
        Supplier.SCOTTISH_POWER, ScriptType.ACQUISITION, CallClass.DUAL, "Oct2024", "2024-10-01", False),
    SupplierScriptMeta(
        "supplier_scripts__scottish_power_for_business_renewal_script_-_tpi_october_24.md",
        Supplier.SCOTTISH_POWER, ScriptType.RENEWAL, CallClass.DUAL, "Oct2024", "2024-10-01", False),
    SupplierScriptMeta(
        "supplier_scripts__scottish_power_for_business_script_-_tpi_acq_multisite_october_24.md",
        Supplier.SCOTTISH_POWER, ScriptType.ACQUISITION, CallClass.DUAL, "Oct2024-multisite", "2024-10-01", False),
)


def docs_dir() -> Path:
    """Resolve `.planning/phase2-docs/` relative to repo root."""
    here = Path(__file__).resolve()
    # backend/app/compliance/supplier_seed.py → repo root is parents[3]
    return here.parents[3] / ".planning" / "phase2-docs"


def chunk_script_markdown(md: str, *, max_chunk_chars: int = 1500) -> Iterable[tuple[int, str]]:
    """Split the extracted markdown into (chunk_idx, text) pairs.

    Strategy: respect the `## Page N` headings produced by the PDF
    extractor and the numbered items typical of supplier scripts. If a
    section exceeds `max_chunk_chars`, break it on paragraph boundaries.
    """
    lines = md.splitlines()
    sections: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("## ") and sections[-1]:
            sections.append([])
        sections[-1].append(line)

    chunks: list[str] = []
    for sec in sections:
        text = "\n".join(sec).strip()
        if not text:
            continue
        if len(text) <= max_chunk_chars:
            chunks.append(text)
            continue
        # Break long sections on blank-line boundaries.
        buf: list[str] = []
        size = 0
        for para in text.split("\n\n"):
            if size + len(para) > max_chunk_chars and buf:
                chunks.append("\n\n".join(buf))
                buf = []
                size = 0
            buf.append(para)
            size += len(para) + 2
        if buf:
            chunks.append("\n\n".join(buf))

    for i, c in enumerate(chunks):
        yield i, c


def metadata_for(meta: SupplierScriptMeta, chunk_idx: int) -> dict[str, object]:
    """Build the metadata dict written to the ScriptChunk row."""
    return {
        "supplier": meta.supplier.value,
        "script_type": meta.script_type.value,
        "call_class": meta.call_class.value,
        "version": meta.version,
        "effective_from": meta.effective_from,
        "deprecated": meta.deprecated,
        "chunk_idx": chunk_idx,
        "namespace": f"scripts:{meta.supplier.value}:{meta.script_type.value}:{meta.call_class.value}",
    }


def script_id_for(meta: SupplierScriptMeta) -> str:
    """Stable script_id used when upserting via app.rag.ingest."""
    return f"{meta.supplier.value}:{meta.script_type.value}:{meta.call_class.value}:{meta.version}"
