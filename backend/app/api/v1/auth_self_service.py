"""Public self-service identity routes (chantier C14).

Separated from `auth.py` (which holds the enterprise OIDC flow and the
development-only shortcut) because these routes are reachable without any
credential by design, and that is exactly the kind of surface which must be easy
to review in one file.

Anti-enumeration rule applied throughout: signup and password-reset answer the
same way whether or not the address exists. The response never says "compte
existant" — the difference is only which e-mail is queued, and that is recorded
in `email_messages`, not in the HTTP answer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.identity.dependencies import TenantPrincipal, require_permission
from app.identity.self_service import (
    PasswordPolicyError,
    SelfServiceError,
    accept_invitation,
    change_password,
    confirm_password_reset,
    login_with_password,
    request_password_reset,
    signup,
    verify_email,
)
from app.models.identity_schemas import (
    InvitationAcceptRequest,
    PasswordChangeRequest,
    PasswordLoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    SelfServiceMessageResponse,
    SessionIssuedResponse,
    SignupRequest,
    TokenRequest,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth-self-service"])

SESSION_COOKIE = "vericlaim_session"
CSRF_COOKIE = "vericlaim_csrf"
SESSION_DEPENDENCY = Depends(get_db)


def _settings() -> Settings:
    return get_settings()


def _raise(exc: Exception) -> None:
    from fastapi import HTTPException

    if isinstance(exc, PasswordPolicyError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message, "errors": exc.errors},
        ) from exc
    if isinstance(exc, SelfServiceError):
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    raise exc


def _set_session_cookies(response: Response, *, raw_token: str, raw_csrf_token: str, settings: Settings) -> None:
    """Same cookie contract as the OIDC flow: HttpOnly session, readable CSRF."""
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_ttl_seconds,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        raw_csrf_token,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_ttl_seconds,
        path="/",
    )


@router.post("/signup", response_model=SelfServiceMessageResponse, status_code=status.HTTP_202_ACCEPTED)
def create_account(
    body: SignupRequest,
    db: Session = SESSION_DEPENDENCY,
) -> SelfServiceMessageResponse:
    """Create an account and its organization. Identical answer for a known address."""
    settings = _settings()
    try:
        outcome = signup(
            db,
            settings=settings,
            email=str(body.email),
            password=body.password,
            organization_name=body.organization_name,
            display_name=body.display_name,
        )
    except SelfServiceError as exc:
        _raise(exc)
        raise
    db.commit()
    return SelfServiceMessageResponse(
        status="accepted",
        message=(
            "Si cette adresse peut ouvrir un compte, un e-mail vient d'être envoyé avec la suite "
            "du parcours. Vérifiez votre boîte de réception."
        ),
        # The delivery detail is deliberately NOT returned: it would reveal whether
        # the address already existed.
        email_recorded=outcome.verification_email_recorded,
    )


@router.post("/verify-email", response_model=SelfServiceMessageResponse)
def verify_email_address(
    body: TokenRequest,
    db: Session = SESSION_DEPENDENCY,
) -> SelfServiceMessageResponse:
    try:
        user = verify_email(db, raw_token=body.token)
    except SelfServiceError as exc:
        _raise(exc)
        raise
    db.commit()
    return SelfServiceMessageResponse(
        status="verified",
        message=f"L'adresse {user.email} est confirmée. Vous pouvez vous connecter.",
    )


@router.post("/login", response_model=SessionIssuedResponse)
def password_login(
    body: PasswordLoginRequest,
    request: Request,
    response: Response,
    db: Session = SESSION_DEPENDENCY,
) -> SessionIssuedResponse:
    """Password fallback for small teams. Enterprise accounts keep using OIDC."""
    settings = _settings()
    try:
        outcome = login_with_password(
            db,
            settings=settings,
            email=str(body.email),
            password=body.password,
            request_user_agent=request.headers.get("user-agent"),
        )
    except SelfServiceError as exc:
        db.commit()  # a failed attempt must be recorded, not rolled back
        _raise(exc)
        raise
    _set_session_cookies(
        response,
        raw_token=outcome.session_token,
        raw_csrf_token=outcome.csrf_token,
        settings=settings,
    )
    db.commit()
    return SessionIssuedResponse(
        user_id=str(outcome.user.id),
        email=outcome.user.email,
        display_name=outcome.user.display_name,
        active_organization_id=str(outcome.organization_id) if outcome.organization_id else None,
    )


@router.post(
    "/password-reset/request",
    response_model=SelfServiceMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_reset(
    body: PasswordResetRequest,
    db: Session = SESSION_DEPENDENCY,
) -> SelfServiceMessageResponse:
    """Always 202. A different answer would confirm which addresses exist."""
    settings = _settings()
    request_password_reset(db, settings=settings, email=str(body.email))
    db.commit()
    return SelfServiceMessageResponse(
        status="accepted",
        message=(
            "Si un compte existe pour cette adresse, un lien de réinitialisation vient d'être "
            "envoyé. Il est valable une heure."
        ),
        email_recorded=True,
    )


@router.post("/password-reset/confirm", response_model=SelfServiceMessageResponse)
def confirm_reset(
    body: PasswordResetConfirmRequest,
    db: Session = SESSION_DEPENDENCY,
) -> SelfServiceMessageResponse:
    settings = _settings()
    try:
        user = confirm_password_reset(
            db, settings=settings, raw_token=body.token, new_password=body.new_password
        )
    except SelfServiceError as exc:
        _raise(exc)
        raise
    db.commit()
    return SelfServiceMessageResponse(
        status="password_updated",
        message=(
            f"Mot de passe mis à jour pour {user.email}. Toutes les sessions existantes ont été "
            "déconnectées : reconnectez-vous avec le nouveau mot de passe."
        ),
    )


@router.post("/password/change", response_model=SelfServiceMessageResponse)
def change_current_password(
    body: PasswordChangeRequest,
    principal: TenantPrincipal = Depends(require_permission("organization:read", csrf_protected=True)),
    db: Session = SESSION_DEPENDENCY,
) -> SelfServiceMessageResponse:
    """Authenticated change. Requires the current password, by design."""
    settings = _settings()
    try:
        revoked = change_password(
            db,
            settings=settings,
            user_id=principal.user_id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except SelfServiceError as exc:
        _raise(exc)
        raise
    db.commit()
    return SelfServiceMessageResponse(
        status="password_updated",
        message=(
            f"Mot de passe mis à jour. {revoked} session(s) ont été révoquées, y compris la "
            "vôtre : reconnectez-vous."
            if revoked
            else "Mot de passe mis à jour."
        ),
    )


@router.post("/invitations/accept", response_model=SessionIssuedResponse)
def accept_invite(
    body: InvitationAcceptRequest,
    request: Request,
    response: Response,
    db: Session = SESSION_DEPENDENCY,
) -> SessionIssuedResponse:
    """Set the password from an invitation and open the session in one step."""
    settings = _settings()
    try:
        user, organization = accept_invitation(
            db,
            settings=settings,
            raw_token=body.token,
            password=body.password,
            display_name=body.display_name,
        )
    except SelfServiceError as exc:
        _raise(exc)
        raise
    raw_token, raw_csrf_token, _session = _create_session(db, settings=settings, user=user, request=request)
    _set_session_cookies(
        response, raw_token=raw_token, raw_csrf_token=raw_csrf_token, settings=settings
    )
    db.commit()
    return SessionIssuedResponse(
        user_id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        active_organization_id=str(organization.id),
    )


def _create_session(db: Session, *, settings: Settings, user, request: Request):
    from app.identity.service import create_auth_session

    return create_auth_session(
        db, user=user, settings=settings, request_user_agent=request.headers.get("user-agent")
    )


@router.get("/password-policy")
def password_policy() -> dict:
    """The rules the server applies, published so a form can pre-validate them.

    A form that guesses the policy produces avoidable refusals; publishing it costs
    nothing and removes the guessing.
    """
    from app.identity.passwords import (
        COMMON_PASSWORDS,
        MAX_PASSWORD_LENGTH,
        MIN_PASSWORD_LENGTH,
        SCRYPT_N,
        SCRYPT_P,
        SCRYPT_R,
    )

    return {
        "min_length": MIN_PASSWORD_LENGTH,
        "max_length": MAX_PASSWORD_LENGTH,
        "requires_digit": True,
        "requires_letter": True,
        "must_not_contain_email": True,
        "refused_common_passwords": len(COMMON_PASSWORDS),
        # Anti-brute-force settings are published too. The login screen used to
        # announce a fixed number of attempts while the server's default was
        # different — a claim about behaviour that no test could hold, because the
        # interface had no way to know it.
        "login_max_failures": _settings().password_login_max_failures,
        "login_lockout_seconds": _settings().password_login_lockout_seconds,
        "hashing": {
            "scheme": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "note": "Aucun mot de passe n'est stocké en clair, ni journalisé, ni envoyé par e-mail.",
        },
    }
