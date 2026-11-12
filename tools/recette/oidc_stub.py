"""Disposable OpenID Connect provider used to qualify the *real* VeriClaim client.

What this is
------------
A small, dependency-light OIDC Authorization Code + PKCE provider that speaks the
same wire protocol as Clerk, Keycloak, Entra ID or Auth0.  VeriClaim's production
code (`app.identity.oidc.OidcClient`) is pointed at it over HTTP; **no VeriClaim
authentication code is patched, mocked or bypassed**.  Discovery, JWKS, the
authorization redirect, the token exchange, the ID-token signature and every
claim check therefore execute for real.

What this is NOT
----------------
This is a *simulated identity provider*, not a Clerk tenant.  It cannot prove
that the Vercel Preview is wired to a real tenant, and every report produced with
it must say so.  It exists so the negative cases the mission requires (expired
token, wrong issuer, wrong audience, bad signature, nonce/state mismatch) can be
reproduced deterministically without touching a production IdP.

Defective tokens are selected per-authorization-request with ``mode=``.  The stub
never leaks a private key: it is generated in memory at start-up.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

VALID_MODES = frozenset(
    {
        "valid",
        "bad_signature",
        "wrong_issuer",
        "wrong_audience",
        "expired",
        "bad_nonce",
        "unverified_email",
        "unknown_subject_ok",
    }
)

_ID_TOKEN_TTL_SECONDS = 300


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class RecetteUser:
    subject: str
    email: str
    display_name: str
    email_verified: bool = True


@dataclass
class _AuthorizationCode:
    nonce: str
    code_challenge: str
    user: RecetteUser
    mode: str
    redirect_uri: str


@dataclass
class OidcStub:
    """A minimal OIDC provider bound to 127.0.0.1 on an ephemeral port."""

    issuer: str
    client_id: str
    allowed_redirect_uris: tuple[str, ...]
    _key: rsa.RSAPrivateKey = field(init=False, repr=False)
    _key_id: str = field(init=False)
    _codes: dict[str, _AuthorizationCode] = field(default_factory=dict, init=False, repr=False)
    _network: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _port: int = field(default=0, init=False, repr=False)
    _probe: Any = field(default=None, init=False, repr=False)
    _server: ThreadingHTTPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    #: Everything the provider issued, so the harness can assert on rejections.
    issued_tokens: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    token_requests: list[dict[str, str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._key_id = secrets.token_hex(8)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "OidcStub":
        self._probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._probe.bind(("127.0.0.1", 0))
        self._port = int(self._probe.getsockname()[1])
        self._probe.close()
        self.issuer = f"http://127.0.0.1:{self._port}"
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003 - stdlib signature
                # Never write token material to the log tail surfaced to operators.
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib signature
                stub._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802 - stdlib signature
                stub._handle_post(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", self._port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="oidc-stub", daemon=True)
        self._thread.start()
        self._network = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
        }
        return self

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("OIDC stub is not running.")
        return int(self._server.server_address[1])

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # ------------------------------------------------------------------ documents
    def jwks(self) -> dict[str, Any]:
        numbers = self._key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self._key_id,
                    "n": _b64url_uint(numbers.n),
                    "e": _b64url_uint(numbers.e),
                }
            ]
        }

    def discovery_document(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self._network["authorization_endpoint"],
            "token_endpoint": self._network["token_endpoint"],
            "jwks_uri": self._network["jwks_uri"],
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["openid", "email", "profile"],
        }

    # ------------------------------------------------------------------ token minting
    def mint_id_token(self, *, user: RecetteUser, nonce: str, mode: str) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": user.subject,
            "iat": now,
            "exp": now + _ID_TOKEN_TTL_SECONDS,
            "nonce": nonce,
            "email": user.email,
            "email_verified": user.email_verified,
            "name": user.display_name,
        }
        signing_key: rsa.RSAPrivateKey = self._key
        headers: dict[str, Any] = {"kid": self._key_id, "typ": "JWT"}

        if mode == "bad_signature":
            # Signed with a key the provider never published: indistinguishable
            # from an on-path attacker re-signing a forged token.
            signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        elif mode == "wrong_issuer":
            claims["iss"] = "https://attacker.invalid/realms/vericlaim"
        elif mode == "wrong_audience":
            claims["aud"] = "another-client-entirely"
        elif mode == "expired":
            claims["iat"] = now - 7200
            claims["exp"] = now - 3600
        elif mode == "bad_nonce":
            claims["nonce"] = "nonce-replayed-from-another-login"
        elif mode == "unverified_email":
            claims["email_verified"] = False
        elif mode not in VALID_MODES:
            raise ValueError(f"Unknown token mode: {mode}")

        token = jwt.encode(claims, signing_key, algorithm="RS256", headers=headers)
        self.issued_tokens.append({"mode": mode, "subject": user.subject, "email": user.email})
        return token

    # ------------------------------------------------------------------ HTTP handling
    def _send(self, handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any], *, cache: str = "no-store") -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", cache)
        handler.end_headers()
        handler.wfile.write(body)

    def _redirect(self, handler: BaseHTTPRequestHandler, location: str) -> None:
        handler.send_response(302)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/.well-known/openid-configuration":
            self._send(handler, 200, self.discovery_document())
            return
        if parsed.path == "/jwks":
            self._send(handler, 200, self.jwks())
            return
        if parsed.path == "/authorize":
            self._authorize(handler, dict(parse_qsl(parsed.query)))
            return
        self._send(handler, 404, {"error": "not_found"})

    def _authorize(self, handler: BaseHTTPRequestHandler, query: dict[str, str]) -> None:
        redirect_uri = query.get("redirect_uri", "")
        state = query.get("state", "")
        if query.get("client_id") != self.client_id:
            self._send(handler, 400, {"error": "invalid_client"})
            return
        if redirect_uri not in self.allowed_redirect_uris:
            # Never redirect to an unregistered URI: that would be an open redirect.
            self._send(handler, 400, {"error": "invalid_redirect_uri"})
            return
        if query.get("response_type") != "code" or not state:
            self._redirect(handler, _with_query(redirect_uri, {"error": "invalid_request", "state": state}))
            return
        if query.get("code_challenge_method") != "S256" or not query.get("code_challenge"):
            self._redirect(handler, _with_query(redirect_uri, {"error": "invalid_request", "state": state}))
            return

        mode = query.get("mode", "valid")
        if mode not in VALID_MODES:
            self._redirect(handler, _with_query(redirect_uri, {"error": "invalid_request", "state": state}))
            return

        # The "IdP session" is chosen by login_hint so the harness can act as two
        # independent users.  A real provider reads this from its own cookie.
        hint = query.get("login_hint", "")
        user = _user_from_hint(hint)
        if user is None:
            self._redirect(handler, _with_query(redirect_uri, {"error": "login_required", "state": state}))
            return

        code = secrets.token_urlsafe(24)
        self._codes[code] = _AuthorizationCode(
            nonce=query.get("nonce", ""),
            code_challenge=query["code_challenge"],
            user=user,
            mode=mode,
            redirect_uri=redirect_uri,
        )
        self._redirect(handler, _with_query(redirect_uri, {"code": code, "state": state}))

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path != "/token":
            self._send(handler, 404, {"error": "not_found"})
            return
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length).decode("utf-8") if length else ""
        form = dict(parse_qsl(raw))
        self.token_requests.append({key: form.get(key, "") for key in ("grant_type", "client_id", "redirect_uri")})

        if form.get("grant_type") != "authorization_code":
            self._send(handler, 400, {"error": "unsupported_grant_type"})
            return
        record = self._codes.pop(form.get("code", ""), None)
        if record is None:
            self._send(handler, 400, {"error": "invalid_grant"})
            return
        if form.get("redirect_uri") != record.redirect_uri:
            self._send(handler, 400, {"error": "invalid_grant"})
            return
        verifier = form.get("code_verifier", "")
        if not verifier:
            self._send(handler, 400, {"error": "invalid_request"})
            return
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        if not secrets.compare_digest(challenge, record.code_challenge):
            # RFC 7636 §4.6: a mismatched verifier must never yield a token.
            self._send(handler, 400, {"error": "invalid_grant"})
            return

        id_token = self.mint_id_token(user=record.user, nonce=record.nonce, mode=record.mode)
        self._send(
            handler,
            200,
            {
                "access_token": secrets.token_urlsafe(32),
                "token_type": "Bearer",
                "expires_in": _ID_TOKEN_TTL_SECONDS,
                "id_token": id_token,
            },
        )


def _with_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    merged = parse_qsl(parsed.query) + [(key, value) for key, value in params.items() if value is not None]
    return urlunparse(parsed._replace(query=urlencode(merged)))


#: Two fictional organisations' operators plus one unaffiliated account.  No real
#: person, employer or address is represented.
#:
#: ``example.com`` is the RFC 2606 documentation domain: it can never resolve or
#: receive mail.  ``.test``/``.invalid``/``.localhost`` look more appropriate but
#: ``email_validator`` classifies them as special-use names and the application
#: correctly refuses such an SSO e-mail, so they cannot exercise the happy path.
RECETTE_USERS: dict[str, RecetteUser] = {
    "alice": RecetteUser(
        subject="recette-alice-subject-0001",
        email="alice.recette@example.com",
        display_name="Alice Recette (org A)",
    ),
    "bob": RecetteUser(
        subject="recette-bob-subject-0002",
        email="bob.recette@example.com",
        display_name="Bob Recette (org B)",
    ),
    "carol": RecetteUser(
        subject="recette-carol-subject-0003",
        email="carol.recette@example.com",
        display_name="Carol Recette (sans organisation)",
    ),
    "alice-unverified": RecetteUser(
        subject="recette-alice-unverified-0004",
        email="alice.unverified@example.com",
        display_name="Unverified",
        email_verified=False,
    ),
}


def _user_from_hint(hint: str) -> RecetteUser | None:
    if hint in RECETTE_USERS:
        return RECETTE_USERS[hint]
    return None
