"""Enterprise service implementing API Keys management, Observability Metrics, SCIM 2.0 Provisioning, and E-Discovery / Legal Holds (Chantier 9)."""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    ApiKey,
    LegalHold,
    Membership,
    MembershipStatus,
    Organization,
    Role,
    User,
    UserStatus,
)
from app.models.enterprise_schemas import (
    ApiKeyCreatedResponse,
    ApiKeySummary,
    EnterpriseAlert,
    EnterpriseMetricsResponse,
    LegalHoldResponse,
    ScimUserCreate,
    ScimUserResponse,
)

START_TIME = time.time()


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------


def generate_secure_api_key() -> tuple[str, str, str]:
    """Generates (raw_key, prefix, sha256_hash). Prefix is 8 chars, entropy is 32 bytes."""
    raw_token = secrets.token_urlsafe(32)
    raw_key = f"vc_live_{raw_token}"
    prefix = raw_key[:12]
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key, prefix, key_hash


def create_organization_api_key(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    name: str,
    scopes: list[str],
    rate_limit_per_minute: int = 120,
    expires_in_days: int | None = 365,
    req_id: str | None = None,
) -> ApiKeyCreatedResponse:
    raw_key, prefix, key_hash = generate_secure_api_key()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expires_in_days) if expires_in_days else None

    api_key_obj = ApiKey(
        id=uuid4(),
        organization_id=organization_id,
        name=name,
        prefix=prefix,
        key_hash=key_hash,
        scopes_json=scopes,
        rate_limit_per_minute=rate_limit_per_minute,
        is_active=True,
        expires_at=expires_at,
        created_by_user_id=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(api_key_obj)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="api_key",
        entity_id=api_key_obj.id,
        action="enterprise.api_key.created",
        payload={"name": name, "prefix": prefix, "scopes": scopes, "rate_limit": rate_limit_per_minute},
        request_id=req_id,
    )
    db.flush()

    return ApiKeyCreatedResponse(
        id=api_key_obj.id,
        name=name,
        prefix=prefix,
        raw_api_key=raw_key,
        scopes=scopes,
        rate_limit_per_minute=rate_limit_per_minute,
        expires_at=expires_at,
        created_at=now,
    )


def list_organization_api_keys(
    db: Session,
    *,
    organization_id: UUID,
) -> list[ApiKeySummary]:
    keys = db.scalars(
        select(ApiKey).where(ApiKey.organization_id == organization_id).order_by(ApiKey.created_at.desc())
    ).all()

    return [
        ApiKeySummary(
            id=k.id,
            name=k.name,
            prefix=k.prefix,
            scopes=k.scopes_json,
            rate_limit_per_minute=k.rate_limit_per_minute,
            is_active=k.is_active,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


def revoke_organization_api_key(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    key_id: UUID,
    req_id: str | None = None,
) -> None:
    key = db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.organization_id == organization_id)
    )
    if not key:
        raise ValueError("Clé API introuvable")

    key.is_active = False
    key.updated_at = datetime.now(timezone.utc)

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="api_key",
        entity_id=key.id,
        action="enterprise.api_key.revoked",
        payload={"prefix": key.prefix, "name": key.name},
        request_id=req_id,
    )
    db.flush()


# ---------------------------------------------------------------------------
# Enterprise Metrics & System Observability
# ---------------------------------------------------------------------------


def get_enterprise_metrics(
    db: Session,
    *,
    organization_id: UUID,
) -> EnterpriseMetricsResponse:
    uptime = time.time() - START_TIME

    # Count only rows visible in this tenant context. An analysis is completed
    # only when its status says so; merely existing is not completion.
    total_completed = db.scalar(
        select(func.count(Analysis.id)).where(
            Analysis.organization_id == organization_id,
            Analysis.status == "completed",
            Analysis.deleted_at.is_(None),
        )
    ) or 0
    active_tenants = db.scalar(
        select(func.count(Organization.id)).where(
            Organization.id == organization_id,
            Organization.status == "active",
        )
    ) or 0

    return EnterpriseMetricsResponse(
        uptime_seconds=round(uptime, 2),
        # This request reached PostgreSQL, but it did not probe object storage,
        # workers or an SLO. Do not turn a successful query into a health claim.
        service_status="responding",
        database_status="connected",
        storage_status="not_probed",
        workers_status="not_probed",
        active_tenants_count=active_tenants,
        total_analyses_completed=total_completed,
        average_analysis_latency_ms=None,
        total_api_requests=None,
        error_rate_percent=None,
        open_alerts_count=None,
        memory_usage_mb=None,
        cpu_utilization_percent=None,
        timestamp=datetime.now(timezone.utc),
    )


def list_enterprise_alerts(
    db: Session,
    *,
    organization_id: UUID,
) -> list[EnterpriseAlert]:
    # No durable alerting backend is connected in this build. An empty list
    # means "no alerts collected", not "the system is verified healthy".
    return []


# ---------------------------------------------------------------------------
# SCIM 2.0 User Provisioning
# ---------------------------------------------------------------------------


def scim_provision_user(
    db: Session,
    *,
    organization_id: UUID,
    user_data: ScimUserCreate,
) -> ScimUserResponse:
    email = user_data.userName
    if user_data.emails:
        email = user_data.emails[0].get("value", email)

    existing_user = db.scalar(
        select(User).where(User.email == email)
    )

    if not existing_user:
        existing_user = User(
            id=uuid4(),
            email=email,
            display_name=user_data.displayName or email.split("@")[0],
            identity_provider="scim",
            external_subject=user_data.externalId or email,
            status=UserStatus.ACTIVE if user_data.active else UserStatus.SUSPENDED,
        )
        db.add(existing_user)
        db.flush()

    # Ensure membership
    membership = db.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id, Membership.user_id == existing_user.id
        )
    )
    if not membership:
        analyst_role = db.scalar(select(Role).where(Role.code == "analyst"))
        if analyst_role:
            membership = Membership(
                id=uuid4(),
                organization_id=organization_id,
                user_id=existing_user.id,
                role_id=analyst_role.id,
                status=MembershipStatus.ACTIVE,
                activated_at=datetime.now(timezone.utc),
            )
            db.add(membership)

    db.flush()

    return ScimUserResponse(
        id=str(existing_user.id),
        userName=existing_user.email,
        displayName=existing_user.display_name,
        active=existing_user.status == UserStatus.ACTIVE,
        emails=[{"value": existing_user.email, "primary": True}],
        meta={
            "resourceType": "User",
            "created": existing_user.created_at.isoformat(),
            "lastModified": existing_user.updated_at.isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# E-Discovery & Legal Holds
# ---------------------------------------------------------------------------


def create_organization_legal_hold(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    case_reference: str,
    reason: str,
    expires_at: datetime | None = None,
    req_id: str | None = None,
) -> LegalHoldResponse:
    hold = LegalHold(
        id=uuid4(),
        organization_id=organization_id,
        case_reference=case_reference,
        reason=reason,
        is_active=True,
        expires_at=expires_at,
        created_by_user_id=actor_user_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(hold)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="legal_hold",
        entity_id=hold.id,
        action="enterprise.legal_hold.created",
        payload={"case_reference": case_reference, "reason": reason},
        request_id=req_id,
    )
    db.flush()

    return LegalHoldResponse(
        id=hold.id,
        case_reference=hold.case_reference,
        reason=hold.reason,
        is_active=hold.is_active,
        created_by=str(actor_user_id),
        created_at=hold.created_at,
        expires_at=hold.expires_at,
    )


def list_organization_legal_holds(
    db: Session,
    *,
    organization_id: UUID,
) -> list[LegalHoldResponse]:
    holds = db.scalars(
        select(LegalHold).where(LegalHold.organization_id == organization_id).order_by(LegalHold.created_at.desc())
    ).all()

    return [
        LegalHoldResponse(
            id=h.id,
            case_reference=h.case_reference,
            reason=h.reason,
            is_active=h.is_active,
            created_by=str(h.created_by_user_id or "system"),
            created_at=h.created_at,
            expires_at=h.expires_at,
        )
        for h in holds
    ]
