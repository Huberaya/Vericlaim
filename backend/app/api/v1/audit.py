from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit.service import (
    generate_cryptographic_audit_certificate,
    list_audit_events,
    verify_audit_chain,
)
from app.core.database import get_db
from app.identity.dependencies import TenantPrincipal, require_permission
from app.models.audit_schemas import (
    AuditChainVerificationResponse,
    AuditEventResponse,
    AuditIntegrityCertificateResponse,
)

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

DATABASE_DEPENDENCY = Depends(get_db)
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))


@router.get("/events", response_model=List[AuditEventResponse])
def get_audit_events(
    entity_type: Optional[str] = Query(default=None, description="Filtrer par type d'entité"),
    action: Optional[str] = Query(default=None, description="Filtrer par action métier"),
    limit: int = Query(default=50, ge=1, le=200, description="Nombre maximum d'événements"),
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> List[AuditEventResponse]:
    """Liste les événements d'audit scellés de l'organisation courante."""
    events = list_audit_events(
        db=db,
        organization_id=principal.organization_id,
        entity_type=entity_type,
        action=action,
        limit=limit,
    )
    return [
        AuditEventResponse(
            id=ev.id,
            organization_id=ev.organization_id,
            actor_user_id=ev.actor_user_id,
            entity_type=ev.entity_type,
            entity_id=ev.entity_id,
            action=ev.action,
            occurred_at=ev.occurred_at,
            request_id=ev.request_id,
            payload_json=ev.payload_json,
            payload_sha256=ev.payload_sha256,
            previous_event_hash=ev.previous_event_hash,
            event_hash=ev.event_hash,
        )
        for ev in events
    ]


@router.get("/verify", response_model=AuditChainVerificationResponse)
def verify_organization_audit_integrity(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> AuditChainVerificationResponse:
    """Vérifie l'intégrité cryptographique complète de la chaîne d'audit de l'organisation."""
    return verify_audit_chain(db, principal.organization_id)


@router.get("/integrity-certificate", response_model=AuditIntegrityCertificateResponse)
def get_audit_integrity_certificate(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> AuditIntegrityCertificateResponse:
    """Génère un certificat d'intégrité cryptographique scellé pour la piste d'audit."""
    org_name = principal.membership_view.organization.name or "Organisation Abonnée"
    try:
        return generate_cryptographic_audit_certificate(
            db=db,
            organization_id=principal.organization_id,
            organization_name=org_name,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        )
