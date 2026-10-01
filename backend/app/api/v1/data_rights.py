"""C12 — recevoir et suivre les demandes d'exercice des droits.

C20 outillait l'exécution (export, effacement, rétention). Ces routes traitent la
**réception** : enregistrer une demande datée, calculer son échéance, la suivre et la
clore avec la référence de ce qui a été remis.

Trois règles de conception, appliquées ici :

* ``due_at`` n'est **jamais** fourni par le client : il est calculé à partir de
  ``received_at`` (RGPD art. 12.3). Un délai saisi serait un délai négociable ;
* clore en « accordé » sans référence est refusé (400). Un accord sans preuve est
  invérifiable, et c'est exactement ce qu'un contrôle demanderait ;
* ``GET /procedure`` publie ce que le produit sait faire, droit par droit, y compris
  les droits **non outillés** — un test vérifie que chaque route citée existe vraiment.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from fastapi import Request
from app.models.domain import (
    DataSubjectRequestOutcome,
    DataSubjectRequestStatus,
    DataSubjectRight,
)
from app.models.privacy_schemas import (
    RightsProcedureRead,
    RightsRequestClose,
    RightsRequestCreate,
    RightsRequestExtend,
    RightsRequestListRead,
    RightsRequestRead,
)
from app.privacy import rights as data_rights
from app.privacy.rights import RightsError

router = APIRouter(prefix="/api/v1/data-rights", tags=["data-rights"])

DATABASE_DEPENDENCY = Depends(get_db)
ORG_READ_DEPENDENCY = Depends(require_permission("organization:read"))
ORG_MANAGE_DEPENDENCY = Depends(
    require_permission("organization:manage", csrf_protected=True)
)


def _serialize(state: dict[str, object]) -> RightsRequestRead:
    payload = dict(state)
    payload["id"] = str(payload["id"])
    payload["handled_by_user_id"] = (
        str(payload["handled_by_user_id"]) if payload["handled_by_user_id"] else None
    )
    for field in ("received_at", "due_at", "extension_due_at", "effective_due_at", "completed_at"):
        value = payload.get(field)
        if isinstance(value, datetime):
            payload[field] = value.isoformat()
    return RightsRequestRead(**payload)


def _parse_received_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_received_at",
                "message": "La date de réception doit être au format ISO 8601.",
            },
        )


#: Route publique : la carte des droits ne contient aucune donnée personnelle, et c'est
#: elle qui alimente la page légale. La publier évite qu'une page statique raconte autre
#: chose que le produit — un décalage qui ne se voit qu'au pire moment.
public_router = APIRouter(prefix="/api/v1/public", tags=["data-rights"])


def _procedure_payload() -> RightsProcedureRead:
    contact = (getattr(settings, "data_rights_contact_email", "") or "").strip()
    return RightsProcedureRead(
        contact_email=contact or None,
        response_window_months=data_rights.RESPONSE_WINDOW_MONTHS,
        extension_months=data_rights.EXTENSION_MONTHS,
        handling=data_rights.published_handling_map(),
    )


@public_router.get("/rights-procedure", response_model=RightsProcedureRead)
def read_rights_procedure_publicly() -> RightsProcedureRead:
    """Ce que le produit sait traiter, sans compte : c'est ce qu'une page légale affiche."""

    return _procedure_payload()


@router.get("/procedure", response_model=RightsProcedureRead)
def read_procedure(principal: TenantPrincipal = ORG_READ_DEPENDENCY) -> RightsProcedureRead:
    """Ce que le produit sait réellement traiter, droit par droit.

    Publié, et pas seulement documenté : la page légale et le client lisent la même
    source, donc une promesse ne peut pas survivre à une route qui n'existe pas.
    """

    return _procedure_payload()


@router.post(
    "",
    response_model=RightsRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def record_rights_request(
    payload: RightsRequestCreate,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RightsRequestRead:
    """Enregistre une demande reçue et calcule son échéance légale."""

    try:
        row = data_rights.record_request(
            db,
            organization_id=principal.organization_id,
            request_type=DataSubjectRight(payload.request_type),
            requester_email=payload.requester_email,
            requester_name=payload.requester_name,
            requester_is_member=payload.requester_is_member,
            details=payload.details,
            received_at=_parse_received_at(payload.received_at),
            actor_user_id=principal.user_id,
            request_id=request_id_from_request(request),
        )
    except RightsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "rights_request_rejected", "message": str(exc)},
        )
    return _serialize(data_rights.request_state(row))


@router.get("", response_model=RightsRequestListRead)
def list_rights_requests(
    request_status: str | None = Query(default=None, alias="status"),
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RightsRequestListRead:
    """Liste les demandes et publie les retards, sans qu'il faille les chercher."""

    parsed: DataSubjectRequestStatus | None = None
    if request_status:
        try:
            parsed = DataSubjectRequestStatus(request_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "unknown_status",
                    "message": f"Statut inconnu : {request_status!r}.",
                },
            )
    listing = data_rights.list_requests(
        db, organization_id=principal.organization_id, status=parsed
    )
    return RightsRequestListRead(
        requests=[_serialize(state) for state in listing["requests"]],
        total=int(listing["total"]),
        open=int(listing["open"]),
        overdue=int(listing["overdue"]),
        initial_deadline_missed=int(listing["initial_deadline_missed"]),
        response_window_days=int(listing["response_window_days"]),
        window_months=int(listing["window_months"]),
        extension_months=int(listing["extension_months"]),
    )


@router.get("/{request_id}", response_model=RightsRequestRead)
def read_rights_request(
    request_id: str,
    principal: TenantPrincipal = ORG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RightsRequestRead:
    row = _load(db, principal=principal, request_id=request_id)
    return _serialize(data_rights.request_state(row))


@router.post("/{request_id}/extend", response_model=RightsRequestRead)
def extend_rights_request(
    request_id: str,
    payload: RightsRequestExtend,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RightsRequestRead:
    """Prolonge de deux mois, avec un motif écrit — une seule fois par demande."""

    row = _load(db, principal=principal, request_id=request_id)
    try:
        data_rights.extend_request(
            db,
            organization_id=principal.organization_id,
            request_id=row.id,
            reason=payload.reason,
            actor_user_id=principal.user_id,
            request_id_header=request_id_from_request(request),
        )
    except RightsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "rights_extension_refused", "message": str(exc)},
        )
    return _serialize(data_rights.request_state(row))


@router.post("/{request_id}/close", response_model=RightsRequestRead)
def close_rights_request(
    request_id: str,
    payload: RightsRequestClose,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> RightsRequestRead:
    """Clôt la demande avec la preuve de ce qui a été remis, ou le motif du refus."""

    row = _load(db, principal=principal, request_id=request_id)
    try:
        data_rights.close_request(
            db,
            organization_id=principal.organization_id,
            request_id=row.id,
            outcome=DataSubjectRequestOutcome(payload.outcome),
            outcome_reference=payload.outcome_reference,
            outcome_detail=payload.outcome_detail,
            refusal_reason=payload.refusal_reason,
            actor_user_id=principal.user_id,
            request_id_header=request_id_from_request(request),
        )
    except RightsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "rights_close_refused", "message": str(exc)},
        )
    return _serialize(data_rights.request_state(row))


def _load(db: Session, *, principal: TenantPrincipal, request_id: str):
    from uuid import UUID

    try:
        parsed = UUID(request_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "rights_request_not_found", "message": "Demande introuvable."},
        )
    try:
        return data_rights.get_request(
            db, organization_id=principal.organization_id, request_id=parsed
        )
    except RightsError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "rights_request_not_found", "message": "Demande introuvable."},
        )
