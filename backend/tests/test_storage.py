"""Unit tests for app.storage — contract verification via mocking.

We deliberately don't hit live Supabase from unit tests (slow, flaky,
leaves artifacts). A separate smoke-test script exercises the real
roundtrip when needed.

After the W3 refactor, the real logic lives in
`app.storage.supabase_backend.SupabaseBackend`. The module-level
shim (`upload_audio`, `download_audio`, `signed_url`) delegates to
`get_backend()`, so we test by patching `create_client` on the backend
module and clearing the cached singleton between tests.
"""
import pytest
from unittest.mock import MagicMock, patch

from app import storage
from app.storage import supabase_backend


@pytest.fixture(autouse=True)
def reset_backend():
    """Drop the lru_cache so each test gets a fresh SupabaseBackend with
    a re-patched `create_client`."""
    storage.get_backend.cache_clear()
    yield
    storage.get_backend.cache_clear()


def test_upload_audio_calls_supabase_with_correct_args(tmp_path):
    test_file = tmp_path / "x.mp3"
    test_file.write_bytes(b"FAKE MP3 BYTES")

    fake_bucket = MagicMock()
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_bucket

    with patch.object(supabase_backend, "create_client", return_value=fake_client) as cc:
        key = storage.upload_audio(str(test_file), "abc/x.mp3")

    assert key == "abc/x.mp3"
    cc.assert_called_once()
    fake_bucket.upload.assert_called_once()
    kwargs = fake_bucket.upload.call_args.kwargs
    assert kwargs["path"] == "abc/x.mp3"
    assert kwargs["file_options"]["content-type"] == "audio/mpeg"


def test_signed_url_returns_string_from_dict_response():
    fake_bucket = MagicMock()
    fake_bucket.create_signed_url.return_value = {"signedURL": "https://example/signed"}
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_bucket

    with patch.object(supabase_backend, "create_client", return_value=fake_client):
        url = storage.signed_url("abc/x.mp3", expires_in=1800)

    assert url == "https://example/signed"
    fake_bucket.create_signed_url.assert_called_once_with("abc/x.mp3", 1800)


def test_signed_url_falls_back_to_snake_case_key():
    fake_bucket = MagicMock()
    fake_bucket.create_signed_url.return_value = {"signed_url": "https://example/snake"}
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_bucket

    with patch.object(supabase_backend, "create_client", return_value=fake_client):
        url = storage.signed_url("abc/x.mp3")

    assert url == "https://example/snake"


def test_download_audio_writes_bytes_to_local_path(tmp_path):
    dest = tmp_path / "out.mp3"
    fake_bucket = MagicMock()
    fake_bucket.download.return_value = b"HELLO AUDIO"
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_bucket

    with patch.object(supabase_backend, "create_client", return_value=fake_client):
        returned = storage.download_audio("abc/x.mp3", str(dest))

    assert returned == str(dest)
    assert dest.read_bytes() == b"HELLO AUDIO"
