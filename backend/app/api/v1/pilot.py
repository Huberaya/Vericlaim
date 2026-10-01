"""Authenticated API routes for the B2B Pilot Pack (Chantier 8)."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.billing.http import require_active_subscription
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
    RetentionPolicyUpdateRequest,
)
from app.pilot.service import (
    declare_retention_policy,
    generate_pre_audit_report,
    get_pilot_dossier_export,
    get_pilot_overview,
    get_retention_policy,
    import_pilot_catalog,
)

router = APIRouter(prefix="/api/v1/pilot", tags=["b2b-pilot-pack"])

# C13 — garde d'abonnement : cette route produit un artefact payant ou une
# dépense réelle (OCR, e-mail tiers, rapport signé). Voir app/billing/http.py.
SUBSCRIPTION_GATE = Depends(require_active_subscription())

DATABASE_DEPENDENCY = Depends(get_db)
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))
CATALOG_MANAGE_DEPENDENCY = Depends(require_permission("catalog:manage", csrf_protected=True))
ORG_READ_DEPENDENCY = Depends(require_permission("organization:read"))
ORG_MANAGE_DEPENDENCY = Depends(require_permission("organization:manage", csrf_protected=True))


@router.get("/overview", response_model=PilotOverviewResponse)
def get_pilot_kpi_overview(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> PilotOverviewResponse:
    """Returns a 360-degree KPI dashboard overview of the tenant's compliance risk, suppliers, products, and evidence coverage."""
    return get_pilot_overview(db, organization_id=principal.organization_id)


@router.get("/pre-audit-report", response_model=PreAuditReportResponse)
def get_pre_audit_synthesis(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> PreAuditReportResponse:
    """Generates an internal pre-audit risk report with remediation advice and cryptographic signature."""
    return generate_pre_audit_report(db, organization_id=principal.organization_id)


@router.post("/import-catalog", response_model=CatalogImportResult, status_code=status.HTTP_201_CREATED, dependencies=[SUBSCRIPTION_GATE])
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
    """Exports the entire tenant compliance dossier for archiving or portability."""
    return get_pilot_dossier_export(db, organization_id=principal.organization_id)


@router.get("/retention-policy", response_model=RetentionPolicyResponse)
def get_compliance_retention_policy(
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RetentionPolicyResponse:
    """Retention, encryption and DPO contact **as declared by the organization**.

    Nothing is defaulted: an undeclared field is null, and the response lists the
    undeclared fields. This endpoint previously answered with the same fabricated
    values for every tenant (a DPO address, an encryption standard, a hosting
    region, 5/10 year durations and a review date) presented as a compliance
    artefact. Those values are gone.
    """
    return get_retention_policy(db, organization_id=principal.organization_id)


@router.put("/retention-policy", response_model=RetentionPolicyResponse)
def declare_compliance_retention_policy(
    payload: RetentionPolicyUpdateRequest,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RetentionPolicyResponse:
    """Record the organization's own retention declaration.

    Requires ``organization:manage``: writing a retention commitment is an
    organizational act, not a read. The declaration is stored with its author and
    timestamp so a third party can see who committed to what, and when.
    """
    return declare_retention_policy(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        payload=payload,
    )
