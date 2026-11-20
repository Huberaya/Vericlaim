"""Demandes de support : réception datée, délai calculé, clôture motivée.

Même logique que la procédure de droits (C12), appliquée au support : une demande dont
on ne peut pas dire quand elle a été reçue, dans quel délai elle devait être traitée et
ce qui a été répondu n'est pas un support, c'est une boîte aux lettres.

Ce que ce module garantit, et qui est mesuré :

* ``first_response_due_at`` est **calculé** depuis le plan réel de l'organisation et le
  délai publié pour la catégorie — jamais saisi ;
* le plan et le délai sont **figés sur la ligne** : changer d'offre ne réécrit pas
  l'engagement pris le jour du dépôt ;
* une clôture **sans réponse écrite** est refusée, comme un accord de droits sans
  référence de ce qui a été remis ;
* chaque transition écrit un événement dans la chaîne d'audit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.identity.service import append_audit_event
from app.models.domain import (
    SupportRequest,
    SupportRequestCategory,
    SupportRequestStatus,
)
from app.support import help as help_module

#: Longueur minimale du message : une demande en trois mots ne peut pas être traitée.
MINIMUM_MESSAGE_CHARS = 20


class SupportError(Exception):
    """Refus métier, présenté tel quel à l'appelant (400/404 selon le code)."""

    def __init__(self, message: str, *, status_code: int = 400, code: str = "support_request_rejected"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def create_request(
    db: Session,
    *,
    organization_id: UUID,
    category: SupportRequestCategory,
    subject: str,
    message: str,
    requester_email: str,
    plan_code: str,
    screen: str | None = None,
    last_error_code: str | None = None,
    requester_user_id: UUID | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> SupportRequest:
    """Enregistre une demande et calcule le délai de première réponse applicable."""

    clean_subject = subject.strip()
    clean_message = message.strip()
    if len(clean_subject) < 3:
        raise SupportError("L'objet de la demande est trop court.")
    if len(clean_message) < MINIMUM_MESSAGE_CHARS:
        raise SupportError(
            f"Le message est trop court pour être traité (au moins {MINIMUM_MESSAGE_CHARS} caractères)."
        )
    email = requester_email.strip()
    if "@" not in email:
        raise SupportError("L'adresse de réponse est invalide.")
    try:
        hours = help_module.first_response_hours(plan_code, category.value)
    except KeyError as exc:
        # Aucun délai n'est inventé pour un plan inconnu : on refuse la demande plutôt
        # que de laisser croire qu'un engagement a été pris.
        raise SupportError(
            f"Aucun objectif de première réponse n'est publié pour le plan {plan_code!r} : "
            "la demande ne peut pas être enregistrée avec un délai inventé.",
            code="support_plan_unknown",
        ) from exc

    moment = _as_utc(now) if now else datetime.now(timezone.utc)
    row = SupportRequest(
        organization_id=organization_id,
        category=category,
        subject=clean_subject,
        message=clean_message,
        screen=(screen or "").strip()[:64] or None,
        last_error_code=(last_error_code or "").strip()[:128] or None,
        requester_user_id=requester_user_id,
        requester_email=email,
        plan_code=plan_code,
        first_response_hours=hours,
        first_response_due_at=moment + timedelta(hours=hours),
        status=SupportRequestStatus.OPEN,
    )
    db.add(row)
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=requester_user_id,
        entity_type="support_request",
        entity_id=row.id,
        action="support.request_received",
        payload={
            "category": category.value,
            "plan_code": plan_code,
            "first_response_hours": hours,
            "screen": row.screen,
            "last_error_code": row.last_error_code,
        },
        request_id=request_id,
    )
    db.flush()
    return row


def get_request(db: Session, *, organization_id: UUID, request_id: UUID) -> SupportRequest:
    row = db.scalar(
        select(SupportRequest).where(
            SupportRequest.organization_id == organization_id,
            SupportRequest.id == request_id,
        )
    )
    if row is None:
        raise SupportError("Demande de support introuvable pour cette organisation.", status_code=404,
                           code="support_request_not_found")
    return row


def answer_request(
    db: Session,
    *,
    organization_id: UUID,
    request_id: UUID,
    resolution: str,
    actor_user_id: UUID | None,
    close: bool = True,
    request_id_header: str | None = None,
) -> SupportRequest:
    """Répond à une demande. La réponse écrite est obligatoire, y compris pour clore."""

    request = get_request(db, organization_id=organization_id, request_id=request_id)
    if request.status is SupportRequestStatus.CLOSED:
        raise SupportError("Cette demande de support est déjà close.")
    text = resolution.strip()
    if len(text) < 10:
        raise SupportError(
            "Une réponse de support exige un texte écrit (au moins 10 caractères)."
        )
    moment = datetime.now(timezone.utc)
    request.resolution = text
    request.answered_at = request.answered_at or moment
    request.status = SupportRequestStatus.CLOSED if close else SupportRequestStatus.ANSWERED
    if close:
        request.closed_at = moment
        request.closed_by_user_id = actor_user_id
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="support_request",
        entity_id=request.id,
        action="support.request_closed" if close else "support.request_answered",
        payload={
            "response_hours": round(
                (moment - _as_utc(request.created_at)).total_seconds() / 3600, 2
            ),
            "within_target": moment <= _as_utc(request.first_response_due_at),
        },
        request_id=request_id_header,
    )
    db.flush()
    return request


def request_state(request: SupportRequest, *, now: datetime | None = None) -> dict[str, object]:
    """État publiable : où en est la demande, et si l'objectif de réponse est tenu."""

    moment = _as_utc(now) if now else datetime.now(timezone.utc)
    due = _as_utc(request.first_response_due_at)
    open_now = request.status is SupportRequestStatus.OPEN
    return {
        "id": request.id,
        "category": request.category.value,
        "subject": request.subject,
        "screen": request.screen,
        "last_error_code": request.last_error_code,
        "requester_email": request.requester_email,
        "plan_code": request.plan_code,
        "first_response_hours": request.first_response_hours,
        "first_response_due_at": due,
        "status": request.status.value,
        "resolution": request.resolution,
        "answered_at": _as_utc(request.answered_at) if request.answered_at else None,
        "closed_at": _as_utc(request.closed_at) if request.closed_at else None,
        "created_at": _as_utc(request.created_at),
        "overdue": open_now and due < moment,
        "hours_remaining": round((due - moment).total_seconds() / 3600, 2),
    }


def list_requests(
    db: Session, *, organization_id: UUID, status: SupportRequestStatus | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    statement = select(SupportRequest).where(SupportRequest.organization_id == organization_id)
    if status is not None:
        statement = statement.where(SupportRequest.status == status)
    rows = list(db.scalars(statement.order_by(SupportRequest.created_at.desc())).all())
    moment = _as_utc(now) if now else datetime.now(timezone.utc)
    states = [request_state(row, now=moment) for row in rows]
    return {
        "requests": states,
        "total": len(states),
        "open": len([state for state in states if state["status"] == SupportRequestStatus.OPEN.value]),
        "overdue": len([state for state in states if state["overdue"]]),
    }


def open_request_count(db: Session, *, organization_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(SupportRequest)
            .where(
                SupportRequest.organization_id == organization_id,
                SupportRequest.status == SupportRequestStatus.OPEN,
            )
        )
        or 0
    )


def published_contact(contact_email: str | None) -> dict[str, object]:
    """Contact publié. Une adresse vide est publiée ``null``, jamais remplacée par une
    adresse plausible : envoyer une demande à une boîte inexistante est pire que de ne
    pas en proposer.
    """

    address = (contact_email or "").strip()
    return {
        "email": address or None,
        "note": (
            "L'adresse de support est publiée par l'exploitant. Tant qu'elle est vide, "
            "le formulaire de l'application reste le seul canal : les demandes y sont "
            "enregistrées et datées, mais aucune adresse n'est inventée pour autant."
        )
        if not address
        else "Adresse publiée par l'exploitant du service.",
        "form_available": True,
    }
