"""E-04/E-05 — en-têtes de sécurité sur chaque réponse ; surface publique fermée.

E-04 : nosniff, anti-encadrement, politique de référent, CSP de repli, HSTS
seulement en configuration TLS. E-05 : /docs et /openapi.json réversibles à
l'exécution, /metrics authentifié dès qu'un jeton de scrape est configuré, et
configuration de production fail-closed.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_security_headers_are_present_on_every_response():
    with TestClient(app) as client:
        for path in ("/healthz", "/api/v1/auth/status"):
            response = client.get(path)
            assert response.status_code == 200
            headers = response.headers
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert headers["X-Frame-Options"] == "DENY"
            assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
            assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
            # En configuration de test, le TLS n'est pas exigé : pas de HSTS.
            assert "Strict-Transport-Security" not in headers


def test_hsts_is_emitted_when_cookies_are_secure():
    previous = app.state.settings
    app.state.settings = replace(settings, auth_cookie_secure=True)
    try:
        with TestClient(app) as client:
            response = client.get("/healthz")
            assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    finally:
        app.state.settings = previous


def test_docs_lockdown_is_enforced_at_runtime():
    with TestClient(app) as client:
        # Par défaut en test, la documentation reste ouverte (aide au dev).
        assert client.get("/openapi.json").status_code == 200

    previous = app.state.settings
    app.state.settings = replace(settings, api_docs_enabled=False)
    try:
        with TestClient(app) as client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                response = client.get(path)
                assert response.status_code == 404, path
                assert response.headers["Cache-Control"] == "no-store"
            # La fermeture de la doc ne touche pas l'API elle-même.
            assert client.get("/api/v1/auth/status").status_code == 200
    finally:
        app.state.settings = previous


def test_metrics_requires_the_scraping_token_when_configured():
    with TestClient(app) as client:
        # Sans jeton configuré (développement/test), l'export reste ouvert.
        assert client.get("/metrics").status_code == 200

    previous = app.state.settings
    app.state.settings = replace(settings, metrics_auth_token="jeton-de-scraping-0001")
    try:
        with TestClient(app) as client:
            assert client.get("/metrics").status_code == 401
            assert client.get("/metrics", headers={"Authorization": "Bearer mauvais-jeton"}).status_code == 401
            ok = client.get("/metrics", headers={"Authorization": "Bearer jeton-de-scraping-0001"})
            assert ok.status_code == 200
    finally:
        app.state.settings = previous


def test_production_configuration_fails_closed_on_public_surface(monkeypatch):
    """Sans jeton de scrape, la production refuse de démarrer ; la doc est fermée."""
    import pytest

    from app.core.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "false")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.test")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://vericlaim@db.invalid:5432/vericlaim")
    monkeypatch.setenv("DOCUMENT_STORAGE_ENDPOINT", "https://minio.example.test")
    monkeypatch.setenv("DOCUMENT_STORAGE_PUBLIC_ENDPOINT", "https://uploads.example.test")
    monkeypatch.setenv("DOCUMENT_QUARANTINE_BUCKET", "vc-quarantine")
    monkeypatch.setenv("DOCUMENT_CLEAN_BUCKET", "vc-clean")
    monkeypatch.setenv("DOCUMENT_SCANNER_MODE", "clamav")
    monkeypatch.setenv("DOCUMENT_CLAMAV_HOST", "clamav.example.test")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("OIDC_CLIENT_ID", "vericlaim")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example.test/auth/callback")

    monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="METRICS_AUTH_TOKEN"):
        get_settings()

    monkeypatch.setenv("METRICS_AUTH_TOKEN", "jeton-de-scraping-0002")
    production = get_settings()
    assert production.api_docs_enabled is False
    assert production.api_rate_limit_enabled is True
