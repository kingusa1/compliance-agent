"""Storage backend selector + back-compat shim.

The ABC `StorageBackend` defines a four-method contract:
    upload_blob(local_path, remote_key, content_type) -> remote_key
    download_blob(remote_key, local_path)             -> local_path
    signed_url(remote_key, expires_in)                -> str
    delete_blob(remote_key)                           -> None

`get_backend()` is the singleton factory. Selection comes from
`settings.storage_backend` (`"supabase"` or `"s3"`).

Legacy call sites (`from app.storage import upload_audio, download_audio,
signed_url`) keep working — module-level functions delegate to the active
backend. New code should call `get_backend()` directly.
"""
from __future__ import annotations

import abc
from functools import lru_cache
from typing import Literal

from app.config import settings


class StorageBackend(abc.ABC):
    """Object-storage contract. Implementations must be stateless beyond
    a single configured client; they're instantiated once per process."""

    @abc.abstractmethod
    def upload_blob(self, local_path: str, remote_key: str, content_type: str = "application/octet-stream") -> str:
        ...

    @abc.abstractmethod
    def download_blob(self, remote_key: str, local_path: str) -> str:
        ...

    @abc.abstractmethod
    def signed_url(self, remote_key: str, expires_in: int = 3600) -> str:
        ...

    @abc.abstractmethod
    def delete_blob(self, remote_key: str) -> None:
        ...


@lru_cache(maxsize=1)
def get_backend() -> StorageBackend:
    name: Literal["supabase", "s3"] = settings.storage_backend
    if name == "s3":
        from app.storage.s3_backend import S3Backend
        return S3Backend()
    from app.storage.supabase_backend import SupabaseBackend
    return SupabaseBackend()


# ─── Legacy shim — preserved API for existing call sites ────────────────
def upload_audio(local_path: str, remote_key: str, content_type: str = "audio/mpeg") -> str:
    return get_backend().upload_blob(local_path, remote_key, content_type)


def download_audio(remote_key: str, local_path: str) -> str:
    return get_backend().download_blob(remote_key, local_path)


def signed_url(remote_key: str, expires_in: int = 3600) -> str:
    return get_backend().signed_url(remote_key, expires_in)
