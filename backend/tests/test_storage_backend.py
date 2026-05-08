"""Storage backend contract — same suite runs against every implementation.

The ABC defines four methods: upload_blob, download_blob, signed_url,
delete_blob. Each impl must round-trip bytes, mint a working URL, and
delete cleanly. We use an in-memory stub backend that satisfies the ABC
so this suite stays hermetic — no live Supabase/S3 calls. T4's S3 work
will reuse the same fixture pattern against MinIO.
"""
import pathlib

import pytest

from app.storage import StorageBackend, get_backend


class _StubBackend(StorageBackend):
    """In-memory backend; proves the ABC contract is satisfiable and lets
    us exercise upload/signed_url/delete without network."""

    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}

    def upload_blob(self, local_path: str, remote_key: str, content_type: str = "application/octet-stream") -> str:
        with open(local_path, "rb") as f:
            self.uploaded[remote_key] = f.read()
        return remote_key

    def download_blob(self, remote_key: str, local_path: str) -> str:
        with open(local_path, "wb") as f:
            f.write(self.uploaded.get(remote_key, b""))
        return local_path

    def signed_url(self, remote_key: str, expires_in: int = 3600) -> str:
        return f"https://stub.local/{remote_key}?ttl={expires_in}"

    def delete_blob(self, remote_key: str) -> None:
        self.uploaded.pop(remote_key, None)


@pytest.fixture
def backend() -> StorageBackend:
    """Stub backend — proves the ABC is well-formed and exercises the
    contract methods without touching the network."""
    return _StubBackend()


@pytest.fixture
def temp_path(tmp_path: pathlib.Path) -> str:
    p = tmp_path / "audio.bin"
    p.write_bytes(b"hello compliance")
    return str(p)


def test_backend_implements_abc(backend: StorageBackend):
    assert isinstance(backend, StorageBackend)


def test_upload_returns_remote_key(backend: StorageBackend, temp_path: str):
    """upload_blob must return the same remote_key the caller passed in."""
    key = "test/contract/upload.bin"
    out = backend.upload_blob(temp_path, key, content_type="application/octet-stream")
    assert out == key


def test_signed_url_returns_string(backend: StorageBackend):
    """signed_url returns a non-empty string with the remote_key embedded."""
    url = backend.signed_url("abc/def.mp3", expires_in=600)
    assert "abc/def.mp3" in url
    assert "ttl=600" in url


def test_module_shim_reexports_legacy_api():
    """Existing code does `from app.storage import upload_audio, download_audio, signed_url`.
    The package __init__ must re-export those names by delegating to the active backend."""
    from app.storage import download_audio, signed_url, upload_audio
    assert callable(upload_audio)
    assert callable(download_audio)
    assert callable(signed_url)


def test_get_backend_returns_storage_backend_instance():
    """The factory must return an object satisfying the ABC contract."""
    b = get_backend()
    assert isinstance(b, StorageBackend)


import boto3
import pytest
from moto import mock_aws

from app.storage.s3_backend import S3Backend


@pytest.fixture
def s3_backend(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.setenv("S3_ACCESS_KEY", "test")
        monkeypatch.setenv("S3_SECRET_KEY", "test")
        monkeypatch.setenv("S3_BUCKET", "call-audio")
        monkeypatch.setenv("S3_REGION", "us-east-1")
        # Force settings reload — but restore on exit so downstream tests
        # don't see a fresh `Settings` object that has lost any monkeypatched
        # attributes (e.g. test_verdict.py patches dev_all_admin to False on
        # the already-imported `app.config.settings`; if we leave a NEW
        # settings object behind the patch silently no-ops).
        from app import config
        import importlib
        original_settings = config.settings
        importlib.reload(config)
        try:
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="call-audio")
            yield S3Backend()
        finally:
            config.settings = original_settings


def test_s3_upload_then_download_roundtrips(s3_backend, tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"compliance roundtrip")
    s3_backend.upload_blob(str(src), "round/trip.bin", content_type="application/octet-stream")

    dst = tmp_path / "out.bin"
    s3_backend.download_blob("round/trip.bin", str(dst))
    assert dst.read_bytes() == b"compliance roundtrip"


def test_s3_signed_url_contains_key_and_expiry(s3_backend):
    url = s3_backend.signed_url("round/trip.bin", expires_in=300)
    assert "round/trip.bin" in url
    assert "X-Amz-Expires=300" in url


def test_s3_delete_removes_object(s3_backend, tmp_path):
    src = tmp_path / "tmp.bin"
    src.write_bytes(b"to delete")
    s3_backend.upload_blob(str(src), "del/me.bin")
    s3_backend.delete_blob("del/me.bin")
    s3 = boto3.client("s3", region_name="us-east-1")
    listed = s3.list_objects_v2(Bucket="call-audio").get("Contents", [])
    assert "del/me.bin" not in [o["Key"] for o in listed]
