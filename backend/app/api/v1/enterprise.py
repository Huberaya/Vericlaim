"""Authenticated API endpoints for Enterprise Industrialization (Chantier 9)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.enterprise.service import (
    create_organization_api_key,
    create_organization_legal_hold,
    get_enterprise_metrics,
    list_enterprise_alerts,
    list_organization_api_keys,
    list_organization_legal_holds,
    revoke_organization_api_key,
    scim_provision_user,
)
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.enterprise_schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeySummary,
    EnterpriseAlert,
    EnterpriseMetricsResponse,
    LegalHoldRequest,
    LegalHoldResponse,
    ScimUserCreate,
    ScimUserResponse,
)

router = APIRouter(prefix="/api/v1/enterprise", tags=["enterprise-readiness"])

DATABASE_DEPENDENCY = Depends(get_db)
ORG_READ_DEPENDENCY = Depends(require_permission("organization:read"))
ORG_MANAGE_DEPENDENCY = Depends(require_permission("organization:manage", csrf_protected=True))


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: ApiKeyCreateRequest,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ApiKeyCreatedResponse:
    """Generates a secure API key for programmatic B2B integration."""
    return create_organization_api_key(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        name=body.name,
        scopes=body.scopes,
        rate_limit_per_minute=body.rate_limit_per_minute,
        expires_in_days=body.expires_in_days,
        req_id=request_id_from_request(request),
    )


@router.get("/api-keys", response_model=list[ApiKeySummary])
def list_api_keys(
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> list[ApiKeySummary]:
    """Lists all active and revoked API keys for the organization."""
    return list_organization_api_keys(db, organization_id=principal.organization_id)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: UUID,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> None:
    """Revokes an API key with immediate effect."""
    try:
        revoke_organization_api_key(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            key_id=key_id,
            req_id=request_id_from_request(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/metrics", response_model=EnterpriseMetricsResponse)
def get_metrics(
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EnterpriseMetricsResponse:
    """Returns enterprise system observability, performance metrics, and service status."""
    return get_enterprise_metrics(db, organization_id=principal.organization_id)


@router.get("/alerts", response_model=list[EnterpriseAlert])
def get_alerts(
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> list[EnterpriseAlert]:
    """Returns active enterprise security, system, and quota alerts."""
    return list_enterprise_alerts(db, organization_id=principal.organization_id)


@router.post("/scim/v2/Users", response_model=ScimUserResponse, status_code=status.HTTP_201_CREATED)
def scim_create_user(
    body: ScimUserCreate,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ScimUserResponse:
    """SCIM 2.0 User Provisioning endpoint for IdP synchronization."""
    return scim_provision_user(db, organization_id=principal.organization_id, user_data=body)


@router.post("/legal-holds", response_model=LegalHoldResponse, status_code=status.HTTP_201_CREATED)
def create_legal_hold(
    body: LegalHoldRequest,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> LegalHoldResponse:
    """Places an E-Discovery legal hold on the organization's data."""
    return create_organization_legal_hold(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        case_reference=body.case_reference,
        reason=body.reason,
        expires_at=body.expires_at,
        req_id=request_id_from_request(request),
    )


@router.get("/legal-holds", response_model=list[LegalHoldResponse])
def list_legal_holds(
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> list[LegalHoldResponse]:
    """Lists active and historical legal holds."""
    return list_organization_legal_holds(db, organization_id=principal.organization_id)
