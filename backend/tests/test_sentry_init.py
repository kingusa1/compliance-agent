import importlib

import pytest


def test_init_sentry_no_op_when_dsn_empty(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "")
    from app.main import init_sentry
    # Must not raise even when no DSN
    assert init_sentry() is None


def test_init_sentry_initialises_when_dsn_set(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@glitchtip.example.com/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "test")
    # Reload settings so env override is read; main reads settings.sentry_dsn
    # at call time so reloading config alone is enough.
    from app import config
    original_settings = config.settings
    importlib.reload(config)
    # Re-import main so `from app.config import settings` rebinds to the
    # freshly-reloaded settings instance.
    from app import main
    original_main_settings = main.settings if hasattr(main, "settings") else None
    importlib.reload(main)
    try:
        main.init_sentry()
        import sentry_sdk
        assert sentry_sdk.Hub.current.client is not None
    finally:
        # Restore the original settings + main objects so downstream tests
        # that hold references via `from app.config import settings` (e.g.
        # `app.auth.settings`) keep seeing the same instance and any
        # monkeypatches applied to it stick.
        config.settings = original_settings
        if original_main_settings is not None:
            main.settings = original_main_settings
