"""Smoke: pg_dump_to_storage CLI runs pg_dump, optionally encrypts, uploads."""
import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

# pytest.ini sets testpaths=tests with cwd=backend/, so `backend.` package
# isn't on sys.path. Inject the repo root so `from backend.scripts...` resolves.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.scripts import pg_dump_to_storage as mod


def test_run_dumps_invokes_pg_dump_and_uploads(tmp_path, monkeypatch):
    """With no age recipient, the dump is uploaded as plain .sql.gz."""
    monkeypatch.setattr(mod.settings, "backup_age_recipient", "")
    monkeypatch.setattr(mod.settings, "backup_bucket", "backups")
    fake_dump = tmp_path / "dump.sql.gz"
    fake_dump.write_bytes(b"PGDMP fake")

    fake_subprocess_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(mod, "_make_dump_path", lambda ts, work: fake_dump)

    fake_backend = MagicMock()
    fake_backend.upload_blob.return_value = "backups/2026/05/07/compliance.sql.gz"
    monkeypatch.setattr(mod, "get_backend", lambda: fake_backend)

    out = mod.run(work_dir=str(tmp_path))

    assert out.endswith(".sql.gz")
    fake_backend.upload_blob.assert_called_once()
    args, kwargs = fake_backend.upload_blob.call_args
    assert args[0] == str(fake_dump)
    assert "backups/" in args[1]
    assert kwargs.get("content_type") == "application/gzip"


def test_run_encrypts_when_age_recipient_set(tmp_path, monkeypatch):
    """With an age recipient, the dump is age-encrypted to .sql.gz.age before upload."""
    monkeypatch.setattr(mod.settings, "backup_age_recipient", "age1xxxxx")
    monkeypatch.setattr(mod.settings, "backup_bucket", "backups")
    fake_dump = tmp_path / "dump.sql.gz"
    fake_dump.write_bytes(b"PGDMP fake")
    encrypted = tmp_path / "dump.sql.gz.age"
    encrypted.write_bytes(b"AGE encrypted")

    runs = []
    def fake_run(*args, **kwargs):
        runs.append(args[0])
        return subprocess.CompletedProcess(args=args[0], returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "_make_dump_path", lambda ts, work: fake_dump)
    monkeypatch.setattr(mod, "_make_encrypted_path", lambda d: encrypted)

    fake_backend = MagicMock()
    fake_backend.upload_blob.return_value = "backups/2026/05/07/compliance.sql.gz.age"
    monkeypatch.setattr(mod, "get_backend", lambda: fake_backend)

    out = mod.run(work_dir=str(tmp_path))

    assert out.endswith(".sql.gz.age")
    # First call is pg_dump, second is age
    assert any(c[0] == "pg_dump" for c in runs)
    assert any(c[0] == "age" for c in runs)
    fake_backend.upload_blob.assert_called_once()
    assert fake_backend.upload_blob.call_args.kwargs["content_type"] == "application/octet-stream"
