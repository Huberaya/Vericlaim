"""Chantier « Intégration d'un tenant IdP réel » — le flux SSO complet par les routes.

`test_oidc_identity_matrix.py` prouve le protocole contre un fournisseur OIDC
local réel ; `test_identity_tenancy.py` prouve le câblage des routes avec un
client simulé. Ce module relie les deux couches : le vrai `OidcClient`
(découverte, JWKS, PKCE, validation des jetons) derrière les vraies routes
`/api/v1/auth/login|callback|me`, avec le fournisseur local
`tools/recette/oidc_stub.py`. C'est la dernière couche automatisable avant la
mise en service d'un tenant IdP réel (procédure et checklist dans
`docs/operations/integration-tenant-idp-reel.md`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import pytest
from fastapi.testclient import TestClient

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECETTE_TOOLS = REPOSITORY_ROOT / "tools" / "recette"
if str(RECETTE_TOOLS) not in sys.path:
    sys.path.insert(0, str(RECETTE_TOOLS))

from oidc_stub import RECETTE_USERS, OidcStub  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.database import create_tables  # noqa: E402
from app.identity.oidc import OidcClient, build_login_transaction  # noqa: E402
from app.identity.security import SESSION_COOKIE_NAME  # noqa: E402
from app.main import app  # noqa: E402

REDIRECT_URI = "http://localhost:3000/api/v1/auth/callback"


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


@pytest.fixture()
def provider():
    stub = OidcStub(
        issuer="",
        client_id="vericlaim-recette",
        allowed_redirect_uris=(REDIRECT_URI,),
    ).start()
    try:
        yield stub
    finally:
        stub.stop()


def _real_client(provider: OidcStub) -> OidcClient:
    settings = Settings(
        environment="test",
        oidc_issuer=provider.issuer,
        oidc_client_id="vericlaim-recette",
        oidc_redirect_uri=REDIRECT_URI,
        oidc_discovery_url=f"{provider.issuer}/.well-known/openid-configuration",
    )
    return OidcClient(settings)


def _consent(authorization_url: str, *, user: str = "alice", mode: str = "valid") -> dict[str, str]:
    """Simule la connexion/le consentement au fournisseur et renvoie les
    paramètres (`code`, `state`) du rappel renvoyé vers le redirect URI."""
    parsed = urlparse(authorization_url)
    query = dict(parse_qsl(parsed.query))
    query.update({"login_hint": user, "mode": mode})
    response = httpx.get(
        urlunparse(parsed._replace(query=urlencode(query))), follow_redirects=False, timeout=10
    )
    assert response.status_code == 302, response.text
    return dict(parse_qsl(urlparse(response.headers["location"]).query))


def test_complete_login_flow_through_the_application_routes(provider: OidcStub) -> None:
    """Du clic de connexion à la session ouverte : routes réelles + vrai client.

    Seul le navigateur est simulé (suivre les redirections, consentir) ; tout
    le reste — cookie de transaction signé, PKCE, échange de code, validation
    du jeton, création de session — passe par le code de production."""
    previous = getattr(app.state, "oidc_client", None)
    app.state.oidc_client = _real_client(provider)
    try:
        web = TestClient(app)

        login = web.get("/api/v1/auth/login?return_to=/dashboard", follow_redirects=False)
        assert login.status_code == 302
        authorization = urlparse(login.headers["location"])
        assert authorization.netloc == urlparse(provider.issuer).netloc
        assert dict(parse_qsl(authorization.query))["code_challenge_method"] == "S256"

        params = _consent(login.headers["location"])
        assert params["code"] and params["state"]

        callback = web.get(
            f"/api/v1/auth/callback?code={params['code']}&state={params['state']}",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"].endswith("/dashboard")
        assert web.cookies.get(SESSION_COOKIE_NAME)

        me = web.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["email"] == RECETTE_USERS["alice"].email

        # Le fournisseur a bien vérifié le PKCE contre le challenge de la
        # requête d'autorisation émise par l'application.
        assert provider.token_requests, "l'échange de code doit avoir été enregistré"
    finally:
        app.state.oidc_client = previous


def test_jwks_rotation_is_followed_by_an_automatic_refetch(provider: OidcStub) -> None:
    """Une rotation de clé IdP ne casse pas les connexions : le client re-fetch.

    Scénario listé comme non exécuté dans
    `docs/infrastructure/02-authentification-et-identite.md` (limites de la
    recette) : le JWKS est mis en cache, la clé tourne, puis un jeton signé par
    la nouvelle clé arrive — le client doit re-télécharger le jeu de clés."""
    client = _real_client(provider)
    client._jwks_payload(client.metadata())  # noqa: SLF001 - pré-cache volontaire de l'ancienne clé.
    old_kid = provider.jwks()["keys"][0]["kid"]
    provider.rotate_keys()
    assert provider.jwks()["keys"][0]["kid"] != old_kid

    transaction = build_login_transaction("/")
    params = _consent(client.authorization_url(transaction))
    identity = client.exchange_callback(code=params["code"], transaction=transaction)
    assert identity.email == RECETTE_USERS["alice"].email


def test_unverified_email_is_refused_at_the_application_boundary(provider: OidcStub) -> None:
    """Un e-mail non vérifié par l'IdP ne crée jamais de session : 401 net."""
    previous = getattr(app.state, "oidc_client", None)
    app.state.oidc_client = _real_client(provider)
    try:
        web = TestClient(app)
        login = web.get("/api/v1/auth/login", follow_redirects=False)
        params = _consent(login.headers["location"], mode="unverified_email")
        callback = web.get(
            f"/api/v1/auth/callback?code={params['code']}&state={params['state']}",
            follow_redirects=False,
        )
        assert callback.status_code == 401
        assert web.cookies.get(SESSION_COOKIE_NAME) is None
    finally:
        app.state.oidc_client = previous
