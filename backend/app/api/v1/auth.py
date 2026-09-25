from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.v1.presenters import present_membership, present_user
from app.core.config import settings
from app.core.database import get_db
from app.identity.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
    require_csrf_authenticated_principal,
)
from app.identity.oidc import OidcProtocolError, build_login_transaction
from app.identity.security import (
    CSRF_COOKIE_NAME,
    OIDC_TRANSACTION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AuthenticationStateError,
    csrf_cookie_kwargs,
    frontend_redirect_url,
    oidc_transaction_cookie_kwargs,
    read_login_transaction,
    safe_return_to,
    session_cookie_kwargs,
    sign_login_transaction,
)
from app.identity.service import (
    AuthorizationInvariantError,
    IdentityConflictError,
    IdentityNotFoundError,
    append_audit_event,
    create_auth_session,
    list_active_memberships,
    provision_oidc_user,
    revoke_auth_session,
    set_active_organization,
    set_db_request_context,
)
from app.models.identity_schemas import (
    ActiveOrganizationRequest,
    AuthMeResponse,
    AuthStatusResponse,
    MembershipResponse,
)


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


def _oidc_client_or_503(request: Request):
    client = getattr(request.app.state, "oidc_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le SSO OIDC n’est pas configuré pour cet environnement.",
        )
    return client


@router.get("/status", response_model=AuthStatusResponse)
def authentication_status(request: Request) -> AuthStatusResponse:
    return AuthStatusResponse(oidc_configured=getattr(request.app.state, "oidc_client", None) is not None)


@router.get("/login", include_in_schema=False)
def start_oidc_login(request: Request, return_to: str | None = None) -> RedirectResponse:
    oidc_client = _oidc_client_or_503(request)
    transaction = build_login_transaction(safe_return_to(return_to))
    try:
        authorization_url = oidc_client.authorization_url(transaction)
    except OidcProtocolError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        OIDC_TRANSACTION_COOKIE_NAME,
        sign_login_transaction(settings, transaction),
        **oidc_transaction_cookie_kwargs(settings),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback", include_in_schema=False)
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connexion SSO refusée ou annulée.")
    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Réponse SSO incomplète.")

    try:
        transaction = read_login_transaction(settings, request.cookies.get(OIDC_TRANSACTION_COOKIE_NAME))
        if not hmac.compare_digest(transaction.state, state):
            raise AuthenticationStateError("Le state de connexion ne correspond pas.")
        identity = _oidc_client_or_503(request).exchange_callback(code=code, transaction=transaction)
        provisioned = provision_oidc_user(db, identity)
        user = provisioned.user
        for membership in provisioned.activated_memberships:
            set_db_request_context(db, user_id=user.id, organization_id=membership.organization_id)
            append_audit_event(
                db,
                organization_id=membership.organization_id,
                actor_user_id=user.id,
                entity_type="membership",
                entity_id=membership.id,
                action="membership.activated_by_sso",
                payload={"user_id": str(user.id)},
            )
        raw_session_token, raw_csrf_token, auth_session = create_auth_session(
            db,
            user=user,
            settings=settings,
            request_user_agent=request.headers.get("User-Agent"),
        )
    except AuthenticationStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La tentative de connexion a expiré ou est invalide.") from exc
    except OidcProtocolError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except IdentityConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AuthorizationInvariantError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    response = RedirectResponse(frontend_redirect_url(settings, transaction.return_to), status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(SESSION_COOKIE_NAME, raw_session_token, **session_cookie_kwargs(settings))
    response.set_cookie(CSRF_COOKIE_NAME, raw_csrf_token, **csrf_cookie_kwargs(settings))
    response.delete_cookie(OIDC_TRANSACTION_COOKIE_NAME, path="/api/v1/auth/callback")
    response.headers["Cache-Control"] = "no-store"
    # Access the id before returning so SQLAlchemy has flushed all generated keys.
    assert auth_session.id
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    _: AuthenticatedPrincipal = Depends(require_csrf_authenticated_principal),
    db: Session = Depends(get_db),
) -> Response:
    revoke_auth_session(db, raw_token=request.cookies.get(SESSION_COOKIE_NAME), settings=settings)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/me", response_model=AuthMeResponse)
def current_user(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db),
) -> AuthMeResponse:
    memberships = list_active_memberships(db, principal.user_id)
    return AuthMeResponse(
        user=present_user(principal.current.user),
        active_organization_id=principal.current.session.active_organization_id,
        memberships=[present_membership(view) for view in memberships],
    )


@router.post("/active-organization", response_model=MembershipResponse)
def switch_active_organization(
    body: ActiveOrganizationRequest,
    principal: AuthenticatedPrincipal = Depends(require_csrf_authenticated_principal),
    db: Session = Depends(get_db),
) -> MembershipResponse:
    try:
        membership = set_active_organization(db, principal.current, body.organization_id)
    except IdentityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return present_membership(membership)
