"""Backfill call_ref + slug on any calls row missing them.

Run once after migration f1a2b3c4d5e6 to populate human-readable ids
for calls that existed before the migration shipped.

    cd backend && ./venv/bin/python scripts/backfill_call_ids.py

Idempotent — only touches rows where call_ref or slug is NULL.
"""
import re
import sys
from pathlib import Path

# Allow running from the backend dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from sqlalchemy import text


def build_slug(filename: str, existing: set[str]) -> str:
    """Kebab-case the filename stem, cap at 80 chars, dedupe with -N suffix."""
    stem = filename.rsplit(".", 1)[0] if filename else "call"
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:80] or "call"
    slug = base
    n = 2
    while slug in existing:
        slug = f"{base}-{n}"[:80]
        n += 1
    return slug


def main() -> None:
    db = SessionLocal()

    rows = db.execute(
        text(
            """
            SELECT id, filename, created_at, call_ref, slug
            FROM calls
            ORDER BY created_at ASC, id ASC
            """
        )
    ).fetchall()

    taken = {r.slug for r in rows if r.slug}
    year_seq: dict[str, int] = {}
    for r in rows:
        if r.call_ref:
            m = re.match(r"^CA-(\d{4})-(\d+)$", r.call_ref)
            if m:
                y, n = m.group(1), int(m.group(2))
                year_seq[y] = max(year_seq.get(y, 0), n)

    updated = 0
    for r in rows:
        patch: dict[str, str] = {}
        if not r.slug:
            slug = build_slug(r.filename or r.id, taken)
            taken.add(slug)
            patch["slug"] = slug
        if not r.call_ref:
            year = r.created_at.strftime("%Y")
            year_seq[year] = year_seq.get(year, 0) + 1
            patch["call_ref"] = f"CA-{year}-{year_seq[year]:04d}"
        if patch:
            cols = ", ".join(f"{k} = :{k}" for k in patch)
            db.execute(
                text(f"UPDATE calls SET {cols} WHERE id = :id"),
                {**patch, "id": r.id},
            )
            updated += 1
            ref = patch.get("call_ref", "(kept)")
            slug = patch.get("slug", "(kept)")[:60]
            print(f"  {r.id[:8]}  ref={ref}  slug={slug}")

    db.commit()
    print(f"\nbackfilled {updated} of {len(rows)} calls")


if __name__ == "__main__":
    main()
