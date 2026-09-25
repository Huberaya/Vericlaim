from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any
from urllib.parse import urlparse

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from app.core.config import Settings
from app.identity.oidc import OidcLoginTransaction, decode_transaction_payload, encode_transaction_payload


SESSION_COOKIE_NAME = "vericlaim_session"
CSRF_COOKIE_NAME = "vericlaim_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
OIDC_TRANSACTION_COOKIE_NAME = "vericlaim_oidc_transaction"
OIDC_TRANSACTION_MAX_AGE_SECONDS = 10 * 60


class AuthenticationStateError(RuntimeError):
    """A signed browser state or session input cannot be safely accepted."""


def create_session_token() -> str:
    # 256 bits of entropy; the raw token is sent only in an HttpOnly cookie.
    return secrets.token_urlsafe(32)


def create_csrf_token() -> str:
    # A separate random value is readable by the same-site frontend and must
    # match a server-side HMAC digest for every unsafe cookie-authenticated call.
    return secrets.token_urlsafe(32)


def token_hmac(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def user_agent_hash(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()


def _transaction_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.auth_session_secret, salt="vericlaim.oidc.transaction.v1")


def sign_login_transaction(settings: Settings, transaction: OidcLoginTransaction) -> str:
    return _transaction_serializer(settings).dumps(encode_transaction_payload(transaction))


def read_login_transaction(settings: Settings, signed_value: str | None) -> OidcLoginTransaction:
    if not signed_value:
        raise AuthenticationStateError("La transaction de connexion est absente ou expirée.")
    try:
        payload = _transaction_serializer(settings).loads(
            signed_value,
            max_age=OIDC_TRANSACTION_MAX_AGE_SECONDS,
        )
    except (BadData, SignatureExpired) as exc:
        raise AuthenticationStateError("La transaction de connexion est invalide ou expirée.") from exc
    if not isinstance(payload, dict):
        raise AuthenticationStateError("La transaction de connexion est invalide.")
    try:
        return decode_transaction_payload(payload)
    except Exception as exc:
        raise AuthenticationStateError("La transaction de connexion est invalide.") from exc


def safe_return_to(value: str | None) -> str:
    """Keep post-login redirects on the configured frontend origin."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


def frontend_redirect_url(settings: Settings, return_to: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}{safe_return_to(return_to)}"


def session_cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.auth_session_ttl_seconds,
    }


def csrf_cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "httponly": False,
        "secure": settings.auth_cookie_secure,
        "samesite": "strict",
        "path": "/",
        "max_age": settings.auth_session_ttl_seconds,
    }


def oidc_transaction_cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/api/v1/auth/callback",
        "max_age": OIDC_TRANSACTION_MAX_AGE_SECONDS,
    }
