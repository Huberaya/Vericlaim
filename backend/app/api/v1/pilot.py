"""Authenticated API routes for the B2B Pilot Pack (Chantier 8)."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.pilot_schemas import (
    CatalogImportRequest,
    CatalogImportResult,
    PilotOverviewResponse,
    PreAuditReportResponse,
    RetentionPolicyResponse,
)
from app.pilot.service import (
    generate_pre_audit_report,
    get_pilot_dossier_export,
    get_pilot_overview,
    get_retention_policy,
    import_pilot_catalog,
)

router = APIRouter(prefix="/api/v1/pilot", tags=["b2b-pilot-pack"])

DATABASE_DEPENDENCY = Depends(get_db)
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))
CATALOG_MANAGE_DEPENDENCY = Depends(require_permission("catalog:manage", csrf_protected=True))
ORG_READ_DEPENDENCY = Depends(require_permission("organization:read"))


@router.get("/overview", response_model=PilotOverviewResponse)
def get_pilot_kpi_overview(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> PilotOverviewResponse:
    """Return tenant inventory counts and human decisions, not a legal compliance score."""
    return get_pilot_overview(db, organization_id=principal.organization_id)


@router.get("/pre-audit-report", response_model=PreAuditReportResponse)
def get_pre_audit_synthesis(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> PreAuditReportResponse:
    """Return a tenant inventory with a content hash, not a legal evaluation or signature."""
    return generate_pre_audit_report(db, organization_id=principal.organization_id)


@router.post("/import-catalog", response_model=CatalogImportResult, status_code=status.HTTP_201_CREATED)
def import_catalog_batch(
    body: CatalogImportRequest,
    request: Request,
    principal: TenantPrincipal = CATALOG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> CatalogImportResult:
    """Performs a controlled, idempotent batch import of suppliers and products during pilot onboarding."""
    return import_pilot_catalog(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        items=body.items,
        req_id=request_id_from_request(request),
    )


@router.get("/export-dossier")
def export_tenant_dossier(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> dict[str, Any]:
    """Export a metadata inventory, not a complete binary/evidence archive."""
    return get_pilot_dossier_export(db, organization_id=principal.organization_id)


@router.get("/retention-policy", response_model=RetentionPolicyResponse)
def get_compliance_retention_policy(
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RetentionPolicyResponse:
    """Return policy configuration status; unknown values are never promised as deployed controls."""
    return get_retention_policy(db, organization_id=principal.organization_id)
