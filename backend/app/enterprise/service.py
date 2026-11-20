"""Enterprise service implementing API Keys management, Observability Metrics, SCIM 2.0 Provisioning, and E-Discovery / Legal Holds (Chantier 9)."""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing.enforcement import assert_entitlement_included
from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    ApiKey,
    AuditEvent,
    LegalHold,
    Membership,
    MembershipStatus,
    Role,
    User,
    UserStatus,
)
from app.core.alerting import (
    evaluate_organization_alerts,
    evaluate_platform_alerts,
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
    # C13 — une clé d'API est un droit de l'offre, pas un quota : Enterprise et Pro
    # l'incluent, Starter non. Le contrôle est ici, dans le service, pour qu'aucun
    # appelant ne puisse émettre un accès que l'offre ne couvre pas.
    assert_entitlement_included(db, organization_id=organization_id, code="api_keys")
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
    db.commit()

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
    db.commit()


# ---------------------------------------------------------------------------
# Enterprise Metrics & System Observability
# ---------------------------------------------------------------------------


def get_enterprise_metrics(
    db: Session,
    *,
    organization_id: UUID,
    runtime: dict[str, object] | None = None,
) -> EnterpriseMetricsResponse:
    """Metrics that are measured, or explicitly null.

    Every number below used to be a literal: `average_analysis_latency_ms=142.5`,
    `error_rate_percent=0.02`, `memory_usage_mb=128.4`, `cpu_utilization_percent=4.2`,
    `total_api_requests = total_events + 100`, and `storage_status="operational"` /
    `workers_status="active"` without checking anything. Worse, the counts were taken
    **without an organization filter**: any tenant could read how many analyses and
    audit events existed on the whole platform.

    Now: tenant-scoped counts come from the organization's own rows; process metrics
    come from the in-process registry; every field that has no honest value on this
    build is `null` and named in `not_measured` — the same convention C8 established
    for the retention policy.
    """
    from app.core.metrics import REGISTRY, error_rate_percent, main_process_memory_mb, process_cpu_percent

    uptime = time.time() - START_TIME
    not_measured: list[str] = []
    runtime = runtime or {}

    total_analyses = int(
        db.scalar(
            select(func.count(Analysis.id)).where(
                Analysis.organization_id == organization_id,
                Analysis.deleted_at.is_(None),
            )
        )
        or 0
    )
    tenant_events = int(
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.organization_id == organization_id)
        )
        or 0
    )

    readiness_checks = {check.name: check for check in runtime.get("readiness_checks", [])}  # type: ignore[union-attr]
    database_check = readiness_checks.get("database")
    storage_check = readiness_checks.get("document_storage")

    if database_check is None:
        database_status = "non mesuré"
        not_measured.append("database_status")
    else:
        database_status = "connected" if database_check.status == "ok" else "unreachable"

    if storage_check is None:
        storage_status = "non mesuré"
        not_measured.append("storage_status")
    elif storage_check.status == "ok":
        storage_status = "operational"
    elif storage_check.status == "disabled":
        storage_status = "disabled"
    else:
        storage_status = "unreachable"

    # Only the *worker* processes count here. The API registers a heartbeat for
    # itself, and counting it would have reported "active" on an instance whose
    # document worker is dead — the exact confusion this field must not create.
    heartbeats = [
        (labels, timestamp)
        for labels, timestamp in (runtime.get("worker_heartbeats") or [])
        if labels.get("worker_kind") != "api"
    ]
    if not heartbeats:
        # Honest default: without a heartbeat, nothing can be affirmed about workers.
        # The API and the workers are separate processes; the API cannot observe the
        # others except through this in-process registry.
        workers_status = "unknown"
    else:
        newest = max(timestamp for _, timestamp in heartbeats)
        workers_status = "active" if (time.time() - newest) < 60 else "stalled"

    latency = REGISTRY.histogram_snapshot("vericlaim_http_request_duration_seconds")
    if latency["count"] > 0:
        average_latency_ms = round(latency["sum"] / latency["count"] * 1000, 3)
    else:
        average_latency_ms = None
        not_measured.append("average_analysis_latency_ms")

    total_api_requests = int(REGISTRY.counter_total("vericlaim_http_requests_total"))
    if total_api_requests == 0:
        not_measured.append("total_api_requests")

    memory_mb = main_process_memory_mb()
    if memory_mb is None:
        not_measured.append("memory_usage_mb")
    cpu_percent = process_cpu_percent()
    if cpu_percent is None:
        not_measured.append("cpu_utilization_percent")

    # The same population as GET /enterprise/alerts: organization alerts plus the
    # instance's readiness. Counting only one of the two here would have published a
    # number that contradicts the other endpoint, one screen away.
    open_alerts = evaluate_organization_alerts(db, organization_id=organization_id)
    open_alerts += evaluate_platform_alerts(
        runtime.get("settings"),  # type: ignore[arg-type]
        storage=runtime.get("storage"),
        scanner=runtime.get("scanner"),
    )

    # `service_status` is derived from what was actually observed, and the reasons
    # are published: a status word without its justification is the defect this
    # chantier is fixing, one field further down.
    reasons: list[str] = []
    if database_check is None or database_check.status != "ok":
        service_status = "unavailable"
        reasons.append("base de données non joignable")
    else:
        if storage_status == "unreachable":
            reasons.append("stockage documentaire injoignable")
        elif storage_status == "disabled":
            reasons.append("stockage documentaire désactivé : aucun import possible")
        if workers_status in {"stalled", "unknown"}:
            reasons.append(
                f"workers : {workers_status}"
                + (
                    " (aucun battement de cœur reçu : les workers tournent dans un autre "
                    "processus, non observable depuis celui-ci)"
                    if workers_status == "unknown"
                    else ""
                )
            )
        service_status = "healthy" if not reasons else "degraded"

    return EnterpriseMetricsResponse(
        uptime_seconds=round(uptime, 2),
        service_status=service_status,
        status_reasons=reasons,
        database_status=database_status,
        storage_status=storage_status,
        workers_status=workers_status,
        # `active_tenants_count` counted every organization in the database and was
        # served to any member of any tenant. It is not a number a tenant may read, so
        # it is null and named.
        active_tenants_count=None,
        total_analyses_completed=total_analyses,
        average_analysis_latency_ms=average_latency_ms,
        total_api_requests=total_api_requests,
        error_rate_percent=error_rate_percent(),
        open_alerts_count=len(open_alerts),
        memory_usage_mb=memory_mb,
        cpu_utilization_percent=cpu_percent,
        tenant_audit_events=tenant_events,
        not_measured=sorted(set(not_measured + ["active_tenants_count"])),
        timestamp=datetime.now(timezone.utc),
    )


def list_enterprise_alerts(
    db: Session,
    *,
    organization_id: UUID,
    runtime: dict[str, object] | None = None,
) -> list[EnterpriseAlert]:
    """Real alerts. The two literals that were returned to every tenant are gone.

    They announced « SSO OIDC / SAML Actif » and a verified Rule Book, both already
    acknowledged — a monitoring screen that could not go red. What replaces them is
    computed from this organization's jobs, its audit chain and its retention
    declaration, plus the instance's own readiness (explicitly labelled as such).
    """
    runtime = runtime or {}
    alerts = evaluate_organization_alerts(db, organization_id=organization_id)
    alerts += evaluate_platform_alerts(
        runtime.get("settings"),  # type: ignore[arg-type]
        storage=runtime.get("storage"),
        scanner=runtime.get("scanner"),
    )
    return [
        EnterpriseAlert(**alert.as_enterprise_alert(index))
        for index, alert in enumerate(alerts, start=1)
    ]


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

    db.commit()

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
    db.commit()

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
