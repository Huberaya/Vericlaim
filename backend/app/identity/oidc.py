from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from email_validator import EmailNotValidError, validate_email

from app.core.config import Settings


class OidcProtocolError(RuntimeError):
    """An OIDC provider response is unavailable, invalid or unsafe to trust."""


@dataclass(frozen=True)
class OidcMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class OidcIdentity:
    issuer: str
    subject: str
    email: str
    display_name: str | None


@dataclass(frozen=True)
class OidcLoginTransaction:
    state: str
    nonce: str
    code_verifier: str
    return_to: str


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _normalise_email(value: str) -> str:
    return value.strip().lower()


def build_login_transaction(return_to: str) -> OidcLoginTransaction:
    # RFC 7636 permits 43–128 URL-safe characters for the verifier.
    return OidcLoginTransaction(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        return_to=return_to,
    )


class OidcClient:
    """Minimal OIDC Authorization Code + PKCE client with strict ID-token validation."""

    _ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        if not settings.oidc_configured:
            raise ValueError("OIDC is not configured.")
        self._settings = settings
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False)
        self._metadata: OidcMetadata | None = None
        self._jwks: dict[str, Any] | None = None

    def metadata(self) -> OidcMetadata:
        if self._metadata is not None:
            return self._metadata
        try:
            response = self._http.get(str(self._settings.oidc_discovery_url), headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcProtocolError("Le fournisseur SSO est indisponible.") from exc

        required = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
        if not isinstance(payload, dict) or any(not isinstance(payload.get(key), str) for key in required):
            raise OidcProtocolError("La découverte OIDC est incomplète.")
        if payload["issuer"].rstrip("/") != str(self._settings.oidc_issuer).rstrip("/"):
            raise OidcProtocolError("L’émetteur annoncé par le fournisseur SSO est inattendu.")

        self._metadata = OidcMetadata(
            issuer=payload["issuer"].rstrip("/"),
            authorization_endpoint=payload["authorization_endpoint"],
            token_endpoint=payload["token_endpoint"],
            jwks_uri=payload["jwks_uri"],
        )
        return self._metadata

    def authorization_url(self, transaction: OidcLoginTransaction) -> str:
        metadata = self.metadata()
        challenge = _base64url(hashlib.sha256(transaction.code_verifier.encode("ascii")).digest())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.oidc_client_id,
                "redirect_uri": self._settings.oidc_redirect_uri,
                "scope": "openid email profile",
                "state": transaction.state,
                "nonce": transaction.nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in metadata.authorization_endpoint else "?"
        return f"{metadata.authorization_endpoint}{separator}{query}"

    def exchange_callback(self, *, code: str, transaction: OidcLoginTransaction) -> OidcIdentity:
        metadata = self.metadata()
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "client_id": self._settings.oidc_client_id,
            "code_verifier": transaction.code_verifier,
        }
        auth: tuple[str, str] | None = None
        if self._settings.oidc_client_secret:
            # client_secret_basic avoids placing the credential in form/log payloads.
            auth = (str(self._settings.oidc_client_id), self._settings.oidc_client_secret)
            payload.pop("client_id")
        try:
            response = self._http.post(
                metadata.token_endpoint,
                data=payload,
                auth=auth,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token_payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcProtocolError("L’échange du code SSO a échoué.") from exc

        id_token = token_payload.get("id_token") if isinstance(token_payload, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise OidcProtocolError("Le fournisseur SSO n’a pas retourné de jeton d’identité.")
        return self._validate_id_token(id_token, transaction.nonce, metadata)

    def _jwks_payload(self, metadata: OidcMetadata) -> dict[str, Any]:
        if self._jwks is not None:
            return self._jwks
        try:
            response = self._http.get(metadata.jwks_uri, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcProtocolError("Les clés publiques du fournisseur SSO sont indisponibles.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise OidcProtocolError("Le jeu de clés publiques OIDC est invalide.")
        self._jwks = payload
        return payload

    def _validate_id_token(self, id_token: str, nonce: str, metadata: OidcMetadata) -> OidcIdentity:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OidcProtocolError("Le jeton d’identité OIDC est invalide.") from exc

        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in self._ALLOWED_ALGORITHMS or not isinstance(key_id, str) or not key_id:
            raise OidcProtocolError("L’algorithme ou la clé du jeton OIDC est refusé.")

        jwk = next(
            (
                item
                for item in self._jwks_payload(metadata).get("keys", [])
                if isinstance(item, dict) and item.get("kid") == key_id and item.get("use", "sig") == "sig"
            ),
            None,
        )
        if jwk is None:
            # A key rotation can happen between the first and second request.
            self._jwks = None
            jwk = next(
                (
                    item
                    for item in self._jwks_payload(metadata).get("keys", [])
                    if isinstance(item, dict) and item.get("kid") == key_id and item.get("use", "sig") == "sig"
                ),
                None,
            )
        if jwk is None:
            raise OidcProtocolError("La clé de signature OIDC est inconnue.")

        try:
            signing_key = jwt.PyJWK.from_dict(jwk).key
            claims = jwt.decode(
                id_token,
                key=signing_key,
                algorithms=[algorithm],
                audience=self._settings.oidc_client_id,
                issuer=metadata.issuer,
                options={"require": ["exp", "iat", "sub"]},
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise OidcProtocolError("La signature ou les claims du jeton OIDC sont invalides.") from exc

        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcProtocolError("Le nonce OIDC ne correspond pas à la demande de connexion.")
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self._settings.oidc_client_id:
            raise OidcProtocolError("Le client autorisé du jeton OIDC est ambigu.")

        email = claims.get("email")
        email_verified = claims.get("email_verified")
        if not isinstance(email, str) or not email.strip() or email_verified not in {True, "true", "True"}:
            raise OidcProtocolError("Le fournisseur SSO doit fournir une adresse e-mail vérifiée.")
        try:
            normalised_email = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise OidcProtocolError("L’adresse e-mail fournie par le SSO est invalide.") from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OidcProtocolError("Le jeton OIDC ne contient pas de sujet utilisable.")
        display_name = claims.get("name") or claims.get("preferred_username")
        return OidcIdentity(
            issuer=metadata.issuer,
            subject=subject,
            email=_normalise_email(normalised_email),
            display_name=display_name.strip() if isinstance(display_name, str) and display_name.strip() else None,
        )


def encode_transaction_payload(transaction: OidcLoginTransaction) -> dict[str, str]:
    """Small serializable payload used by the signed short-lived callback cookie."""
    return {
        "state": transaction.state,
        "nonce": transaction.nonce,
        "code_verifier": transaction.code_verifier,
        "return_to": transaction.return_to,
    }


def decode_transaction_payload(value: dict[str, Any]) -> OidcLoginTransaction:
    try:
        transaction = OidcLoginTransaction(
            state=str(value["state"]),
            nonce=str(value["nonce"]),
            code_verifier=str(value["code_verifier"]),
            return_to=str(value["return_to"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OidcProtocolError("La transaction de connexion est invalide.") from exc
    if not transaction.state or not transaction.nonce or not transaction.code_verifier:
        raise OidcProtocolError("La transaction de connexion est incomplète.")
    return transaction
