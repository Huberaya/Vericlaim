"""C3 — the runtime environment must be explicit and a database outage must fail closed.

Two independent guarantees are asserted here:

1. **Platform coherence.** ``APP_ENV`` defaults to ``development``, which is
   permissive by design: it re-enables the credential-less pilot route, defaults
   to a local SQLite file, disables secure cookies and accepts localhost CORS.
   On a shared platform that default is never correct, so an unset or
   inconsistent ``APP_ENV`` is a startup error.

2. **No silent database substitution.** An unreachable database raises
   :class:`DatabaseUnavailableError` (mapped to HTTP 503) instead of quietly
   swapping in a throwaway SQLite file that loses writes and bypasses
   PostgreSQL row-level security.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.database as database
from app.core.config import get_settings

# --------------------------------------------------------------------------- #
# A complete, valid production environment. Individual tests override one key
# so that a failure can only come from that key.
# --------------------------------------------------------------------------- #

PRODUCTION_ENV = {
    "APP_ENV": "production",
    "DATABASE_URL": "postgresql+psycopg://user:password@db.internal:5432/vericlaim",
    "AUTH_SESSION_SECRET": "p" * 48,
    "AUTH_COOKIE_SECURE": "true",
    "FRONTEND_URL": "https://app.example",
    "CORS_ORIGINS": "https://app.example",
    "OIDC_ISSUER": "https://idp.example",
    "OIDC_CLIENT_ID": "vericlaim-web",
    "OIDC_REDIRECT_URI": "https://app.example/api/v1/auth/callback",
    "DOCUMENT_STORAGE_BACKEND": "s3",
    "DOCUMENT_STORAGE_ENDPOINT": "https://minio.example",
    "DOCUMENT_STORAGE_PUBLIC_ENDPOINT": "https://objects.example",
    "DOCUMENT_STORAGE_SSE_MODE": "aes256",
    "DOCUMENT_QUARANTINE_BUCKET": "vericlaim-quarantine",
    "DOCUMENT_CLEAN_BUCKET": "vericlaim-documents",
    "DOCUMENT_SCANNER_MODE": "clamav",
    "DOCUMENT_CLAMAV_HOST": "clamav.example",
    # C7: a signed report is impossible without this key, so production
    # configuration cannot be valid without it.
    "REPORT_SIGNING_KEY": "Rk7Qm2Xv9BpL4Tn6Ys3Wz8Hd5Jc0Fa1Ug",
}


def _settings_without(**removed: str) -> dict[str, str]:
    return {key: value for key, value in PRODUCTION_ENV.items() if key not in removed}


# --------------------------------------------------------------------------- #
# 1. Platform coherence
# --------------------------------------------------------------------------- #


def test_missing_app_env_on_a_shared_platform_is_a_startup_error():
    """Vercel sets VERCEL_ENV; without APP_ENV the permissive default would apply."""
    with patch.dict(os.environ, {**_settings_without(APP_ENV=""), "VERCEL_ENV": "production"}, clear=True):
        with pytest.raises(RuntimeError, match="APP_ENV must be set explicitly"):
            get_settings()


def test_platform_presence_alone_is_enough_to_require_app_env():
    """VERCEL=1 without VERCEL_ENV must still refuse the development default."""
    with patch.dict(os.environ, {**_settings_without(APP_ENV=""), "VERCEL": "1"}, clear=True):
        with pytest.raises(RuntimeError, match="APP_ENV must be set explicitly"):
            get_settings()


def test_development_on_a_production_platform_is_refused():
    """The exact failure this chantier exists for: prod host, development mode."""
    with patch.dict(
        os.environ,
        {**PRODUCTION_ENV, "APP_ENV": "development", "VERCEL_ENV": "production"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="inconsistent"):
            get_settings()


def test_test_environment_on_a_production_platform_is_refused():
    with patch.dict(
        os.environ,
        {**PRODUCTION_ENV, "APP_ENV": "test", "VERCEL_ENV": "production"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="inconsistent"):
            get_settings()


def test_production_platform_with_production_app_env_is_accepted():
    with patch.dict(os.environ, {**PRODUCTION_ENV, "VERCEL_ENV": "production"}, clear=True):
        settings = get_settings()
        assert settings.environment == "production"
        assert settings.is_production_like is True
        assert settings.enable_dev_login is False


# --------------------------------------------------------------------------- #
# C7 — a report signature is worthless if the key is guessable
# --------------------------------------------------------------------------- #


def test_a_report_signing_key_is_mandatory_in_production():
    """Without a key no report can be bound to its analysis, so booting is refused."""
    with patch.dict(
        os.environ, _settings_without(REPORT_SIGNING_KEY=""), clear=True
    ):
        with pytest.raises(RuntimeError, match="REPORT_SIGNING_KEY must be configured"):
            get_settings()


@pytest.mark.parametrize(
    "weak_key",
    [
        "k" * 48,  # long, but one distinct character
        "replace-with-a-long-random-report-signing-key-at-least-32-chars",  # .env.example
        "changeme" * 6,
        "vericlaim-report-signing-key-2026-development",
        "a" * 15 + "b" * 4,  # too short
    ],
)
def test_a_long_but_guessable_report_signing_key_is_refused(weak_key):
    """A length check alone accepts 'changeme' repeated; that key can be forged."""
    with patch.dict(
        os.environ, {**PRODUCTION_ENV, "REPORT_SIGNING_KEY": weak_key}, clear=True
    ):
        with pytest.raises(RuntimeError, match="REPORT_SIGNING_KEY must be a random value"):
            get_settings()


def test_a_random_report_signing_key_is_accepted():
    with patch.dict(
        os.environ,
        {**PRODUCTION_ENV, "REPORT_SIGNING_KEY": "Rk7Qm2Xv9BpL4Tn6Ys3Wz8Hd5Jc0Fa1Ug"},
        clear=True,
    ):
        assert get_settings().report_signing_key == "Rk7Qm2Xv9BpL4Tn6Ys3Wz8Hd5Jc0Fa1Ug"


def test_the_signing_key_is_never_published_in_the_runtime_description():
    """Diagnostics must not leak the secret that makes forged reports detectable."""
    secret = "Rk7Qm2Xv9BpL4Tn6Ys3Wz8Hd5Jc0Fa1Ug"
    with patch.dict(
        os.environ, {**PRODUCTION_ENV, "REPORT_SIGNING_KEY": secret}, clear=True
    ):
        description = json.dumps(get_settings().describe_runtime(), default=str)
    assert secret not in description


def test_preview_platform_accepts_staging():
    staging = {
        **_settings_without(APP_ENV=""),
        "APP_ENV": "staging",
        "AUTH_COOKIE_SECURE": "true",
    }
    with patch.dict(os.environ, {**staging, "VERCEL_ENV": "preview"}, clear=True):
        assert get_settings().environment == "staging"


def test_preview_platform_refuses_development():
    with patch.dict(
        os.environ,
        {**_settings_without(APP_ENV=""), "APP_ENV": "development", "VERCEL_ENV": "preview"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="inconsistent"):
            get_settings()


def test_development_platform_accepts_development():
    with patch.dict(
        os.environ,
        {"APP_ENV": "development", "VERCEL_ENV": "development"},
        clear=True,
    ):
        assert get_settings().environment == "development"


def test_unknown_platform_value_is_refused():
    with patch.dict(
        os.environ,
        {**PRODUCTION_ENV, "VERCEL_ENV": "not-a-real-tier"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="unsupported value"):
            get_settings()


def test_a_local_machine_without_platform_variables_still_works():
    """The guard must not break local development."""
    with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
        settings = get_settings()
        assert settings.environment == "development"
        assert settings.auto_create_schema is True


# --------------------------------------------------------------------------- #
# 2. The local SQLite fallback is opt-in and never leaves development
# --------------------------------------------------------------------------- #


def test_local_sqlite_fallback_is_disabled_by_default():
    with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
        assert get_settings().allow_local_sqlite_fallback is False


def test_local_sqlite_fallback_can_be_opted_into_locally():
    with patch.dict(
        os.environ,
        {"APP_ENV": "development", "ALLOW_LOCAL_SQLITE_FALLBACK": "true"},
        clear=True,
    ):
        assert get_settings().allow_local_sqlite_fallback is True


def test_local_sqlite_fallback_is_refused_in_production():
    with patch.dict(
        os.environ,
        {**PRODUCTION_ENV, "ALLOW_LOCAL_SQLITE_FALLBACK": "true"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="ALLOW_LOCAL_SQLITE_FALLBACK"):
            get_settings()


def test_local_sqlite_fallback_is_refused_in_staging():
    with patch.dict(
        os.environ,
        {**_settings_without(APP_ENV=""), "APP_ENV": "staging", "ALLOW_LOCAL_SQLITE_FALLBACK": "true"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="ALLOW_LOCAL_SQLITE_FALLBACK"):
            get_settings()


def test_local_sqlite_fallback_is_refused_on_a_shared_platform():
    with patch.dict(
        os.environ,
        {
            **_settings_without(APP_ENV=""),
            "APP_ENV": "development",
            "ALLOW_LOCAL_SQLITE_FALLBACK": "true",
            "VERCEL_ENV": "development",
        },
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="ALLOW_LOCAL_SQLITE_FALLBACK"):
            get_settings()


# --------------------------------------------------------------------------- #
# 3. An unreachable database fails closed at request time
# --------------------------------------------------------------------------- #

UNREACHABLE_URL = "postgresql+psycopg://nobody:nobody@127.0.0.1:1/none"


@pytest.fixture()
def unreachable_database(monkeypatch):
    engine = create_engine(UNREACHABLE_URL, connect_args={"connect_timeout": 2})
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False),
    )
    return engine


def test_unreachable_database_raises_instead_of_substituting_sqlite(unreachable_database, monkeypatch):
    monkeypatch.setattr(
        database,
        "settings",
        replace(database.settings, allow_local_sqlite_fallback=False, environment="development"),
    )
    generator = database.get_db()
    with pytest.raises(database.DatabaseUnavailableError):
        next(generator)


def test_unreachable_database_raises_in_production(unreachable_database, monkeypatch):
    monkeypatch.setattr(
        database,
        "settings",
        replace(database.settings, allow_local_sqlite_fallback=False, environment="production"),
    )
    generator = database.get_db()
    with pytest.raises(database.DatabaseUnavailableError):
        next(generator)


def test_explicit_local_opt_in_still_substitutes_the_fallback(unreachable_database, monkeypatch):
    """The development convenience survives, but only when asked for."""
    monkeypatch.setattr(
        database,
        "settings",
        replace(database.settings, allow_local_sqlite_fallback=True, environment="development"),
    )
    generator = database.get_db()
    session = next(generator)
    try:
        assert session is not None
        assert session.get_bind().dialect.name == "sqlite"
    finally:
        generator.close()


def test_fallback_is_refused_even_when_opted_in_outside_development(unreachable_database, monkeypatch):
    """Defence in depth: the flag alone must not enable the fallback in production."""
    monkeypatch.setattr(
        database,
        "settings",
        replace(database.settings, allow_local_sqlite_fallback=True, environment="production"),
    )
    generator = database.get_db()
    with pytest.raises(database.DatabaseUnavailableError):
        next(generator)


def test_api_returns_503_when_the_database_is_unreachable(unreachable_database, monkeypatch):
    """A client must receive an explicit 503, never an empty database."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(
        database,
        "settings",
        replace(database.settings, allow_local_sqlite_fallback=False, environment="production"),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/suppliers")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["code"] == "database_unavailable"
    assert "injoignable" in body["detail"]


# --------------------------------------------------------------------------- #
# 4. The runtime summary is safe to log
# --------------------------------------------------------------------------- #


def test_runtime_summary_exposes_no_secret():
    summary = get_settings().describe_runtime()
    serialised = " ".join(str(value) for value in summary.values())
    assert "password" not in serialised.lower()
    assert "secret" not in summary
    assert "@" not in serialised, "credentials must not appear in the startup summary"
    assert set(summary) >= {
        "environment",
        "database_backend",
        "dev_login_enabled",
        "secure_cookies",
    }
