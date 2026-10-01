"""C1 — the credential-less pilot route must never be reachable outside local use.

This file guards the single most severe finding of the pre-launch audit:
``POST /api/v1/auth/dev-login`` minted an owner session with no credential, from
the home page's primary button, in every environment.

Two independent layers are asserted here, because a single layer is not a
control:
  1. registration  — the route is omitted unless explicitly allowed;
  2. handler guard — the handler refuses by itself even if registered.

The subprocess tests boot a real application with a real environment so the
settings singleton is genuinely re-evaluated, instead of being mocked away.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.config import get_settings
from app.main import app

BACKEND_ROOT = Path(__file__).resolve().parents[1]

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


def _boot_and_probe(extra_env: dict[str, str]) -> dict[str, object]:
    """Boot a real app in a subprocess and report whether the route is reachable."""
    script = """
import json, sys
sys.path.insert(0, %r)
from fastapi.testclient import TestClient
from app.core.database import create_tables
from app.main import app
from app.core.config import settings

create_tables()
client = TestClient(app)
client.__enter__()
response = client.post("/api/v1/auth/dev-login")
print(json.dumps({
    "status": response.status_code,
    "enable_dev_login": settings.enable_dev_login,
    "environment": settings.environment,
    "in_openapi": "/api/v1/auth/dev-login" in app.openapi()["paths"],
}))
""" % str(BACKEND_ROOT)

    env = {
        **os.environ,
        "AUTO_CREATE_SCHEMA": "true",
        "DATABASE_URL": "sqlite:///:memory:",
        "APP_ENV": "development",
        **extra_env,
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BACKEND_ROOT),
        timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(f"subprocess boot failed:\n{completed.stderr[-2000:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# Layer 1 — registration
# --------------------------------------------------------------------------- #


def test_pilot_route_is_reachable_when_explicitly_enabled():
    """Local development keeps the pilot shortcut, otherwise the tool is unusable."""
    result = _boot_and_probe({"ENABLE_DEV_LOGIN": "true"})
    assert result["enable_dev_login"] is True
    assert result["status"] == 200


def test_pilot_route_is_absent_when_disabled():
    """With the flag off, the endpoint must not exist at all."""
    result = _boot_and_probe({"ENABLE_DEV_LOGIN": "false"})
    assert result["enable_dev_login"] is False
    assert result["status"] == 404
    assert result["in_openapi"] is False, "an unauthenticated route must not be advertised"


def test_pilot_route_is_not_advertised_in_openapi():
    result = _boot_and_probe({"ENABLE_DEV_LOGIN": "true"})
    assert result["in_openapi"] is False


# --------------------------------------------------------------------------- #
# Layer 2 — handler guard (defence in depth)
# --------------------------------------------------------------------------- #


def test_handler_guard_refuses_even_if_route_is_registered(monkeypatch):
    """A registration mistake must not be enough to expose an owner session."""
    monkeypatch.setattr(
        auth_module,
        "settings",
        replace(auth_module.settings, enable_dev_login=False, environment="production"),
    )
    with pytest.raises(HTTPException) as excinfo:
        auth_module.dev_login(request=None, response=None, db=None)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Not Found", "must not confirm the route exists"


# --------------------------------------------------------------------------- #
# Configuration rule
# --------------------------------------------------------------------------- #


def test_production_boot_disables_the_pilot_route_by_default():
    with patch.dict(os.environ, PRODUCTION_ENV, clear=True):
        assert get_settings().enable_dev_login is False


def test_staging_boot_disables_the_pilot_route_by_default():
    with patch.dict(os.environ, {**PRODUCTION_ENV, "APP_ENV": "staging"}, clear=True):
        assert get_settings().enable_dev_login is False


def test_enabling_the_pilot_route_in_production_is_a_startup_error():
    """Fail loudly rather than silently exposing an unauthenticated session."""
    with patch.dict(os.environ, {**PRODUCTION_ENV, "ENABLE_DEV_LOGIN": "true"}, clear=True):
        with pytest.raises(RuntimeError, match="ENABLE_DEV_LOGIN"):
            get_settings()


def test_enabling_the_pilot_route_in_staging_is_a_startup_error():
    with patch.dict(
        os.environ,
        {**PRODUCTION_ENV, "APP_ENV": "staging", "ENABLE_DEV_LOGIN": "true"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="ENABLE_DEV_LOGIN"):
            get_settings()


def test_development_enables_the_pilot_route_by_default():
    with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
        assert get_settings().enable_dev_login is True


def test_local_opt_out_is_respected():
    with patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEV_LOGIN": "false"}, clear=True):
        assert get_settings().enable_dev_login is False


# --------------------------------------------------------------------------- #
# Non-regression on the whole authentication surface
# --------------------------------------------------------------------------- #

# Routes that may legitimately answer an anonymous caller. Everything else under
# /api/v1/auth must resolve an authenticated principal first. Adding an entry
# here is a security decision that must be justified in review.
#
# This allow-list is what a new contributor sees before adding an authentication
# route; the test below fails if a route serves an anonymous caller without
# being listed, which is exactly the control that was missing when the
# credential-less pilot route shipped enabled in every environment.
PUBLIC_AUTH_ROUTES = {
    "/api/v1/auth/status",  # reports whether SSO is configured
    "/api/v1/auth/login",  # GET starts the OIDC redirect; POST is the password fallback (C14)
    "/api/v1/auth/callback",  # completes the OIDC redirect and mints a session
    "/api/v1/auth/dev-login",  # local-only, gated by settings.enable_dev_login
    # C14 — self-service identity. These routes are anonymous *by definition*: they
    # are the only way an outsider can obtain an account without the editor. Each
    # one is bounded by a value the caller must already hold (a password, a
    # one-shot token) and that bound is asserted in
    # test_the_self_service_routes_refuse_an_anonymous_caller_without_a_valid_secret
    # below — "public" must never be read as "unprotected".
    "/api/v1/auth/signup",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/password-reset/request",
    "/api/v1/auth/password-reset/confirm",
    "/api/v1/auth/invitations/accept",
    "/api/v1/auth/password-policy",  # publishes the rules; contains no secret
}

# Routes that can create a session. Each must be an OIDC leg, the explicitly gated
# local helper, or (C14) a route that verifies a credential the caller holds:
# a password for POST /login, a one-shot invitation/reset token for the others.
# The point of the list is that adding a session-minting route cannot go unnoticed.
SESSION_MINTING_AUTH_ROUTES = {
    "/api/v1/auth/login",
    "/api/v1/auth/callback",
    "/api/v1/auth/dev-login",
    "/api/v1/auth/invitations/accept",  # C14: invitation token -> session
    "/api/v1/auth/password-reset/confirm",  # C14: reset token -> password, then login
}

_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _collect_paths(routes, collected):
    for route in routes:
        path = getattr(route, "path", None)
        if path:
            collected.append(path)
        for attribute in ("routes", "original_router"):
            nested = getattr(route, attribute, None)
            nested_routes = getattr(nested, "routes", None)
            if nested_routes:
                _collect_paths(nested_routes, collected)
    return collected


def _auth_surface() -> set[str]:
    with TestClient(app):
        return {
            path
            for path in _collect_paths(app.routes, [])
            if path.startswith("/api/v1/auth") and "{" not in path
        }


def test_the_authentication_surface_is_enumerable():
    """Fail loudly if route introspection silently stops working."""
    surface = _auth_surface()
    assert surface, "the authentication surface could not be enumerated"
    assert "/api/v1/auth/me" in surface


def test_no_auth_route_serves_an_anonymous_caller_except_the_allow_list():
    """Property: an anonymous caller gets no 2xx from any non-public auth route.

    This is the regression test for the audit finding: had it existed, the
    credential-less pilot route would have failed it in every environment.
    """
    offenders: dict[str, int] = {}
    with TestClient(app) as client:
        for path in sorted(_auth_surface()):
            if path in PUBLIC_AUTH_ROUTES:
                continue
            for method in _HTTP_METHODS:
                response = client.request(method, path)
                if 200 <= response.status_code < 300:
                    offenders[f"{method} {path}"] = response.status_code

    assert not offenders, (
        "authentication route(s) answered an anonymous caller without being listed in "
        f"PUBLIC_AUTH_ROUTES: {offenders}"
    )


def test_session_minting_routes_are_all_explicitly_justified():
    """Every session-minting route is on the watch list, and that list stays honest.

    The original contract was "OIDC, or the gated local helper, and nothing else".
    C14 deliberately changes it: a small team must be able to use the product with a
    password. The list is therefore widened, not silently bypassed — and the routes
    it gains are asserted below to be unusable without the credential they check.
    """
    assert SESSION_MINTING_AUTH_ROUTES <= PUBLIC_AUTH_ROUTES
    assert "/api/v1/auth/dev-login" in SESSION_MINTING_AUTH_ROUTES, (
        "the gated helper must stay on the watch list so it cannot be forgotten"
    )
    assert SESSION_MINTING_AUTH_ROUTES == {
        "/api/v1/auth/login",
        "/api/v1/auth/callback",
        "/api/v1/auth/dev-login",
        "/api/v1/auth/invitations/accept",
        "/api/v1/auth/password-reset/confirm",
    }, "la liste des routes qui créent une session a changé sans revue explicite"


def test_the_self_service_routes_refuse_an_anonymous_caller_without_a_valid_secret():
    """Each C14 public route is bounded by a secret the caller must hold.

    Without this test, adding a route to PUBLIC_AUTH_ROUTES above would be a way to
    make the previous property pass while granting anonymous access for real.
    """
    from app.core.database import create_tables

    create_tables()
    with TestClient(app) as client:
        invented = "jeton-invente-de-64-caracteres-" + "0" * 32
        verify = client.post("/api/v1/auth/verify-email", json={"token": invented})
        assert verify.status_code == 400, verify.text
        assert verify.json()["detail"]["code"] == "token_invalid"

        confirm = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": invented, "new_password": "Boussole-Verte-2026"},
        )
        assert confirm.status_code == 400, confirm.text
        assert confirm.json()["detail"]["code"] == "token_invalid"

        accept = client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": invented, "password": "Boussole-Verte-2026"},
        )
        assert accept.status_code == 400, accept.text
        assert accept.json()["detail"]["code"] == "token_invalid"

        login = client.post(
            "/api/v1/auth/login",
            json={"email": "personne@laposte.net", "password": "Boussole-Verte-2026"},
        )
        assert login.status_code == 401, login.text
        assert login.json()["detail"]["code"] == "invalid_credentials"

        # The reset request answers 202 to everyone (anti-enumeration) but must not
        # create a session or a token for an address it has never seen.
        anonymous = client.post(
            "/api/v1/auth/password-reset/request", json={"email": "personne@laposte.net"}
        )
        assert anonymous.status_code == 202
        assert "vericlaim_session" not in anonymous.cookies
