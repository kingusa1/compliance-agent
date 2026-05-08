"""Supabase Storage backend — moved from the original app/storage.py.

Public method bodies are byte-identical to the previous module-level
functions; only the wrapping changed (class instead of free functions).
"""
from __future__ import annotations

from typing import Optional

from supabase import Client, create_client

from app.config import settings
from app.storage import StorageBackend


class SupabaseBackend(StorageBackend):
    def __init__(self) -> None:
        self._client: Optional[Client] = None

    def _supabase(self) -> Client:
        if self._client is None:
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )
        return self._client

    def upload_blob(self, local_path: str, remote_key: str, content_type: str = "application/octet-stream") -> str:
        with open(local_path, "rb") as f:
            self._supabase().storage.from_(settings.supabase_storage_bucket).upload(
                path=remote_key,
                file=f,
                file_options={"content-type": content_type, "upsert": "false"},
            )
        return remote_key

    def download_blob(self, remote_key: str, local_path: str) -> str:
        data = self._supabase().storage.from_(settings.supabase_storage_bucket).download(remote_key)
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path

    def signed_url(self, remote_key: str, expires_in: int = 3600) -> str:
        res = self._supabase().storage.from_(settings.supabase_storage_bucket).create_signed_url(
            remote_key, expires_in
        )
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signed_url") or ""
        return getattr(res, "signedURL", "") or getattr(res, "signed_url", "")

    def delete_blob(self, remote_key: str) -> None:
        self._supabase().storage.from_(settings.supabase_storage_bucket).remove([remote_key])
