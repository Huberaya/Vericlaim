from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.core.database import canonical_json, sha256_json
from app.identity.oidc import OidcIdentity
from app.identity.roles import SYSTEM_ROLE_BY_CODE, ensure_system_roles
from app.identity.security import create_csrf_token, create_session_token, token_hmac, user_agent_hash
from app.models.domain import (
    AuditEvent,
    AuthSession,
    Membership,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    Role,
    User,
    UserStatus,
)


class IdentityConflictError(RuntimeError):
    pass


class IdentityNotFoundError(RuntimeError):
    pass


class AuthorizationInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class CurrentSession:
    session: AuthSession
    user: User


@dataclass(frozen=True)
class ProvisionedUser:
    user: User
    activated_memberships: tuple[Membership, ...]


@dataclass(frozen=True)
class MembershipView:
    membership: Membership
    organization: Organization
    role: Role


@dataclass(frozen=True)
class OrganizationMemberView:
    membership: Membership
    organization: Organization
    role: Role
    user: User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def normalize_email(value: str) -> str:
    return value.strip().lower()


def set_db_request_context(db: Session, *, user_id: UUID, organization_id: UUID | None) -> None:
    """Install request-local values consumed by PostgreSQL RLS policies.

    SQLite has no RLS implementation; tests still retain the context in
    ``Session.info`` so application services can assert and inspect it.
    """
    db.info["current_user_id"] = user_id
    db.info["current_organization_id"] = organization_id
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT set_config('app.current_user_id', :value, true)"), {"value": str(user_id)})
    db.execute(
        text("SELECT set_config('app.current_organization_id', :value, true)"),
        {"value": str(organization_id) if organization_id else ""},
    )


def provision_oidc_user(db: Session, identity: OidcIdentity) -> ProvisionedUser:
    """Bind a verified OIDC subject without allowing an e-mail takeover."""
    email = normalize_email(identity.email)
    user = db.scalar(
        select(User).where(
            User.identity_provider == identity.issuer,
            User.external_subject == identity.subject,
        )
    )
    if user is None:
        same_email = db.scalar(select(User).where(func.lower(User.email) == email))
        if same_email is not None:
            if same_email.identity_provider or same_email.external_subject:
                raise IdentityConflictError(
                    "Cette adresse e-mail est déjà liée à une autre identité SSO. Contactez un administrateur."
                )
            user = same_email
        else:
            user = User(email=email, status=UserStatus.INVITED)
            db.add(user)

        user.identity_provider = identity.issuer
        user.external_subject = identity.subject

    if user.status == UserStatus.DISABLED:
        raise AuthorizationInvariantError("Ce compte est désactivé.")
    if user.email != email:
        # An IdP subject changing e-mail is security-sensitive: it requires an
        # administrator-mediated merge rather than silently changing identity.
        raise IdentityConflictError(
            "L’adresse e-mail de cette identité SSO a changé et doit être rapprochée par un administrateur."
        )
    user.status = UserStatus.ACTIVE
    if identity.display_name:
        user.display_name = identity.display_name
    authenticated_at = utcnow()
    user.last_authenticated_at = authenticated_at
    # An invitation deliberately creates no password or bearer link. The first
    # successful SSO login with the invited, verified e-mail activates it.
    activated_memberships = tuple(
        db.scalars(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.status == MembershipStatus.INVITED,
            )
        ).all()
    )
    for membership in activated_memberships:
        membership.status = MembershipStatus.ACTIVE
        membership.activated_at = authenticated_at
    db.flush()
    return ProvisionedUser(user=user, activated_memberships=activated_memberships)


def _active_membership_statement(user_id: UUID) -> Select[tuple[Membership, Organization, Role]]:
    return (
        select(Membership, Organization, Role)
        .join(Organization, Organization.id == Membership.organization_id)
        .join(Role, Role.id == Membership.role_id)
        .where(
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.ACTIVE,
            Organization.status == OrganizationStatus.ACTIVE,
        )
        .order_by(Organization.name.asc())
    )


def list_active_memberships(db: Session, user_id: UUID) -> list[MembershipView]:
    return [MembershipView(membership=row[0], organization=row[1], role=row[2]) for row in db.execute(_active_membership_statement(user_id)).all()]


def _first_active_organization_id(db: Session, user_id: UUID) -> UUID | None:
    return db.scalar(_active_membership_statement(user_id).with_only_columns(Membership.organization_id).limit(1))


def create_auth_session(
    db: Session,
    *,
    user: User,
    settings: Settings,
    request_user_agent: str | None,
) -> tuple[str, str, AuthSession]:
    raw_token = create_session_token()
    raw_csrf_token = create_csrf_token()
    session = AuthSession(
        user_id=user.id,
        active_organization_id=_first_active_organization_id(db, user.id),
        token_hash=token_hmac(raw_token, settings.auth_session_secret),
        csrf_token_hash=token_hmac(raw_csrf_token, settings.auth_session_secret),
        expires_at=utcnow() + timedelta(seconds=settings.auth_session_ttl_seconds),
        user_agent_hash=user_agent_hash(request_user_agent),
    )
    db.add(session)
    db.flush()
    return raw_token, raw_csrf_token, session


def resolve_current_session(db: Session, *, raw_token: str | None, settings: Settings) -> CurrentSession | None:
    if not raw_token:
        return None
    token_hash = token_hmac(raw_token, settings.auth_session_secret)
    auth_session = db.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.user))
        .where(AuthSession.token_hash == token_hash)
    )
    if auth_session is None or auth_session.revoked_at is not None or _as_utc(auth_session.expires_at) <= utcnow():
        return None
    user = auth_session.user
    if user.status != UserStatus.ACTIVE:
        return None
    return CurrentSession(session=auth_session, user=user)


def revoke_auth_session(db: Session, *, raw_token: str | None, settings: Settings) -> None:
    if not raw_token:
        return
    token_hash = token_hmac(raw_token, settings.auth_session_secret)
    auth_session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = utcnow()
        db.flush()


def resolve_active_membership(db: Session, current: CurrentSession) -> MembershipView | None:
    active_organization_id = current.session.active_organization_id
    if active_organization_id is None:
        return None
    row = db.execute(
        _active_membership_statement(current.user.id).where(Membership.organization_id == active_organization_id)
    ).one_or_none()
    if row is None:
        current.session.active_organization_id = None
        db.flush()
        return None
    view = MembershipView(membership=row[0], organization=row[1], role=row[2])
    set_db_request_context(db, user_id=current.user.id, organization_id=view.organization.id)
    return view


def set_active_organization(db: Session, current: CurrentSession, organization_id: UUID) -> MembershipView:
    row = db.execute(
        _active_membership_statement(current.user.id).where(Membership.organization_id == organization_id)
    ).one_or_none()
    if row is None:
        raise IdentityNotFoundError("Organisation inaccessible ou membership inactif.")
    current.session.active_organization_id = organization_id
    db.flush()
    view = MembershipView(membership=row[0], organization=row[1], role=row[2])
    set_db_request_context(db, user_id=current.user.id, organization_id=organization_id)
    return view


def _role_by_code(db: Session, role_code: str) -> Role:
    if role_code not in SYSTEM_ROLE_BY_CODE:
        raise IdentityNotFoundError("Rôle inconnu.")
    role = db.scalar(select(Role).where(Role.code == role_code))
    if role is None:
        ensure_system_roles(db)
        role = db.scalar(select(Role).where(Role.code == role_code))
    if role is None:
        raise IdentityNotFoundError("Rôle système indisponible.")
    return role


def create_organization(
    db: Session,
    *,
    current: CurrentSession,
    name: str,
    slug: str,
) -> MembershipView:
    owner_role = _role_by_code(db, "owner")
    organization = Organization(name=name.strip(), slug=slug.strip().lower(), status=OrganizationStatus.ACTIVE)
    db.add(organization)
    db.flush()
    membership = Membership(
        organization_id=organization.id,
        user_id=current.user.id,
        role_id=owner_role.id,
        status=MembershipStatus.ACTIVE,
        activated_at=utcnow(),
    )
    db.add(membership)
    current.session.active_organization_id = organization.id
    db.flush()
    set_db_request_context(db, user_id=current.user.id, organization_id=organization.id)
    return MembershipView(membership=membership, organization=organization, role=owner_role)


def update_organization_name(db: Session, organization: Organization, *, name: str) -> Organization:
    organization.name = name.strip()
    db.flush()
    return organization


def list_organization_members(db: Session, organization_id: UUID) -> list[OrganizationMemberView]:
    rows = db.execute(
        select(Membership, Organization, Role, User)
        .join(Organization, Organization.id == Membership.organization_id)
        .join(Role, Role.id == Membership.role_id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == organization_id)
        .order_by(User.email.asc())
    ).all()
    return [
        OrganizationMemberView(membership=row[0], organization=row[1], role=row[2], user=row[3])
        for row in rows
    ]


def _membership_with_user_and_role(db: Session, organization_id: UUID, user_id: UUID) -> tuple[Membership, User, Role] | None:
    return db.execute(
        select(Membership, User, Role)
        .join(User, User.id == Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .where(Membership.organization_id == organization_id, Membership.user_id == user_id)
    ).one_or_none()


def _ensure_actor_can_manage_role(*, actor_role_code: str, target_role_code: str) -> None:
    if target_role_code == "owner" and actor_role_code != "owner":
        raise AuthorizationInvariantError("Seul un propriétaire peut attribuer ou modifier le rôle propriétaire.")


def _assert_not_last_owner(db: Session, *, organization_id: UUID, membership: Membership, role: Role) -> None:
    if role.code != "owner" or membership.status != MembershipStatus.ACTIVE:
        return
    active_owners = db.scalar(
        select(func.count())
        .select_from(Membership)
        .join(Role, Role.id == Membership.role_id)
        .where(
            Membership.organization_id == organization_id,
            Membership.status == MembershipStatus.ACTIVE,
            Role.code == "owner",
        )
    )
    if int(active_owners or 0) <= 1:
        raise AuthorizationInvariantError("Une organisation doit conserver au moins un propriétaire actif.")


def invite_or_provision_member(
    db: Session,
    *,
    organization_id: UUID,
    email: str,
    role_code: str,
    invited_by_user_id: UUID,
    actor_role_code: str,
) -> tuple[Membership, User, Role]:
    _ensure_actor_can_manage_role(actor_role_code=actor_role_code, target_role_code=role_code)
    target_role = _role_by_code(db, role_code)
    normalised_email = normalize_email(email)
    user = db.scalar(select(User).where(func.lower(User.email) == normalised_email))
    if user is None:
        user = User(email=normalised_email, status=UserStatus.INVITED)
        db.add(user)
        db.flush()

    existing = _membership_with_user_and_role(db, organization_id, user.id)
    if existing is not None:
        membership, _, existing_role = existing
        if membership.status == MembershipStatus.ACTIVE:
            raise IdentityConflictError("Cette personne est déjà membre actif de l’organisation.")
        _assert_not_last_owner(db, organization_id=organization_id, membership=membership, role=existing_role)
        membership.role_id = target_role.id
        membership.status = MembershipStatus.INVITED
        membership.invited_by_user_id = invited_by_user_id
        membership.activated_at = None
    else:
        membership = Membership(
            organization_id=organization_id,
            user_id=user.id,
            role_id=target_role.id,
            status=MembershipStatus.INVITED,
            invited_by_user_id=invited_by_user_id,
        )
        db.add(membership)
    db.flush()
    return membership, user, target_role


def update_member_role(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    role_code: str,
    actor_role_code: str,
) -> tuple[Membership, User, Role]:
    existing = _membership_with_user_and_role(db, organization_id, user_id)
    if existing is None:
        raise IdentityNotFoundError("Membership introuvable.")
    membership, user, current_role = existing
    # Managing an existing owner is itself owner-only. Without this check an
    # admin could demote a co-owner simply by choosing a non-owner target role.
    _ensure_actor_can_manage_role(actor_role_code=actor_role_code, target_role_code=current_role.code)
    target_role = _role_by_code(db, role_code)
    _ensure_actor_can_manage_role(actor_role_code=actor_role_code, target_role_code=target_role.code)
    if current_role.code == "owner" and target_role.code != "owner":
        _assert_not_last_owner(db, organization_id=organization_id, membership=membership, role=current_role)
    membership.role_id = target_role.id
    db.flush()
    return membership, user, target_role


def revoke_member(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    actor_role_code: str,
) -> tuple[Membership, User, Role]:
    existing = _membership_with_user_and_role(db, organization_id, user_id)
    if existing is None:
        raise IdentityNotFoundError("Membership introuvable.")
    membership, user, role = existing
    _ensure_actor_can_manage_role(actor_role_code=actor_role_code, target_role_code=role.code)
    _assert_not_last_owner(db, organization_id=organization_id, membership=membership, role=role)
    membership.status = MembershipStatus.REVOKED
    db.flush()
    return membership, user, role


def append_audit_event(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID | None,
    entity_type: str,
    entity_id: UUID | None,
    action: str,
    payload: dict[str, Any],
    request_id: str | None = None,
) -> AuditEvent:
    """Append a tenant-scoped event without mutating historical events.

    PostgreSQL callers are already in a request transaction and RLS context.
    ``FOR UPDATE`` serializes the common non-empty-chain case; a stronger
    cross-writer serialization strategy remains a dedicated audit workstream.
    """
    previous = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
        .with_for_update()
    )
    previous_hash = previous.event_hash if previous else None
    occurred_at = utcnow()
    payload_hash = sha256_json(payload)
    material = {
        "organization_id": str(organization_id),
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id else None,
        "action": action,
        "occurred_at": occurred_at.isoformat(),
        "request_id": request_id,
        "payload": json_safe(payload),
        "payload_sha256": payload_hash,
        "previous_event_hash": previous_hash,
    }
    event = AuditEvent(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        occurred_at=occurred_at,
        request_id=request_id,
        payload_json=json_safe(payload),
        payload_sha256=payload_hash,
        previous_event_hash=previous_hash,
        event_hash=sha256_json(material),
    )
    db.add(event)
    db.flush()
    return event


def json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    # Canonical round-trip makes UUID/date payloads safe for the JSON column.
    import json

    return json.loads(canonical_json(payload))
