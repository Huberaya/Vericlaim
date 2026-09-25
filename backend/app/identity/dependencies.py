from __future__ import annotations

import hmac
from dataclasses import dataclass
from re import fullmatch
from typing import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
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
    def role_code(self) -> str:
        return self.membership_view.role.code

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(str(permission) for permission in self.membership_view.role.permissions_json)


def get_authenticated_principal(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthenticatedPrincipal:
    current = resolve_current_session(
        db,
        raw_token=request.cookies.get(SESSION_COOKIE_NAME),
        settings=settings,
    )
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise.",
        )
    return AuthenticatedPrincipal(current=current)


def get_tenant_principal(
    authenticated: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db),
) -> TenantPrincipal:
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


def require_permission(permission: str, *, csrf_protected: bool = False) -> Callable[..., TenantPrincipal]:
    def dependency(request: Request, principal: TenantPrincipal = Depends(get_tenant_principal)) -> TenantPrincipal:
        if csrf_protected:
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
