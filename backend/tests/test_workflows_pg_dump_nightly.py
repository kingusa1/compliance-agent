"""Inngest function pg_dump_nightly invokes the script and surfaces failures."""
import asyncio
import subprocess
from unittest.mock import patch

import pytest

from app.workflows.pg_dump_nightly import _run_backup


@pytest.mark.asyncio
async def test_run_backup_returns_remote_key_on_success():
    with patch("app.workflows.pg_dump_nightly.run_pg_dump", return_value="backups/2026/05/07/x.sql.gz"):
        result = await _run_backup()
    assert result == {"remote_key": "backups/2026/05/07/x.sql.gz"}


@pytest.mark.asyncio
async def test_run_backup_raises_on_pg_dump_failure():
    with patch("app.workflows.pg_dump_nightly.run_pg_dump", side_effect=subprocess.CalledProcessError(1, "pg_dump")):
        with pytest.raises(subprocess.CalledProcessError):
            await _run_backup()
