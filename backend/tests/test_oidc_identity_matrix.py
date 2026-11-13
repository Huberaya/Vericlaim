"""Authentication matrix against a live OIDC provider, without patching the client.

`test_identity_tenancy.py` proves the callback flow with a stubbed client. This
module goes one level deeper: it stands up a real HTTP OpenID Provider and lets
`app.identity.oidc.OidcClient` perform discovery, JWKS retrieval, the authorization
code exchange and the signature/claim validation itself.

That distinction matters for a recette: a stubbed `exchange_callback` proves the
*callback wiring*, never that a forged token is rejected. Here every negative case
is a token a hostile provider could actually have issued, and the assertion is made
against the client's real verification code.

The provider is a disposable local process; it is not Clerk. See
``docs/infrastructure/02-authentification-et-identite.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECETTE_TOOLS = REPOSITORY_ROOT / "tools" / "recette"
if str(RECETTE_TOOLS) not in sys.path:
    sys.path.insert(0, str(RECETTE_TOOLS))

from oidc_stub import RECETTE_USERS, OidcStub  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.identity.oidc import (  # noqa: E402
    OidcClient,
    OidcLoginTransaction,
    OidcProtocolError,
    build_login_transaction,
)


@pytest.fixture(scope="module")
def provider():
    stub = OidcStub(
        issuer="",
        client_id="vericlaim-recette",
        allowed_redirect_uris=("http://localhost:3000/api/v1/auth/callback",),
    ).start()
    try:
        yield stub
    finally:
        stub.stop()


def _client(provider: OidcStub, **overrides: object) -> OidcClient:
    settings = Settings(
        environment="test",
        oidc_issuer=provider.issuer,
        oidc_client_id="vericlaim-recette",
        oidc_redirect_uri="http://localhost:3000/api/v1/auth/callback",
        oidc_discovery_url=f"{provider.issuer}/.well-known/openid-configuration",
        **overrides,
    )
    return OidcClient(settings)


def _issue(provider: OidcStub, *, user: str = "alice", mode: str = "valid") -> OidcLoginTransaction:
    """Perform a real authorization step and return the matching transaction."""

    import httpx
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    transaction = build_login_transaction("/")
    authorization_url = _client(provider).authorization_url(transaction)
    parsed = urlparse(authorization_url)
    query = dict(parse_qsl(parsed.query))
    query.update({"login_hint": user, "mode": mode})
    url = urlunparse(parsed._replace(query=urlencode(query)))
    response = httpx.get(url, follow_redirects=False, timeout=10)
    assert response.status_code == 302, response.text
    callback = dict(parse_qsl(urlparse(response.headers["location"]).query))
    return transaction, callback["code"]  # type: ignore[return-value]


def test_valid_login_yields_a_verified_identity(provider: OidcStub) -> None:
    transaction, code = _issue(provider, user="alice")
    identity = _client(provider).exchange_callback(code=code, transaction=transaction)
    expected = RECETTE_USERS["alice"]
    assert identity.issuer == provider.issuer
    assert identity.subject == expected.subject
    assert identity.email == expected.email


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("expired", "expiration"),
        ("bad_signature", "signature"),
        ("wrong_issuer", "issuer"),
        ("wrong_audience", "audience"),
        ("bad_nonce", "nonce"),
        ("unverified_email", "e-mail non vérifié"),
    ],
)
def test_hostile_tokens_are_refused(provider: OidcStub, mode: str, reason: str) -> None:
    transaction, code = _issue(provider, mode=mode)
    with pytest.raises(OidcProtocolError):
        _client(provider).exchange_callback(code=code, transaction=transaction)


def test_an_authorization_code_cannot_be_replayed(provider: OidcStub) -> None:
    transaction, code = _issue(provider)
    _client(provider).exchange_callback(code=code, transaction=transaction)
    with pytest.raises(OidcProtocolError):
        _client(provider).exchange_callback(code=code, transaction=transaction)


def test_pkce_verifier_must_match_the_challenge(provider: OidcStub) -> None:
    transaction, code = _issue(provider)
    tampered = OidcLoginTransaction(
        state=transaction.state,
        nonce=transaction.nonce,
        code_verifier="x" * 64,
        return_to=transaction.return_to,
    )
    with pytest.raises(OidcProtocolError):
        _client(provider).exchange_callback(code=code, transaction=tampered)


def test_discovery_issuer_mismatch_is_refused(provider: OidcStub) -> None:
    """A discovery document advertising another issuer must not be trusted."""

    settings = Settings(
        environment="test",
        oidc_issuer="https://impostor.invalid",
        oidc_client_id="vericlaim-recette",
        oidc_redirect_uri="http://localhost:3000/api/v1/auth/callback",
        oidc_discovery_url=f"{provider.issuer}/.well-known/openid-configuration",
    )
    with pytest.raises(OidcProtocolError):
        OidcClient(settings).metadata()


def test_unregistered_redirect_uri_is_never_used(provider: OidcStub) -> None:
    """The provider must not send a code to a redirect URI it never registered."""

    import httpx
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    settings = Settings(
        environment="test",
        oidc_issuer=provider.issuer,
        oidc_client_id="vericlaim-recette",
        oidc_redirect_uri="http://localhost:3000/api/v1/auth/callback",
        oidc_discovery_url=f"{provider.issuer}/.well-known/openid-configuration",
    )
    transaction = build_login_transaction("/")
    parsed = urlparse(OidcClient(settings).authorization_url(transaction))
    query = dict(parse_qsl(parsed.query))
    query["redirect_uri"] = "https://attacker.invalid/steal"
    response = httpx.get(
        urlunparse(parsed._replace(query=urlencode(query))), follow_redirects=False, timeout=10
    )
    assert response.status_code == 400
    assert "location" not in response.headers


def test_production_refuses_a_cleartext_identity_provider() -> None:
    """Staging/production must not accept an http:// issuer."""

    # URL policy is enforced by ``get_settings`` (the real entry point), not by
    # the dataclass, so the guard is exercised the way a booting process hits it.
    import os

    previous = dict(os.environ)
    try:
        os.environ.update(
            {
                "APP_ENV": "staging",
                "DATABASE_URL": "postgresql+psycopg://u:p@db.invalid:5432/v",
                "AUTH_SESSION_SECRET": "a" * 48,
                "FRONTEND_URL": "https://app.example.com",
                "CORS_ORIGINS": "https://app.example.com",
                "OIDC_ISSUER": "http://idp.invalid",
                "OIDC_CLIENT_ID": "c",
                "OIDC_REDIRECT_URI": "https://app.example.com/api/v1/auth/callback",
                "DOCUMENT_STORAGE_BACKEND": "s3",
                "DOCUMENT_STORAGE_ENDPOINT": "https://objects.example.com",
                "DOCUMENT_SCANNER_MODE": "clamav",
                "DOCUMENT_CLAMAV_HOST": "clamav.internal",
            }
        )
        from app.core import config as config_module

        with pytest.raises(RuntimeError):
            config_module.get_settings()
    finally:
        os.environ.clear()
        os.environ.update(previous)
