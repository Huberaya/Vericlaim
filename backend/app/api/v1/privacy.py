"""C20 — retention, erasure and export routes.

Every route here returns **what was actually done**, not what was asked: an erasure
refused because of a legal hold answers 409 with the case reference, an undisclosed
retention policy answers a purge plan that deletes nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.documents.storage import ObjectStorageError
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.privacy_schemas import (
    ErasureReport,
    ErasureRequest,
    PurgeReport,
    RetentionEnforcementRead,
)
from app.privacy import service as privacy
from app.privacy.service import PrivacyError

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])

logger = logging.getLogger("vericlaim.privacy")

DATABASE_DEPENDENCY = Depends(get_db)
ORG_READ_DEPENDENCY = Depends(require_permission("organization:read"))
ORG_MANAGE_DEPENDENCY = Depends(require_permission("organization:manage", csrf_protected=True))


@router.get("/retention", response_model=RetentionEnforcementRead)
def read_retention_enforcement(
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RetentionEnforcementRead:
    """The declared windows, what is enforced, and what is not — with the reason.

    This endpoint exists because a retention policy that is displayed but not applied
    is a compliance artefact, not a control. The `not_enforced` list is the honest half.
    """
    plan = privacy.build_purge_plan(db, organization_id=principal.organization_id)
    policy = privacy.retention_policy(db, organization_id=principal.organization_id)
    holds = privacy.active_legal_holds(
        db, organization_id=principal.organization_id, now=datetime.now(timezone.utc)
    )
    return RetentionEnforcementRead(
        declared={
            "documents_retention_years": getattr(policy, "documents_retention_years", None),
            "evidence_archive_retention_years": getattr(policy, "evidence_archive_retention_years", None),
            "audit_trail_retention_years": getattr(policy, "audit_trail_retention_years", None),
            "configured_at": getattr(policy, "created_at", None),
            "status": (
                "aucune politique déclarée"
                if policy is None
                else "déclarée par l'organisation — à vérifier avec le DPO"
            ),
        },
        enforced=[
            {
                "item": "documents",
                "window_years": getattr(policy, "documents_retention_years", None),
                "applied_by": "POST /api/v1/privacy/purge (puis passage planifié)",
                "note": (
                    "Un document utilisé comme preuve suit la durée d'archivage des preuves "
                    "quand elle est la plus longue : la plus longue durée applicable gagne."
                ),
            },
            {
                "item": "document_versions / segments / artefacts",
                "window_years": getattr(policy, "documents_retention_years", None),
                "applied_by": "supprimés avec leur document, objets de stockage compris",
                "note": None,
            },
        ],
        not_enforced=plan.not_enforced,
        # C13 — le plafond de conservation ne doit pas seulement exister dans le
        # code : il est publié ici, avec la durée qu'il plafonne réellement.
        plan=plan.plan_retention,
        active_legal_holds=[
            {
                "id": str(hold.id),
                "case_reference": hold.case_reference,
                "reason": hold.reason,
                "expires_at": hold.expires_at.isoformat() if hold.expires_at else None,
            }
            for hold in holds
        ],
    )


@router.post("/purge", response_model=PurgeReport)
def apply_retention_policy(
    request: Request,
    dry_run: bool = True,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> PurgeReport:
    """Apply the declared retention policy. `dry_run` defaults to **true**.

    A destructive endpoint whose default is to destroy is an endpoint that eventually
    destroys the wrong thing during an incident.
    """
    try:
        plan = privacy.run_purge(
            db,
            storage=_storage(),
            settings=settings,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            dry_run=dry_run,
            request_id=request_id_from_request(request),
        )
    except PrivacyError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return PurgeReport(**plan.as_dict())


@router.post("/erasures", response_model=ErasureReport)
def erase_content(
    body: ErasureRequest,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ErasureReport:
    """Destroy one document and everything derived from it. Never past a legal hold."""
    if body.scope != "document":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "scope_not_implemented",
                "message": (
                    "Seul le périmètre « document » est implémenté. L'effacement d'une analyse "
                    "supposerait de décider du sort des rapports signés déjà émis — décision "
                    "juridique, pas technique."
                ),
            },
        )
    from uuid import UUID

    try:
        target = UUID(body.target_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_target", "message": "target_id doit être un identifiant."},
        )
    try:
        outcome = privacy.erase_document(
            db,
            storage=_storage(),
            settings=settings,
            organization_id=principal.organization_id,
            document_id=target,
            actor_user_id=principal.user_id,
            reason=body.reason,
            request_id=request_id_from_request(request),
        )
    except PrivacyError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    except ObjectStorageError as exc:
        # The object could not be deleted, so nothing was: the rows are intact and no
        # tombstone was written. Answering 500 is the honest outcome — a 200 would
        # claim an erasure that did not complete.
        logger.error(
            "Erasure aborted: object storage refused a deletion",
            extra={"event": "erasure_aborted", "organization_id": str(principal.organization_id)},
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "storage_unavailable",
                "message": (
                    "La suppression d'un objet de stockage a échoué : rien n'a été effacé "
                    "(lignes conservées, aucune trace d'effacement écrite). Réessayer une fois "
                    "le stockage joignable."
                ),
            },
        ) from exc
    if not outcome.erased:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "erasure_refused", "message": outcome.reason},
        )
    return ErasureReport(**outcome.as_dict())


@router.get("/export")
def export_everything(
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> dict[str, object]:
    """Complete export of what this instance holds for the organization.

    Requires `organization:manage`: the payload contains personal data (audit actors,
    requester addresses) and the whole compliance dossier.
    """
    return privacy.export_organization(db, organization_id=principal.organization_id)


def _storage() -> object:
    """The storage adapter of the running application, or a refusing one.

    Imported lazily: `app.main` imports the routers, and importing it at module load
    would close the cycle.
    """
    from app.main import app as application
    from app.documents.storage import DisabledObjectStorage

    return getattr(application.state, "document_storage", None) or DisabledObjectStorage()
