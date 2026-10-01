"""C12 — réception et suivi des demandes d'exercice des droits.

C20 outillait l'**exécution** : `GET /api/v1/privacy/export` produit un export complet,
`POST /api/v1/privacy/erasures` efface un contenu sous conditions, `POST
/api/v1/privacy/purge` applique la politique de rétention. Il manquait la **réception**
et le suivi : personne ne pouvait dire qu'une demande avait été reçue telle date, ni
qu'elle devait être traitée avant telle autre.

Trois décisions, et pourquoi :

1. **Le délai est calculé, pas déclaré.** ``due_at`` vaut ``received_at`` + un mois
   (art. 12.3). Le calcul gère les fins de mois (31 janvier + un mois = 28/29 février) :
   une date saisie à la main finirait par être fausse, et une échéance fausse est pire
   que pas d'échéance.
2. **Un accord sans preuve est refusé.** Clôturer en « accordé » exige une
   ``outcome_reference`` : ce qui a été effectivement remis (empreinte de l'export,
   référence du manifeste d'effacement). La contrainte est aussi en base.
3. **Ce que le produit ne sait pas faire est publié.** ``RIGHT_HANDLING`` dit, droit par
   droit, si le traitement est servi par une route existante ou s'il est manuel — et
   pourquoi. Une page qui promet un bouton inexistant est un mensonge ; cette table est
   exposée par l'API pour être vérifiable, et un test vérifie que chaque route citée
   existe réellement.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.identity.service import append_audit_event
from app.models.domain import (
    DataSubjectRequest,
    DataSubjectRequestOutcome,
    DataSubjectRequestStatus,
    DataSubjectRight,
)

#: Délai de réponse : un mois (RGPD art. 12.3), prolongeable de deux mois motivés.
RESPONSE_WINDOW_MONTHS = 1
EXTENSION_MONTHS = 2

#: Traitement de chaque droit, tel qu'il est réellement outillé aujourd'hui.
#:
#: ``automated`` à ``False`` signifie : **le produit ne le fait pas**. La demande est
#: reçue, datée, suivie à la main — et la raison est écrite ici plutôt que découverte
#: par un client qui cherche un bouton.
RIGHT_HANDLING: dict[DataSubjectRight, dict[str, object]] = {
    DataSubjectRight.ACCESS: {
        "article": "RGPD art. 15",
        "automated": True,
        "route": "GET /api/v1/privacy/export",
        "permission": "organization:manage",
        "note": (
            "L'export complet de l'organisation (membres, documents, analyses, audit) est "
            "produit par l'application ; la référence remise est l'empreinte du manifeste."
        ),
    },
    DataSubjectRight.PORTABILITY: {
        "article": "RGPD art. 20",
        "automated": True,
        "route": "GET /api/v1/privacy/export",
        "permission": "organization:manage",
        "note": (
            "Le même export sert la portabilité : il est structuré (JSON) et contient les "
            "données fournies par la personne. Il ne contient pas de format dédié "
            "interopérable : c'est une limite connue, publiée ici."
        ),
    },
    DataSubjectRight.ERASURE: {
        "article": "RGPD art. 17",
        "automated": True,
        "route": "POST /api/v1/privacy/erasures",
        "permission": "organization:manage",
        "note": (
            "L'effacement est refusé (409) lorsqu'un gel légal actif le protège : le refus "
            "cite le dossier de gel, il n'est jamais silencieux."
        ),
    },
    DataSubjectRight.RECTIFICATION: {
        "article": "RGPD art. 16",
        "automated": False,
        "route": None,
        "permission": "organization:manage",
        "note": (
            "Non outillé comme un « droit » : les données de l'organisation sont des "
            "documents et des verdicts, corrigés par la revue humaine (une nouvelle "
            "version d'analyse remplace la précédente sans l'écraser) ou par le "
            "remplacement de la version de document. La demande est enregistrée et "
            "traitée à la main tant qu'un parcours dédié n'existe pas."
        ),
    },
    DataSubjectRight.RESTRICTION: {
        "article": "RGPD art. 18",
        "automated": False,
        "route": None,
        "permission": "organization:manage",
        "note": (
            "Non outillé : le produit ne sait pas marquer un contenu « conservé mais non "
            "traité ». Le gel légal (C20) empêche la suppression, il ne limite pas le "
            "traitement. À construire si un client le demande."
        ),
    },
    DataSubjectRight.OBJECTION: {
        "article": "RGPD art. 21",
        "automated": False,
        "route": None,
        "permission": "organization:manage",
        "note": (
            "Non outillé : l'opposition se traite contractuellement (fin de la base "
            "légale) et conduit en pratique à un effacement, qui lui est outillé."
        ),
    },
}

TERMINAL_STATUSES = (
    DataSubjectRequestStatus.COMPLETED,
    DataSubjectRequestStatus.REFUSED,
    DataSubjectRequestStatus.WITHDRAWN,
)


class RightsError(RuntimeError):
    """Une demande ne peut pas être enregistrée ou clôturée telle qu'elle est demandée."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_months(moment: datetime, months: int) -> datetime:
    """Ajoute des mois calendaires, en ramenant au dernier jour du mois si besoin.

    ``31 janvier + 1 mois`` n'existe pas : le délai RGPD d'un mois court jusqu'au
    dernier jour de février. Une addition de « 30 jours » donnerait une échéance
    fausse en mars et au-delà — et c'est cette date qui décide si un retard existe.
    """

    year = moment.year + (moment.month - 1 + months) // 12
    month = (moment.month - 1 + months) % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def effective_due_at(request: DataSubjectRequest) -> datetime:
    """L'échéance applicable : la prolongation si elle existe, sinon le délai initial."""

    return _as_utc(request.extension_due_at or request.due_at)


def request_state(request: DataSubjectRequest, *, now: datetime | None = None) -> dict[str, object]:
    """État publiable d'une demande : où elle en est, et si le délai est tenu."""

    moment = now or utcnow()
    due = effective_due_at(request)
    terminal = request.status in TERMINAL_STATUSES
    days_remaining = (due - moment).days
    return {
        "id": request.id,
        "request_type": request.request_type.value,
        "article": RIGHT_HANDLING[request.request_type]["article"],
        "requester_email": request.requester_email,
        "requester_name": request.requester_name,
        "requester_is_member": request.requester_is_member,
        "status": request.status.value,
        "received_at": _as_utc(request.received_at),
        "due_at": _as_utc(request.due_at),
        "extension_due_at": _as_utc(request.extension_due_at) if request.extension_due_at else None,
        "extension_reason": request.extension_reason,
        "effective_due_at": due,
        "days_remaining": days_remaining,
        "overdue": (not terminal) and due < moment,
        #: L'échéance **d'origine** a-t-elle été dépassée ? Distinct de « overdue » : une
        #: prolongation régulière rend la demande dans les délais, elle n'efface pas le
        #: fait que le premier mois a été dépassé. Sans cet indicateur, une prolongation
        #: serait un moyen silencieux de transformer un retard en respect du délai —
        #: exactement ce que la prolongation ne doit pas permettre.
        "initial_deadline_passed": (not terminal) and _as_utc(request.due_at) < moment,
        "outcome": request.outcome.value if request.outcome else None,
        "outcome_reference": request.outcome_reference,
        "outcome_detail": request.outcome_detail,
        "refusal_reason": request.refusal_reason,
        "completed_at": _as_utc(request.completed_at) if request.completed_at else None,
        "handled_by_user_id": request.handled_by_user_id,
        "handling": dict(RIGHT_HANDLING[request.request_type]),
    }


def record_request(
    db: Session,
    *,
    organization_id: UUID,
    request_type: DataSubjectRight,
    requester_email: str,
    details: str,
    requester_name: str | None = None,
    requester_is_member: bool = False,
    received_at: datetime | None = None,
    actor_user_id: UUID | None = None,
    request_id: str | None = None,
) -> DataSubjectRequest:
    """Enregistre une demande reçue et calcule son échéance."""

    email = requester_email.strip()
    if "@" not in email or len(email) < 5:
        raise RightsError("L'adresse du demandeur est invalide.")
    if len(details.strip()) < 10:
        # Sans le texte de la demande, aucune réponse ne peut être justifiée plus tard.
        raise RightsError(
            "La demande doit être enregistrée avec son texte (au moins 10 caractères)."
        )
    received = _as_utc(received_at) if received_at else utcnow()
    due = add_months(received, RESPONSE_WINDOW_MONTHS)
    row = DataSubjectRequest(
        organization_id=organization_id,
        request_type=request_type,
        requester_email=email,
        requester_name=(requester_name or "").strip() or None,
        requester_is_member=requester_is_member,
        details=details.strip(),
        received_at=received,
        due_at=due,
        status=DataSubjectRequestStatus.RECEIVED,
    )
    db.add(row)
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="data_subject_request",
        entity_id=row.id,
        action="privacy.rights_request_received",
        payload={
            "request_type": request_type.value,
            "requester_email": email,
            "requester_is_member": requester_is_member,
            "received_at": received.isoformat(),
            "due_at": due.isoformat(),
            "handling_automated": bool(RIGHT_HANDLING[request_type]["automated"]),
        },
        request_id=request_id,
    )
    db.flush()
    return row


def extend_request(
    db: Session,
    *,
    organization_id: UUID,
    request_id: UUID,
    reason: str,
    actor_user_id: UUID | None,
    request_id_header: str | None = None,
) -> DataSubjectRequest:
    """Prolonge le délai de deux mois, avec un motif (art. 12.3, seconde phrase)."""

    request = get_request(db, organization_id=organization_id, request_id=request_id)
    if request.status in TERMINAL_STATUSES:
        raise RightsError("Une demande close ne peut plus être prolongée.")
    if request.extension_due_at is not None:
        raise RightsError("Ce délai a déjà été prolongé une fois.")
    if len(reason.strip()) < 10:
        raise RightsError("Une prolongation exige un motif écrit (au moins 10 caractères).")
    request.extension_due_at = add_months(_as_utc(request.due_at), EXTENSION_MONTHS)
    request.extension_reason = reason.strip()
    if request.status == DataSubjectRequestStatus.RECEIVED:
        request.status = DataSubjectRequestStatus.IN_PROGRESS
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="data_subject_request",
        entity_id=request.id,
        action="privacy.rights_request_extended",
        payload={
            "extension_due_at": _as_utc(request.extension_due_at).isoformat(),
            "reason": request.extension_reason[:500],
        },
        request_id=request_id_header,
    )
    db.flush()
    return request


def close_request(
    db: Session,
    *,
    organization_id: UUID,
    request_id: UUID,
    outcome: DataSubjectRequestOutcome,
    outcome_reference: str | None = None,
    outcome_detail: str | None = None,
    refusal_reason: str | None = None,
    actor_user_id: UUID | None,
    request_id_header: str | None = None,
) -> DataSubjectRequest:
    """Clôt une demande : accordée (avec ce qui a été remis), ou refusée (avec le motif)."""

    request = get_request(db, organization_id=organization_id, request_id=request_id)
    if request.status in TERMINAL_STATUSES:
        raise RightsError("Cette demande est déjà close.")
    reference = (outcome_reference or "").strip()
    if outcome is DataSubjectRequestOutcome.REFUSED:
        if len((refusal_reason or "").strip()) < 10:
            raise RightsError("Un refus exige un motif écrit (au moins 10 caractères).")
        request.status = DataSubjectRequestStatus.REFUSED
        request.refusal_reason = (refusal_reason or "").strip()
        request.outcome_reference = None
    else:
        if len(reference) < 6:
            raise RightsError(
                "Un accord exige la référence de ce qui a été remis (empreinte d'export, "
                "référence de manifeste d'effacement)."
            )
        request.status = DataSubjectRequestStatus.COMPLETED
        request.outcome_reference = reference
        request.refusal_reason = None
    request.outcome = outcome
    request.outcome_detail = (outcome_detail or "").strip() or None
    request.completed_at = utcnow()
    request.handled_by_user_id = actor_user_id
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="data_subject_request",
        entity_id=request.id,
        action="privacy.rights_request_closed",
        payload={
            "request_type": request.request_type.value,
            "outcome": outcome.value,
            "outcome_reference": request.outcome_reference,
            "refusal_reason": request.refusal_reason,
            "overdue_at_close": effective_due_at(request) < utcnow(),
            "days_since_receipt": (utcnow() - _as_utc(request.received_at)).days,
        },
        request_id=request_id_header,
    )
    db.flush()
    return request


def get_request(
    db: Session, *, organization_id: UUID, request_id: UUID
) -> DataSubjectRequest:
    request = db.scalar(
        select(DataSubjectRequest).where(
            DataSubjectRequest.organization_id == organization_id,
            DataSubjectRequest.id == request_id,
        )
    )
    if request is None:
        raise RightsError("Demande introuvable pour cette organisation.")
    return request


def list_requests(
    db: Session,
    *,
    organization_id: UUID,
    status: DataSubjectRequestStatus | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Liste les demandes et publie l'état du délai, y compris les retards."""

    statement = select(DataSubjectRequest).where(
        DataSubjectRequest.organization_id == organization_id
    )
    if status is not None:
        statement = statement.where(DataSubjectRequest.status == status)
    rows = list(db.scalars(statement.order_by(DataSubjectRequest.received_at.desc())).all())
    moment = now or utcnow()
    states = [request_state(row, now=moment) for row in rows]
    open_states = [state for state in states if state["status"] not in {
        DataSubjectRequestStatus.COMPLETED.value,
        DataSubjectRequestStatus.REFUSED.value,
        DataSubjectRequestStatus.WITHDRAWN.value,
    }]
    return {
        "requests": states,
        "total": len(states),
        "open": len(open_states),
        "overdue": len([state for state in open_states if state["overdue"]]),
        "initial_deadline_missed": len(
            [state for state in open_states if state["initial_deadline_passed"]]
        ),
        "response_window_days": 30,
        "window_months": RESPONSE_WINDOW_MONTHS,
        "extension_months": EXTENSION_MONTHS,
    }


def open_request_count(db: Session, *, organization_id: UUID) -> int:
    """Nombre de demandes non closes, pour les alertes et le tableau de bord."""

    return int(
        db.scalar(
            select(func.count())
            .select_from(DataSubjectRequest)
            .where(
                DataSubjectRequest.organization_id == organization_id,
                DataSubjectRequest.status.not_in(
                    (
                        DataSubjectRequestStatus.COMPLETED,
                        DataSubjectRequestStatus.REFUSED,
                        DataSubjectRequestStatus.WITHDRAWN,
                    )
                ),
            )
        )
        or 0
    )


def overdue_requests(
    db: Session, *, organization_id: UUID, now: datetime | None = None
) -> list[DataSubjectRequest]:
    """Demandes ouvertes dont l'échéance est dépassée.

    La comparaison se fait en Python sur les seules demandes ouvertes : l'échéance
    applicable dépend d'une prolongation éventuelle, et l'exprimer en SQL demanderait
    un ``COALESCE`` que les deux moteurs n'écrivent pas de la même façon.
    """

    moment = now or utcnow()
    rows = db.scalars(
        select(DataSubjectRequest).where(
            DataSubjectRequest.organization_id == organization_id,
            DataSubjectRequest.status.in_(
                (DataSubjectRequestStatus.RECEIVED, DataSubjectRequestStatus.IN_PROGRESS)
            ),
        )
    ).all()
    return [row for row in rows if effective_due_at(row) < moment]


def published_handling_map() -> list[dict[str, object]]:
    """Ce que le produit sait réellement traiter, droit par droit."""

    return [
        {
            "request_type": right.value,
            "article": RIGHT_HANDLING[right]["article"],
            "automated": RIGHT_HANDLING[right]["automated"],
            "route": RIGHT_HANDLING[right]["route"],
            "requires_permission": RIGHT_HANDLING[right]["permission"],
            "note": RIGHT_HANDLING[right]["note"],
        }
        for right in DataSubjectRight
    ]


def deadline_hint(now: datetime | None = None) -> datetime:
    """Échéance d'une demande reçue maintenant — sert à afficher le délai, pas à le saisir."""

    return add_months(now or utcnow(), RESPONSE_WINDOW_MONTHS)


__all__ = [
    "EXTENSION_MONTHS",
    "RESPONSE_WINDOW_MONTHS",
    "RIGHT_HANDLING",
    "RightsError",
    "add_months",
    "close_request",
    "deadline_hint",
    "effective_due_at",
    "extend_request",
    "get_request",
    "list_requests",
    "open_request_count",
    "overdue_requests",
    "published_handling_map",
    "record_request",
    "request_state",
]
