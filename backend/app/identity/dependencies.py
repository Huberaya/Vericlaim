from __future__ import annotations

import hmac
from dataclasses import dataclass
from re import fullmatch
from typing import Callable, Union
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.identity.api_keys import (
    ApiKeyPrincipal,
    extract_raw_api_key,
    resolve_api_key_principal,
)
from app.identity.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME, token_hmac
from app.identity.service import (
    CurrentSession,
    MembershipView,
    resolve_active_membership,
    resolve_current_session,
)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    current: CurrentSession

    @property
    def user_id(self) -> UUID:
        return self.current.user.id


@dataclass(frozen=True)
class TenantPrincipal(AuthenticatedPrincipal):
    membership_view: MembershipView

    @property
    def organization_id(self) -> UUID:
        return self.membership_view.organization.id

    @property
    def organization(self):
        return self.membership_view.organization

    @property
    def role_code(self) -> str:
        return self.membership_view.role.code

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(str(permission) for permission in self.membership_view.role.permissions_json)


#: Un endpoint tenant accepte deux natures de principal : la session SSO
#: (humaine, cookie, CSRF exigé sur les mutations) et la clé API (machine,
#: en-tête, CSRF inapplicable, périmètres contractuels). Les routes consomment
#: ``permissions``, ``organization_id``, ``organization`` et ``user_id`` ;
#: ``role_code`` et ``membership_view`` n'existent que côté session et ne sont
#: touchés que par les parcours d'administration humaine (périmètres interdits
#: aux clés).
TenantPrincipalLike = Union[TenantPrincipal, ApiKeyPrincipal]


def get_authenticated_principal(
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> AuthenticatedPrincipal:
    current = resolve_current_session(
        db,
        raw_token=request.cookies.get(SESSION_COOKIE_NAME),
        settings=settings,
        request_user_agent=request.headers.get("User-Agent"),
    )
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise.",
        )
    return AuthenticatedPrincipal(current=current)


def get_tenant_principal(
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> TenantPrincipalLike:
    """Résout le principal de la requête : clé API si présentée, sinon session.

    Une requête qui porte une clé API n'est **jamais** réauthentifiée par
    cookie, même si la clé est invalide : elle a explicitement choisi
    l'identité machine.
    """
    raw_api_key = extract_raw_api_key(dict(request.headers))
    if raw_api_key is not None:
        return resolve_api_key_principal(db, raw_key=raw_api_key)

    authenticated = get_authenticated_principal(request, db)
    membership_view = resolve_active_membership(db, authenticated.current)
    if membership_view is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aucune organisation active. Créez ou sélectionnez une organisation avant de poursuivre.",
        )
    return TenantPrincipal(current=authenticated.current, membership_view=membership_view)


def _require_csrf(request: Request, authenticated: AuthenticatedPrincipal) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF manquant ou invalide.")
    expected_hash = authenticated.current.session.csrf_token_hash
    received_hash = token_hmac(header_token, settings.auth_session_secret)
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF manquant ou invalide.")


def require_csrf_authenticated_principal(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> AuthenticatedPrincipal:
    _require_csrf(request, principal)
    return principal


def require_permission(permission: str, *, csrf_protected: bool = False) -> Callable[..., TenantPrincipalLike]:
    def dependency(
        request: Request,
        principal: TenantPrincipalLike = Depends(get_tenant_principal),
    ) -> TenantPrincipalLike:
        # CSRF : uniquement pour les sessions cookie. Une requête portant un
        # en-tête de clé API n'est pas émettable en navigation simple
        # cross-origin : le jeton double-submit n'y a rien à protéger.
        if csrf_protected and isinstance(principal, TenantPrincipal):
            _require_csrf(request, principal)
        if permission not in principal.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Autorisation insuffisante pour cette action.",
            )
        return principal

    return dependency


def request_id_from_request(request: Request) -> str | None:
    candidate = request.headers.get("X-Request-Id", "").strip()
    if len(candidate) > 128 or not fullmatch(r"[A-Za-z0-9._-]+", candidate):
        return None
    return candidate or None
