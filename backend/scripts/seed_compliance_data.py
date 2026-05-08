"""Seed the 14 supplier scripts into RAG.

Reads the markdown extracts produced by ``extract_phase2_docs.py``,
upserts a ``Script`` row + ``ScriptVersion`` for each entry in
``app.watt_compliance.supplier_seed.CATALOGUE``, then calls the
existing ``app.rag.ingest.ingest_script`` to chunk+embed+store.

Usage:

    cd backend
    ./venv/bin/python -m scripts.seed_compliance_data --dry-run    # default
    ./venv/bin/python -m scripts.seed_compliance_data --apply      # actually write

In ``--dry-run`` mode (default) no DB writes happen — the script just
prints what it WOULD do. This lets us verify the catalogue + paths are
correct before plugging in real Supabase + OpenAI credentials.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.watt_compliance.supplier_seed import (
    CATALOGUE,
    SupplierScriptMeta,
    chunk_script_markdown,
    docs_dir,
    metadata_for,
    script_id_for,
)


def _load_markdown(meta: SupplierScriptMeta) -> str | None:
    p = docs_dir() / meta.filename
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _checkpoints_from_markdown(md: str) -> list[dict]:
    """Convert chunked markdown into the JSON shape ``ingest_script``
    expects (a list of objects with ``text`` and an ``index``)."""
    return [
        {"index": idx, "section": f"chunk_{idx}", "name": f"Chunk {idx}", "text": text}
        for idx, text in chunk_script_markdown(md)
    ]


def _print_dry_run(meta: SupplierScriptMeta, checkpoints: list[dict]) -> None:
    print(f"  -> script_id={script_id_for(meta)}")
    print(f"     supplier={meta.supplier.value} type={meta.script_type.value} "
          f"class={meta.call_class.value} version={meta.version} "
          f"deprecated={meta.deprecated}")
    print(f"     chunks={len(checkpoints)} avg_chars="
          f"{sum(len(c['text']) for c in checkpoints) // max(1, len(checkpoints))}")
    if checkpoints:
        first = checkpoints[0]["text"]
        print(f"     preview={first[:120]!r}{'...' if len(first) > 120 else ''}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write to DB. Default is dry-run.")
    parser.add_argument("--filter", default=None,
                        help="Only process catalogue entries whose filename contains this substring.")
    args = parser.parse_args(argv)

    src_dir = docs_dir()
    if not src_dir.exists():
        print(f"ERROR: phase2-docs/ not found at {src_dir}", file=sys.stderr)
        print("Run `python scripts/extract_phase2_docs.py` first.", file=sys.stderr)
        return 2

    print(f"Source dir: {src_dir}")
    print(f"Catalogue:  {len(CATALOGUE)} entries")
    print(f"Mode:       {'APPLY' if args.apply else 'DRY-RUN'}")
    if args.filter:
        print(f"Filter:     {args.filter!r}")
    print()

    processed = 0
    skipped = 0
    for meta in CATALOGUE:
        if args.filter and args.filter not in meta.filename:
            continue
        md = _load_markdown(meta)
        if md is None:
            print(f"  FAIL {meta.filename} — markdown file not found, skipping")
            skipped += 1
            continue
        checkpoints = _checkpoints_from_markdown(md)
        print(f"[{processed + skipped + 1:2}/{len(CATALOGUE)}] {meta.filename}")
        _print_dry_run(meta, checkpoints)

        if args.apply:
            try:
                from app.database import SessionLocal
                from app.models import Script, ScriptVersion
                from app.rag.ingest import ingest_script
            except Exception as e:  # noqa: BLE001
                print(f"  FAIL APPLY mode requires the running stack: {e}", file=sys.stderr)
                return 3

            db = SessionLocal()
            try:
                sid = script_id_for(meta)
                script = db.query(Script).filter(Script.id == sid).one_or_none()
                if script is None:
                    script = Script(
                        id=sid,
                        name=f"{meta.supplier.value} / {meta.script_type.value} / {meta.call_class.value}",
                        checkpoints=json.dumps(checkpoints),
                    )
                    db.add(script)
                else:
                    script.checkpoints = json.dumps(checkpoints)
                # Always create a new ScriptVersion so historical
                # versions are auditable.
                latest = (
                    db.query(ScriptVersion)
                    .filter(ScriptVersion.script_id == sid)
                    .order_by(ScriptVersion.version_number.desc())
                    .first()
                )
                next_version = (latest.version_number + 1) if latest else 1
                version_row = ScriptVersion(
                    script_id=sid,
                    version_number=next_version,
                    checkpoints_snapshot=json.dumps(checkpoints),
                    metadata_snapshot=json.dumps(metadata_for(meta, 0)),
                )
                db.add(version_row)
                db.commit()
                result = ingest_script(sid, db)
                print(f"  OK wrote: {result}")
            finally:
                db.close()
        processed += 1

    print()
    print(f"Done — processed={processed} skipped={skipped} mode="
          f"{'APPLY' if args.apply else 'DRY-RUN'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
