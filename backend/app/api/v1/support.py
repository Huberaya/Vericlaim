"""C15 — centre d'aide, objectifs de réponse publiés, demandes de support suivies.

Trois routes publiques (aucune session, aucune donnée personnelle) et trois routes
d'organisation :

* ``GET /api/v1/public/support`` : le centre d'aide — parcours de démarrage, erreurs
  fréquentes **vérifiées contre le code**, objectifs de réponse par plan, et ce que le
  support ne fait pas. C'est ce que la page ``/aide`` affiche ;
* ``GET /api/v1/public/support/contact`` : l'adresse de support, ou ``null`` ;
* ``POST /api/v1/support/requests`` : déposer une demande depuis l'application, avec
  l'écran courant et le dernier code d'erreur affiché — le contexte réel, pas une
  reconstitution ;
* ``GET /api/v1/support/requests`` : suivre les demandes et leur échéance ;
* ``POST /api/v1/support/requests/{id}/answer`` : répondre. La réponse écrite est
  obligatoire, comme la référence de remise l'est pour clore une demande de droits.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.billing.subscriptions import resolve_entitlement
from app.models.domain import Organization
from app.models.domain import SupportRequestStatus
from app.models.support_schemas import (
    SupportAnswerRequest,
    SupportCentreRead,
    SupportContactRead,
    SupportRequestCreate,
    SupportRequestListRead,
    SupportRequestRead,
)
from app.support import help as support_help
from app.support import service as support
from app.support.service import SupportError

router = APIRouter(prefix="/api/v1/support", tags=["support"])
public_router = APIRouter(prefix="/api/v1/public/support", tags=["support"])

DATABASE_DEPENDENCY = Depends(get_db)
ORG_READ_DEPENDENCY = Depends(require_permission("organization:read"))
#: Déposer une demande écrit dans la base de l'organisation : même exigence que toute
#: écriture (permission de gestion + jeton CSRF).
ORG_ACT_DEPENDENCY = Depends(require_permission("organization:manage", csrf_protected=True))


def _serialize(state: dict[str, object]) -> SupportRequestRead:
    payload = dict(state)
    payload["id"] = str(payload["id"])
    for key in ("first_response_due_at", "answered_at", "closed_at", "created_at"):
        value = payload.get(key)
        payload[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return SupportRequestRead(**payload)  # type: ignore[arg-type]


def _contact_payload() -> SupportContactRead:
    return SupportContactRead(**support.published_contact(settings.support_contact_email))


@public_router.get("/contact", response_model=SupportContactRead)
def read_contact() -> SupportContactRead:
    """Le contact publié. ``null`` quand il n'est pas configuré, jamais une adresse plausible."""

    return _contact_payload()


@public_router.get("", response_model=SupportCentreRead)
def read_centre() -> SupportCentreRead:
    """Le centre d'aide, sans session : c'est ce qu'une page publique peut afficher."""

    centre = support_help.help_centre()
    contact = _contact_payload()
    return SupportCentreRead(
        contact=contact,
        start_here=centre["start_here"],  # type: ignore[arg-type]
        frequent_errors=centre["frequent_errors"],  # type: ignore[arg-type]
        sla=centre["sla"],  # type: ignore[arg-type]
        honesty=str(centre["honesty"]),
        help_url="/aide",
    )


@router.post("/requests", response_model=SupportRequestRead, status_code=status.HTTP_201_CREATED)
def create_request(
    body: SupportRequestCreate,
    request: Request,
    principal: TenantPrincipal = ORG_ACT_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupportRequestRead:
    """Dépose une demande. Le délai de réponse vient du plan réel, il n'est pas saisi."""

    organization = db.get(Organization, principal.organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail={"code": "organization_not_found"})
    # Le plan vient de l'état d'accès réel (abonnement ou essai), pas du corps de la
    # requête : un client ne choisit pas le délai auquel il a droit.
    entitlement = resolve_entitlement(db, organization=organization)
    plan_code = entitlement.plan_code or ""
    if not plan_code:
        # Sans plan connu, aucun délai publié ne s'applique. On refuse plutôt que
        # d'enregistrer la demande avec un engagement inventé.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "support_plan_unknown",
                "message": (
                    "Aucune offre n'est active sur cette organisation : aucun objectif de "
                    "réponse ne s'applique, et la demande ne peut pas être enregistrée avec "
                    "un délai inventé."
                ),
            },
        )
    try:
        row = support.create_request(
            db,
            organization_id=principal.organization_id,
            category=body.category,
            subject=body.subject,
            message=body.message,
            requester_email=principal.email,
            plan_code=plan_code,
            screen=body.screen,
            last_error_code=body.last_error_code,
            requester_user_id=principal.user_id,
            request_id=request_id_from_request(request),
        )
    except SupportError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    db.commit()
    return _serialize(support.request_state(row))


@router.get("/requests", response_model=SupportRequestListRead)
def list_requests(
    request_status: SupportRequestStatus | None = Query(default=None, alias="status"),
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupportRequestListRead:
    listing = support.list_requests(
        db, organization_id=principal.organization_id, status=request_status
    )
    return SupportRequestListRead(
        requests=[_serialize(state) for state in listing["requests"]],
        total=int(listing["total"]),
        open=int(listing["open"]),
        overdue=int(listing["overdue"]),
    )


@router.get("/requests/{request_id}", response_model=SupportRequestRead)
def read_request(
    request_id: str,
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupportRequestRead:
    from uuid import UUID

    try:
        identifier = UUID(request_id)
    except ValueError:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_request_id", "message": "Identifiant invalide."}
        )
    try:
        row = support.get_request(db, organization_id=principal.organization_id, request_id=identifier)
    except SupportError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return _serialize(support.request_state(row))


@router.post("/requests/{request_id}/answer", response_model=SupportRequestRead)
def answer_request(
    request_id: str,
    body: SupportAnswerRequest,
    request: Request,
    principal: TenantPrincipal = ORG_ACT_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupportRequestRead:
    """Répond à une demande. Une réponse sans texte écrit est refusée."""

    from uuid import UUID

    try:
        identifier = UUID(request_id)
    except ValueError:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_request_id", "message": "Identifiant invalide."}
        )
    try:
        row = support.answer_request(
            db,
            organization_id=principal.organization_id,
            request_id=identifier,
            resolution=body.resolution,
            actor_user_id=principal.user_id,
            close=body.close,
            request_id_header=request_id_from_request(request),
        )
    except SupportError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    db.commit()
    return _serialize(support.request_state(row))
