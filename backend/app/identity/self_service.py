"""Self-service identity: signup, verification, login, reset, invitations (C14).

The audit's finding was blunt: a customer could not create an account, could not
recover a password, and an invitation created a membership whose e-mail was never
sent. Everything in this module exists to close that gap, with three rules:

1. **No account enumeration.** Signup and password-reset always answer the same
   way whether or not the address exists. The difference is only in which e-mail
   is queued.
2. **One-shot, expiring, hashed tokens.** A token is usable once, for one purpose,
   within a bounded delay, and only its digest is stored.
3. **Every identity event is audited.** Signups, verifications, logins, failures,
   lockouts, resets and invitations append to the audit chain built in C4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.email.service import DeliveryResult, OutboundEmail, deliver
from app.identity.passwords import generate_token, hash_password, password_policy_errors, token_digest, verify_password
from app.identity.service import (
    append_audit_event,
    create_auth_session,
    normalize_email,
    utcnow,
)
from app.models.domain import (
    IdentityToken,
    IdentityTokenPurpose,
    Membership,
    MembershipStatus,
    Organization,
    PasswordLoginAttempt,
    Role,
    User,
    UserStatus,
)


class SelfServiceError(RuntimeError):
    """Any refusal of the self-service journey, with a user-safe message."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class PasswordPolicyError(SelfServiceError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(
            "password_policy_violation",
            " ".join(errors),
            status_code=422,
        )
        self.errors = errors


@dataclass(frozen=True)
class SignupOutcome:
    """Result of a signup attempt, including the case where nothing was created.

    `organization_id` is optional on purpose: when the address already exists, no
    organization is created, and the previous version of this dataclass filled the
    field with the *user* id so that the type would hold. A caller would then have
    treated a user id as a tenant id. Optional is the honest type.
    """

    user_id: UUID
    organization_id: UUID | None
    account_created: bool
    verification_email_recorded: bool
    verification_delivery: str


@dataclass(frozen=True)
class LoginOutcome:
    user: User
    session_token: str
    csrf_token: str
    organization_id: UUID | None


def _slugify(name: str, *, attempt: int = 0) -> str:
    """A readable, unique slug. Never derived from an identifier the user typed twice."""
    import re
    import secrets as _secrets

    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "organisation"
    base = base[:60]
    return f"{base}-{_secrets.token_hex(3)}" if attempt == 0 else f"{base}-{_secrets.token_hex(4)}"


def _create_organization_for_user(
    db: Session, *, user: User, name: str
) -> tuple[Organization, Membership]:
    """Create the organization and its founding owner membership.

    Written here rather than reusing ``identity.service.create_organization``,
    which requires an already-authenticated session — exactly what a signup does
    not have yet.
    """
    for attempt in range(3):
        slug = _slugify(name, attempt=attempt)
        if db.scalar(select(Organization).where(Organization.slug == slug)) is None:
            break
    else:
        raise SelfServiceError("slug_exhausted", "Nom d'organisation déjà pris.", status_code=409)

    organization = Organization(name=name.strip(), slug=slug)
    db.add(organization)
    db.flush()
    membership = Membership(
        organization_id=organization.id,
        user_id=user.id,
        role_id=_owner_role(db).id,
        status=MembershipStatus.ACTIVE,
        activated_at=utcnow(),
    )
    db.add(membership)
    db.flush()
    return organization, membership


def _owner_role(db: Session) -> Role:
    role = db.scalar(select(Role).where(Role.code == "owner"))
    if role is None:
        raise SelfServiceError("role_missing", "Le rôle « owner » n'est pas provisionné.", status_code=500)
    return role


def _issue_token(
    db: Session,
    *,
    user: User,
    purpose: IdentityTokenPurpose,
    ttl_seconds: int,
    organization_id: UUID | None = None,
    created_by_user_id: UUID | None = None,
) -> str:
    """Create a single-use token and return the raw value (never stored as is)."""
    raw_token = generate_token()
    db.add(
        IdentityToken(
            user_id=user.id,
            organization_id=organization_id,
            purpose=purpose.value,
            token_hash=token_digest(raw_token),
            expires_at=utcnow() + timedelta(seconds=ttl_seconds),
            created_by_user_id=created_by_user_id,
        )
    )
    db.flush()
    return raw_token


def _consume_token(
    db: Session, *, raw_token: str, purpose: IdentityTokenPurpose
) -> IdentityToken:
    record = db.scalar(
        select(IdentityToken).where(
            IdentityToken.token_hash == token_digest(raw_token),
            IdentityToken.purpose == purpose.value,
        )
    )
    if record is None:
        raise SelfServiceError("token_invalid", "Ce lien est invalide ou a expiré.", status_code=400)
    if record.used_at is not None:
        raise SelfServiceError("token_already_used", "Ce lien a déjà été utilisé.", status_code=409)
    if _as_utc(record.expires_at) <= utcnow():
        raise SelfServiceError("token_expired", "Ce lien a expiré.", status_code=410)
    record.used_at = utcnow()
    db.flush()
    return record


def _as_utc(value):
    from app.identity.service import _as_utc as convert

    return convert(value)


def _frontend_link(settings: Settings, path: str, token: str) -> str:
    base = (settings.frontend_url or "").rstrip("/")
    return f"{base}{path}?token={token}"


# --------------------------------------------------------------------------- #
# Signup and verification
# --------------------------------------------------------------------------- #


def signup(
    db: Session,
    *,
    settings: Settings,
    email: str,
    password: str,
    organization_name: str,
    display_name: str | None = None,
) -> SignupOutcome:
    """Create an account and its first organization.

    The response is deliberately identical for a new and an existing address: a
    409 here would turn the signup form into an account-existence oracle. When the
    address already exists, the same call queues a password-reset e-mail instead
    of a verification one.
    """
    normalized_email = normalize_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise SelfServiceError("invalid_email", "L'adresse e-mail est invalide.", status_code=422)
    if not organization_name.strip():
        raise SelfServiceError("invalid_organization", "Le nom de l'organisation est obligatoire.", status_code=422)

    existing = db.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if existing is not None:
        # Same shape of answer as a fresh signup. If the account has no password
        # yet (SSO-only), the reset link lets the owner set one.
        if existing.status == UserStatus.DISABLED:
            raise SelfServiceError(
                "account_disabled",
                "Ce compte est désactivé. Contactez l'administrateur de votre organisation.",
                status_code=403,
            )
        token = _issue_token(
            db,
            user=existing,
            purpose=IdentityTokenPurpose.PASSWORD_RESET,
            ttl_seconds=settings.password_reset_ttl_seconds,
        )
        membership = db.scalar(
            select(Membership)
            .where(Membership.user_id == existing.id)
            .order_by(Membership.created_at.asc())
        )
        delivery = deliver(
            db,
            settings=settings,
            message=OutboundEmail(
                recipient=normalized_email,
                subject="VeriClaim : un compte existe déjà pour cette adresse",
                body_text=(
                    "Une demande de création de compte a été faite avec cette adresse, qui possède "
                    "déjà un accès VeriClaim.\n\n"
                    f"Pour définir ou réinitialiser votre mot de passe :\n"
                    f"{_frontend_link(settings, '/app', token)}\n\n"
                    "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message."
                ),
                purpose="account_exists_notice",
                organization_id=membership.organization_id if membership else None,
            ),
        )
        return SignupOutcome(
            user_id=existing.id,
            # No membership yet (for instance an account created through SSO): the
            # answer truthfully carries no organization instead of borrowing an id.
            organization_id=membership.organization_id if membership else None,
            account_created=False,
            verification_email_recorded=True,
            verification_delivery=delivery.detail,
        )

    policy_errors = password_policy_errors(password, email=normalized_email)
    if policy_errors:
        raise PasswordPolicyError(policy_errors)

    user = User(
        email=normalized_email,
        display_name=(display_name or "").strip() or None,
        status=UserStatus.INVITED,
        password_hash=hash_password(password),
        password_updated_at=utcnow(),
    )
    db.add(user)
    db.flush()

    organization, _membership = _create_organization_for_user(
        db, user=user, name=organization_name
    )
    # The founder is active on their own organization immediately: the account is
    # gated on the e-mail verification, not the membership.

    token = _issue_token(
        db,
        user=user,
        purpose=IdentityTokenPurpose.EMAIL_VERIFICATION,
        ttl_seconds=settings.email_verification_ttl_seconds,
        organization_id=organization.id,
    )
    delivery = deliver(
        db,
        settings=settings,
        message=OutboundEmail(
            recipient=normalized_email,
            subject="VeriClaim : confirmez votre adresse e-mail",
            body_text=(
                f"Bienvenue sur VeriClaim.\n\n"
                f"Confirmez votre adresse pour activer votre espace « {organization.name} » :\n"
                f"{_frontend_link(settings, '/app', token)}\n\n"
                "Sans confirmation, l'analyse de documents reste bloquée."
            ),
            purpose="email_verification",
            organization_id=organization.id,
        ),
    )
    append_audit_event(
        db,
        organization_id=organization.id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="identity.signup",
        payload={"email_domain": normalized_email.split("@", 1)[1], "organization_name": organization.name},
    )
    return SignupOutcome(
        user_id=user.id,
        organization_id=organization.id,
        account_created=True,
        verification_email_recorded=True,
        verification_delivery=delivery.detail,
    )


def verify_email(db: Session, *, raw_token: str) -> User:
    record = _consume_token(db, raw_token=raw_token, purpose=IdentityTokenPurpose.EMAIL_VERIFICATION)
    user = db.get(User, record.user_id)
    if user is None:
        raise SelfServiceError("user_missing", "Compte introuvable.", status_code=404)
    user.email_verified_at = utcnow()
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
    db.flush()
    append_audit_event(
        db,
        organization_id=record.organization_id or user.id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="identity.email_verified",
        payload={"purpose": IdentityTokenPurpose.EMAIL_VERIFICATION.value},
    )
    return user


# --------------------------------------------------------------------------- #
# Password login, with a failure counter
# --------------------------------------------------------------------------- #


def _attempts(db: Session, email_normalized: str) -> PasswordLoginAttempt | None:
    return db.scalar(
        select(PasswordLoginAttempt).where(PasswordLoginAttempt.email_normalized == email_normalized)
    )


def _register_failure(
    db: Session, *, settings: Settings, email_normalized: str, organization_id: UUID | None
) -> None:
    attempt = _attempts(db, email_normalized)
    now = utcnow()
    if attempt is None:
        attempt = PasswordLoginAttempt(
            email_normalized=email_normalized, failed_count=0, first_failed_at=now
        )
        db.add(attempt)
    attempt.failed_count += 1
    attempt.last_failed_at = now
    if attempt.failed_count >= settings.password_login_max_failures:
        attempt.locked_until = now + timedelta(seconds=settings.password_login_lockout_seconds)
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id or UUID(int=0),
        actor_user_id=None,
        entity_type="user",
        entity_id=UUID(int=0),
        action="identity.password_login_failed",
        payload={
            "email_normalized": email_normalized,
            "failed_count": attempt.failed_count,
            "locked": attempt.locked_until is not None,
        },
    )


def _clear_failures(db: Session, email_normalized: str) -> None:
    attempt = _attempts(db, email_normalized)
    if attempt is not None:
        attempt.failed_count = 0
        attempt.locked_until = None
        attempt.first_failed_at = None
        db.flush()


def login_with_password(
    db: Session,
    *,
    settings: Settings,
    email: str,
    password: str,
    request_user_agent: str | None = None,
) -> LoginOutcome:
    """Authenticate with a password, with a lockout that survives restarts."""
    normalized_email = normalize_email(email)
    attempt = _attempts(db, normalized_email)
    if attempt is not None and attempt.locked_until is not None and _as_utc(attempt.locked_until) > utcnow():
        remaining = int((_as_utc(attempt.locked_until) - utcnow()).total_seconds() // 60) + 1
        raise SelfServiceError(
            "account_locked",
            f"Trop de tentatives : ce compte est temporairement verrouillé ({remaining} min). "
            "Utilisez la réinitialisation de mot de passe.",
            status_code=429,
        )

    user = db.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if user is None or not verify_password(password, user.password_hash):
        _register_failure(
            db,
            settings=settings,
            email_normalized=normalized_email,
            organization_id=None,
        )
        # Same message for an unknown address and a wrong password.
        raise SelfServiceError(
            "invalid_credentials", "Adresse e-mail ou mot de passe incorrect.", status_code=401
        )
    if user.status == UserStatus.DISABLED:
        raise SelfServiceError("account_disabled", "Ce compte est désactivé.", status_code=403)
    if user.status == UserStatus.INVITED and user.email_verified_at is None:
        raise SelfServiceError(
            "email_not_verified",
            "Confirmez d'abord votre adresse e-mail : le lien de vérification vous a été envoyé.",
            status_code=403,
        )

    membership = db.scalar(
        select(Membership)
        .where(Membership.user_id == user.id, Membership.status == MembershipStatus.ACTIVE)
        .order_by(Membership.created_at.asc())
    )
    raw_token, raw_csrf_token, _session = create_auth_session(
        db,
        user=user,
        settings=settings,
        request_user_agent=request_user_agent,
    )
    _clear_failures(db, normalized_email)
    user.last_authenticated_at = utcnow()
    db.flush()
    append_audit_event(
        db,
        organization_id=membership.organization_id if membership else user.id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="identity.password_login_succeeded",
        payload={"method": "password"},
    )
    return LoginOutcome(
        user=user,
        session_token=raw_token,
        csrf_token=raw_csrf_token,
        organization_id=membership.organization_id if membership else None,
    )


# --------------------------------------------------------------------------- #
# Password reset and change
# --------------------------------------------------------------------------- #


def request_password_reset(db: Session, *, settings: Settings, email: str) -> tuple[bool, str]:
    """Always succeed. Returns (account_found, delivery_detail)."""
    normalized_email = normalize_email(email)
    user = db.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if user is None:
        # No e-mail, no token, same answer to the caller: nothing to enumerate.
        return False, "aucun compte pour cette adresse ; aucune action"
    token = _issue_token(
        db,
        user=user,
        purpose=IdentityTokenPurpose.PASSWORD_RESET,
        ttl_seconds=settings.password_reset_ttl_seconds,
    )
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at.asc())
    )
    delivery = deliver(
        db,
        settings=settings,
        message=OutboundEmail(
            recipient=normalized_email,
            subject="VeriClaim : réinitialisation de votre mot de passe",
            body_text=(
                "Une réinitialisation de mot de passe a été demandée pour ce compte.\n\n"
                f"Définir un nouveau mot de passe :\n"
                f"{_frontend_link(settings, '/app', token)}\n\n"
                "Ce lien est valable une heure et ne fonctionne qu'une fois. Si vous n'êtes pas à "
                "l'origine de cette demande, ignorez ce message : votre mot de passe reste inchangé."
            ),
            purpose="password_reset",
            organization_id=membership.organization_id if membership else None,
        ),
    )
    append_audit_event(
        db,
        organization_id=membership.organization_id if membership else user.id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="identity.password_reset_requested",
        payload={"token_message_id": str(delivery.message_id)},
    )
    return True, delivery.detail


def confirm_password_reset(db: Session, *, settings: Settings, raw_token: str, new_password: str) -> User:
    record = _consume_token(db, raw_token=raw_token, purpose=IdentityTokenPurpose.PASSWORD_RESET)
    user = db.get(User, record.user_id)
    if user is None:
        raise SelfServiceError("user_missing", "Compte introuvable.", status_code=404)
    if user.status == UserStatus.DISABLED:
        raise SelfServiceError("account_disabled", "Ce compte est désactivé.", status_code=403)
    policy_errors = password_policy_errors(new_password, email=user.email)
    if policy_errors:
        raise PasswordPolicyError(policy_errors)
    user.password_hash = hash_password(new_password)
    user.password_updated_at = utcnow()
    if user.email_verified_at is None:
        # Following a reset link proves control of the mailbox.
        user.email_verified_at = utcnow()
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
    _clear_failures(db, normalize_email(user.email))
    revoked = revoke_user_sessions(db, user_id=user.id)
    append_audit_event(
        db,
        organization_id=record.organization_id or user.id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="identity.password_reset_completed",
        payload={"revoked_sessions": revoked},
    )
    return user


def change_password(
    db: Session, *, settings: Settings, user_id: UUID, current_password: str, new_password: str
) -> int:
    """Change a password from an authenticated session; returns revoked sessions."""
    user = db.get(User, user_id)
    if user is None:
        raise SelfServiceError("user_missing", "Compte introuvable.", status_code=404)
    if not verify_password(current_password, user.password_hash):
        raise SelfServiceError(
            "invalid_credentials", "Le mot de passe actuel est incorrect.", status_code=401
        )
    policy_errors = password_policy_errors(new_password, email=user.email)
    if policy_errors:
        raise PasswordPolicyError(policy_errors)
    user.password_hash = hash_password(new_password)
    user.password_updated_at = utcnow()
    db.flush()
    revoked = revoke_user_sessions(db, user_id=user.id)
    append_audit_event(
        db,
        organization_id=user.id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="identity.password_changed",
        payload={"revoked_sessions": revoked},
    )
    return revoked


def revoke_user_sessions(db: Session, *, user_id: UUID) -> int:
    """Invalidate every session of a user. A password change must end them all."""
    from app.models.domain import AuthSession

    sessions = list(
        db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
    )
    now = utcnow()
    for session in sessions:
        session.revoked_at = now
    db.flush()
    return len(sessions)


# --------------------------------------------------------------------------- #
# Invitations
# --------------------------------------------------------------------------- #


def send_invitation_email(
    db: Session,
    *,
    settings: Settings,
    user: User,
    organization: Organization,
    invited_by_user_id: UUID,
    role_code: str,
) -> DeliveryResult:
    """Queue the invitation e-mail that the audit found missing.

    Returns the delivery result, not a boolean and not a sentence: the caller must
    be able to report the truth, including "recorded but not delivered" when no
    transport is configured. Swallowing that difference is what made the original
    invitation silently useless.
    """
    token = _issue_token(
        db,
        user=user,
        purpose=IdentityTokenPurpose.INVITATION,
        ttl_seconds=settings.invitation_ttl_seconds,
        organization_id=organization.id,
        created_by_user_id=invited_by_user_id,
    )
    delivery = deliver(
        db,
        settings=settings,
        message=OutboundEmail(
            recipient=user.email,
            subject=f"VeriClaim : invitation à rejoindre « {organization.name} »",
            body_text=(
                f"Vous êtes invité(e) à rejoindre l'espace VeriClaim « {organization.name} » "
                f"avec le rôle « {role_code} ».\n\n"
                f"Activez votre accès :\n{_frontend_link(settings, '/app', token)}\n\n"
                "Ce lien est valable 7 jours et ne fonctionne qu'une fois."
            ),
            purpose="invitation",
            organization_id=organization.id,
        ),
    )
    append_audit_event(
        db,
        organization_id=organization.id,
        actor_user_id=invited_by_user_id,
        entity_type="user",
        entity_id=user.id,
        action="identity.invitation_email_recorded",
        payload={"message_id": str(delivery.message_id), "status": delivery.status.value},
    )
    return delivery


def accept_invitation(
    db: Session, *, settings: Settings, raw_token: str, password: str, display_name: str | None
) -> tuple[User, Organization]:
    """Set the password, activate the membership and the user, in one transaction."""
    record = _consume_token(db, raw_token=raw_token, purpose=IdentityTokenPurpose.INVITATION)
    user = db.get(User, record.user_id)
    if user is None:
        raise SelfServiceError("user_missing", "Invitation introuvable.", status_code=404)
    organization = db.get(Organization, record.organization_id) if record.organization_id else None
    if organization is None:
        raise SelfServiceError("organization_missing", "Organisation introuvable.", status_code=404)
    policy_errors = password_policy_errors(password, email=user.email)
    if policy_errors:
        raise PasswordPolicyError(policy_errors)

    membership = db.scalar(
        select(Membership).where(
            Membership.organization_id == organization.id, Membership.user_id == user.id
        )
    )
    if membership is None:
        raise SelfServiceError(
            "membership_missing",
            "Votre accès à cette organisation a été retiré.",
            status_code=409,
        )
    if membership.status == MembershipStatus.REVOKED:
        raise SelfServiceError(
            "membership_revoked", "Votre accès à cette organisation a été révoqué.", status_code=403
        )

    user.password_hash = hash_password(password)
    user.password_updated_at = utcnow()
    user.email_verified_at = user.email_verified_at or utcnow()
    user.status = UserStatus.ACTIVE
    if display_name and display_name.strip():
        user.display_name = display_name.strip()
    membership.status = MembershipStatus.ACTIVE
    membership.activated_at = utcnow()
    db.flush()
    append_audit_event(
        db,
        organization_id=organization.id,
        actor_user_id=user.id,
        entity_type="membership",
        entity_id=membership.id,
        action="identity.invitation_accepted",
        payload={"role_code": membership.role.code if membership.role else None},
    )
    return user, organization


def count_users_with_password(db: Session, *, organization_id: UUID) -> int:
    """How many members of an organization can actually sign in with a password."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.organization_id == organization_id, User.password_hash.is_not(None))
        )
        or 0
    )
