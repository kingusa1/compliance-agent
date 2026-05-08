"""pg_dump -> optional age encryption -> object-storage upload.

Driven by environment via `app.config.settings`:
  - migration_database_url (required for pg_dump)
  - backup_bucket          (logical bucket inside the active StorageBackend)
  - backup_age_recipient   (age public key; empty = upload plaintext gzip)

Output key layout:
  backups/YYYY/MM/DD/compliance-HHMMSS.sql.gz[.age]

Designed for invocation by:
  - Inngest scheduled function `pg_dump_nightly` (preferred prod)
  - Cron on the VPS (fallback)
  - Engineer ad-hoc via `python -m backend.scripts.pg_dump_to_storage`
"""
from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import settings
from app.storage import get_backend


def _make_dump_path(ts: _dt.datetime, work_dir: str) -> Path:
    name = f"compliance-{ts.strftime('%H%M%S')}.sql.gz"
    return Path(work_dir) / name


def _make_encrypted_path(dump: Path) -> Path:
    return dump.with_suffix(dump.suffix + ".age")


def _remote_key(ts: _dt.datetime, fname: str) -> str:
    return f"{settings.backup_bucket}/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/{fname}"


def run(work_dir: str | None = None) -> str:
    """Execute one backup. Returns the remote key of the uploaded artefact."""
    work = work_dir or tempfile.mkdtemp(prefix="cmpl-bkp-")
    ts = _dt.datetime.utcnow()

    dump_path = _make_dump_path(ts, work)
    db_url = settings.migration_database_url or settings.database_url
    if not db_url:
        raise RuntimeError("No DATABASE_URL or MIGRATION_DATABASE_URL configured.")

    pg_cmd = ["pg_dump", "--format=custom", "--compress=6", "--file", str(dump_path), db_url]
    subprocess.run(pg_cmd, check=True)

    upload_path: Path = dump_path
    content_type = "application/gzip"
    if settings.backup_age_recipient.strip():
        encrypted = _make_encrypted_path(dump_path)
        age_cmd = ["age", "-r", settings.backup_age_recipient, "-o", str(encrypted), str(dump_path)]
        subprocess.run(age_cmd, check=True)
        upload_path = encrypted
        content_type = "application/octet-stream"

    key = _remote_key(ts, upload_path.name)
    get_backend().upload_blob(str(upload_path), key, content_type=content_type)
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pg_dump -> object storage")
    parser.add_argument("--work-dir", default=None, help="Override temp work dir")
    args = parser.parse_args(argv)
    key = run(work_dir=args.work_dir)
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
