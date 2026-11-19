"""C13 — état d'abonnement, transitions et application des événements prestataire.

Trois principes structurent ce module :

1. **L'état vient du prestataire.** Le portail ne décide pas qu'un client est
   passé sur un plan supérieur : il demande le changement au prestataire, et
   seule l'application de l'événement (signé, journalisé, idempotent) modifie
   l'état local. Un abonnement payé mais dont l'événement n'est jamais arrivé est
   un abonnement non accordé — c'est la panne qui coûte moins cher.
2. **L'essai n'est pas une ligne en base.** Il se dérive de
   ``organizations.created_at`` : un essai ne peut donc pas être remis à zéro par
   un appel d'API, seulement par la création d'une organisation.
3. **Un abonnement expiré n'est pas un abonnement actif.** Aucun délai de grâce
   implicite : si la période payée est terminée et qu'aucun renouvellement n'a
   été confirmé, les actions payantes sont refusées en le disant. Le produit ne
   fait pas crédit à l'insu du client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing.plans import (
    DEFAULT_TRIAL_PLAN_CODE,
    Metric,
    Plan,
    TRIAL_DAYS,
    is_upgrade,
    plan_or_none,
)
from app.billing.usage import add_months, utcnow
from app.identity.service import set_db_request_context
from app.models.domain import (
    BillingCheckoutSession,
    BillingInvoice,
    BillingProviderEvent,
    BillingSubscription,
    BillingSubscriptionStatus,
    Membership,
    MembershipStatus,
    Organization,
)


class BillingStateError(RuntimeError):
    """Une transition refusée, avec une raison lisible par un client."""


@dataclass(frozen=True)
class Entitlement:
    """Ce à quoi une organisation a droit, maintenant, et pourquoi."""

    organization_id: UUID
    plan: Plan | None
    status: str
    granted: bool
    reason: str
    subscription: BillingSubscription | None
    provider: str | None
    trial_ends_at: datetime | None
    trial_derived: bool
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    pending_plan_code: str | None
    pending_plan_effective_at: datetime | None

    @property
    def plan_code(self) -> str | None:
        return self.plan.code if self.plan else None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def load_subscription(db: Session, *, organization_id: UUID) -> BillingSubscription | None:
    return db.scalar(
        select(BillingSubscription).where(BillingSubscription.organization_id == organization_id)
    )


def load_subscription_by_provider_id(
    db: Session, *, provider: str, provider_subscription_id: str
) -> BillingSubscription | None:
    return db.scalar(
        select(BillingSubscription).where(
            BillingSubscription.provider == provider,
            BillingSubscription.provider_subscription_id == provider_subscription_id,
        )
    )


def seats_in_use(db: Session, *, organization_id: UUID) -> int:
    """Sièges occupés : membres actifs **et** invitations en attente.

    Compter les invitations en attente est une décision assumée : sans cela, une
    organisation pourrait inviter dix personnes et ne consommer que le siège du
    premier inscrit. Une invitation en attente consomme un siège jusqu'à son
    expiration ou son retrait, et c'est écrit dans la réponse de l'API.
    """

    total = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.organization_id == organization_id,
            Membership.status.in_((MembershipStatus.ACTIVE, MembershipStatus.INVITED)),
        )
    )
    return int(total or 0)


def trial_window(organization: Organization) -> tuple[datetime, datetime]:
    start = _as_utc(organization.created_at)
    return start, start + timedelta(days=TRIAL_DAYS)


def resolve_entitlement(
    db: Session,
    *,
    organization: Organization,
    now: datetime | None = None,
) -> Entitlement:
    """L'état d'accès d'une organisation, dérivé — jamais deviné."""

    moment = _as_utc(now) if now else utcnow()
    subscription = load_subscription(db, organization_id=organization.id)

    if subscription is None:
        trial_start, trial_end = trial_window(organization)
        plan = plan_or_none(DEFAULT_TRIAL_PLAN_CODE)
        granted = moment < trial_end
        reason = (
            f"Essai {plan.name} en cours jusqu'au {trial_end:%d/%m/%Y}."
            if granted
            else (
                f"Essai {plan.name} terminé le {trial_end:%d/%m/%Y} : "
                "souscrivez une offre pour reprendre les imports et les analyses."
            )
        )
        return Entitlement(
            organization_id=organization.id,
            plan=plan,
            status=BillingSubscriptionStatus.TRIALING.value if granted else "trial_expired",
            granted=granted,
            reason=reason,
            subscription=None,
            provider=None,
            trial_ends_at=trial_end,
            trial_derived=True,
            current_period_start=trial_start,
            current_period_end=trial_end,
            cancel_at_period_end=False,
            pending_plan_code=None,
            pending_plan_effective_at=None,
        )

    plan = plan_or_none(subscription.plan_code)
    status = (
        subscription.status.value
        if isinstance(subscription.status, BillingSubscriptionStatus)
        else str(subscription.status)
    )
    period_end = _as_utc(subscription.current_period_end)
    period_start = _as_utc(subscription.current_period_start)
    granted = False
    reason = ""
    if status == BillingSubscriptionStatus.TRIALING.value:
        granted = moment < period_end
        reason = (
            f"Essai {plan.name if plan else subscription.plan_code} en cours jusqu'au {period_end:%d/%m/%Y}."
            if granted
            else f"Essai terminé le {period_end:%d/%m/%Y}."
        )
    elif status == BillingSubscriptionStatus.ACTIVE.value:
        granted = moment < period_end
        if granted and subscription.cancel_at_period_end:
            # Un abonnement actif mais résilié ne doit pas se lire « à jour » : le
            # client a besoin de la date exacte a laquelle l'acces s'arretera.
            active_reason = (
                "Abonnement résilié : l'accès reste ouvert jusqu'au dernier jour payé, "
                f"le {period_end:%d/%m/%Y}. Aucun nouveau prélèvement ne sera fait."
            )
        else:
            active_reason = (
                f"Abonnement {plan.name if plan else subscription.plan_code} à jour jusqu'au "
                f"{period_end:%d/%m/%Y}."
            )
        reason = (
            active_reason
            if granted
            else (
                # Une résiliation demandée par le client n'est pas un « renouvellement
                # non confirmé » : annoncer cela à quelqu'un qui a résilié lui-même
                # ferait croire à un incident de paiement.
                f"Abonnement résilié : l'accès a pris fin le {period_end:%d/%m/%Y}, "
                "comme demandé. Aucune donnée n'a été supprimée ; une nouvelle souscription "
                "rétablit l'accès aux imports et aux analyses."
                if subscription.cancel_at_period_end
                else (
                    f"Période payée terminée le {period_end:%d/%m/%Y} et renouvellement non confirmé : "
                    "l'accès aux imports, analyses et rapports est suspendu jusqu'au paiement."
                )
            )
        )
    elif status == BillingSubscriptionStatus.PAST_DUE.value:
        reason = (
            "Le dernier paiement a été refusé : les imports, analyses et rapports sont "
            "suspendus. La lecture reste ouverte. Régularisez le moyen de paiement pour reprendre."
        )
    elif status == BillingSubscriptionStatus.CANCELED.value:
        granted = moment < period_end
        reason = (
            f"Abonnement résilié : l'accès reste ouvert jusqu'au dernier jour payé, le {period_end:%d/%m/%Y}."
            if granted
            else f"Abonnement résilié le {_as_utc(subscription.canceled_at):%d/%m/%Y} ; accès terminé."
            if subscription.canceled_at is not None
            else "Abonnement résilié ; accès terminé."
        )
    else:  # pragma: no cover - un statut inconnu ne doit jamais ouvrir un accès
        reason = f"Statut d'abonnement inconnu ({status}) : aucun accès payant accordé."

    return Entitlement(
        organization_id=organization.id,
        plan=plan,
        status=status,
        granted=granted,
        reason=reason,
        subscription=subscription,
        provider=subscription.provider,
        trial_ends_at=None,
        trial_derived=False,
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=bool(subscription.cancel_at_period_end),
        pending_plan_code=subscription.pending_plan_code,
        pending_plan_effective_at=(
            _as_utc(subscription.pending_plan_effective_at)
            if subscription.pending_plan_effective_at is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Cycle de vie : de la demande de paiement à la résiliation
# ---------------------------------------------------------------------------


def create_checkout_session(
    db: Session,
    *,
    organization: Organization,
    plan: Plan,
    provider_code: str,
    checkout_url: str,
    provider_session_id: str,
    ttl_seconds: int,
    actor_user_id: UUID | None,
    now: datetime | None = None,
) -> BillingCheckoutSession:
    moment = _as_utc(now) if now else utcnow()
    set_db_request_context(
        db,
        user_id=actor_user_id or UUID(int=0),
        organization_id=organization.id,
    )
    session_row = BillingCheckoutSession(
        organization_id=organization.id,
        provider=provider_code,
        provider_session_id=provider_session_id,
        plan_code=plan.code,
        status="pending",
        url=checkout_url,
        expires_at=moment + timedelta(seconds=ttl_seconds),
        created_by_user_id=actor_user_id,
    )
    db.add(session_row)
    db.flush()
    return session_row


def _period_from_payload(
    payload: dict[str, object], *, fallback_start: datetime, months: int
) -> tuple[datetime, datetime]:
    raw_start = payload.get("period_start")
    raw_end = payload.get("period_end")
    if isinstance(raw_start, str) and isinstance(raw_end, str):
        try:
            start = _as_utc(datetime.fromisoformat(raw_start.replace("Z", "+00:00")))
            end = _as_utc(datetime.fromisoformat(raw_end.replace("Z", "+00:00")))
            if end > start:
                return start, end
        except ValueError:
            pass
    start = fallback_start
    return start, add_months(start, months)


@dataclass(frozen=True)
class ApplyOutcome:
    applied: bool
    detail: str
    organization_id: UUID | None


def _status_value(value: BillingSubscriptionStatus | str) -> str:
    return value.value if isinstance(value, BillingSubscriptionStatus) else str(value)


def apply_provider_event(
    db: Session,
    *,
    event: BillingProviderEvent,
    now: datetime | None = None,
) -> ApplyOutcome:
    """Applique un événement prestataire déjà journalisé.

    L'appelant a déjà vérifié la signature et enregistré l'événement ; cette
    fonction ne fait que la transition d'état, et retourne ce qu'elle a fait.
    """

    moment = _as_utc(now) if now else utcnow()
    payload = dict(event.payload_json or {})
    event_type = event.event_type

    organization_id = event.organization_id
    subscription: BillingSubscription | None = None
    provider_subscription_id = payload.get("provider_subscription_id")
    if isinstance(provider_subscription_id, str) and provider_subscription_id:
        subscription = load_subscription_by_provider_id(
            db, provider=event.provider, provider_subscription_id=provider_subscription_id
        )
    if subscription is not None:
        organization_id = subscription.organization_id
    if organization_id is None:
        raw_org = payload.get("organization_id")
        if isinstance(raw_org, str) and raw_org:
            try:
                organization_id = UUID(raw_org)
            except ValueError:
                organization_id = None
    if organization_id is None:
        return ApplyOutcome(False, "organisation inconnue pour cet événement : rien n'a été appliqué", None)

    organization = db.get(Organization, organization_id)
    if organization is None:
        return ApplyOutcome(False, "organisation déclarée dans l'événement introuvable", None)
    set_db_request_context(
        db,
        user_id=UUID(int=0),
        organization_id=organization_id,
    )

    if event_type in {"checkout.completed", "subscription.activated"}:
        plan_code = str(payload.get("plan_code") or "")
        plan = plan_or_none(plan_code)
        if plan is None:
            return ApplyOutcome(False, f"plan inconnu dans l'événement : {plan_code!r}", organization_id)
        trial_end_raw = payload.get("trial_end")
        status = BillingSubscriptionStatus.ACTIVE
        if isinstance(trial_end_raw, str) and trial_end_raw:
            status = BillingSubscriptionStatus.TRIALING
        start, end = _period_from_payload(payload, fallback_start=moment, months=1)
        if subscription is None:
            subscription = BillingSubscription(
                organization_id=organization_id,
                plan_code=plan.code,
                status=status,
                provider=event.provider,
                provider_customer_id=_str_or_none(payload.get("provider_customer_id")),
                provider_subscription_id=_str_or_none(provider_subscription_id),
                provider_price_id=_str_or_none(payload.get("provider_price_id")),
                current_period_start=start,
                current_period_end=end,
                cancel_at_period_end=False,
            )
            db.add(subscription)
        else:
            subscription.plan_code = plan.code
            subscription.status = status
            subscription.current_period_start = start
            subscription.current_period_end = end
            subscription.cancel_at_period_end = False
            subscription.canceled_at = None
            subscription.past_due_since = None
            if provider_subscription_id:
                subscription.provider_subscription_id = provider_subscription_id
        subscription.last_provider_event_id = event.provider_event_id
        _complete_checkout_session(db, organization_id=organization_id, now=moment, plan_code=plan.code)
        db.flush()
        return ApplyOutcome(
            True,
            f"abonnement {plan.code} activé jusqu'au {end:%d/%m/%Y}",
            organization_id,
        )

    if subscription is None:
        return ApplyOutcome(
            False,
            "aucun abonnement connu pour cet identifiant prestataire : rien n'a été appliqué",
            organization_id,
        )

    if event_type == "invoice.paid":
        start, end = _period_from_payload(
            payload,
            fallback_start=_as_utc(subscription.current_period_end),
            months=1,
        )
        subscription.status = BillingSubscriptionStatus.ACTIVE
        subscription.current_period_start = start
        subscription.current_period_end = end
        subscription.past_due_since = None
        applied_plan = None
        if subscription.pending_plan_code and (
            subscription.pending_plan_effective_at is None
            or _as_utc(subscription.pending_plan_effective_at) <= moment
        ):
            applied_plan = subscription.pending_plan_code
            subscription.plan_code = applied_plan
            subscription.pending_plan_code = None
            subscription.pending_plan_effective_at = None
        subscription.last_provider_event_id = event.provider_event_id
        record_invoice(
            db,
            organization_id=organization_id,
            provider=event.provider,
            provider_invoice_id=str(payload.get("provider_invoice_id") or event.provider_event_id),
            amount_cents=int(payload.get("amount_cents") or 0),
            currency=str(payload.get("currency") or "eur"),
            status="paid",
            plan_code=subscription.plan_code,
            period_start=start.date(),
            period_end=end.date(),
            issued_at=moment,
            hosted_url=_str_or_none(payload.get("hosted_invoice_url")),
        )
        db.flush()
        detail = f"période payée jusqu'au {end:%d/%m/%Y}"
        if applied_plan:
            detail += f" ; descente de gamme appliquée vers {applied_plan}"
        return ApplyOutcome(True, detail, organization_id)

    if event_type == "payment.failed":
        subscription.status = BillingSubscriptionStatus.PAST_DUE
        subscription.past_due_since = moment
        subscription.last_provider_event_id = event.provider_event_id
        db.flush()
        return ApplyOutcome(True, "paiement refusé : accès payant suspendu", organization_id)

    if event_type == "subscription.canceled":
        subscription.status = BillingSubscriptionStatus.CANCELED
        subscription.canceled_at = moment
        subscription.cancel_at_period_end = False
        subscription.last_provider_event_id = event.provider_event_id
        end = _as_utc(subscription.current_period_end)
        db.flush()
        return ApplyOutcome(True, f"résiliation enregistrée ; accès jusqu'au {end:%d/%m/%Y}", organization_id)

    if event_type == "subscription.updated":
        changed: list[str] = []
        plan_code = payload.get("plan_code")
        plan = plan_or_none(str(plan_code)) if isinstance(plan_code, str) else None
        if plan is not None and plan.code != subscription.plan_code:
            subscription.plan_code = plan.code
            changed.append(f"plan → {plan.code}")
        if "cancel_at_period_end" in payload:
            subscription.cancel_at_period_end = bool(payload.get("cancel_at_period_end"))
            changed.append(
                "résiliation en fin de période programmée"
                if subscription.cancel_at_period_end
                else "résiliation annulée"
            )
        if "period_start" in payload or "period_end" in payload:
            start, end = _period_from_payload(
                payload,
                fallback_start=_as_utc(subscription.current_period_start),
                months=1,
            )
            subscription.current_period_start = start
            subscription.current_period_end = end
            changed.append(f"période → {start:%d/%m/%Y} – {end:%d/%m/%Y}")
        if "status" in payload:
            raw_status = str(payload.get("status") or "")
            if raw_status in {item.value for item in BillingSubscriptionStatus}:
                subscription.status = BillingSubscriptionStatus(raw_status)
                changed.append(f"statut → {raw_status}")
        subscription.last_provider_event_id = event.provider_event_id
        db.flush()
        return ApplyOutcome(
            True,
            " ; ".join(changed) if changed else "événement sans changement d'état",
            organization_id,
        )

    return ApplyOutcome(False, f"type d'événement non traité : {event_type}", organization_id)


def _str_or_none(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _complete_checkout_session(
    db: Session, *, organization_id: UUID, now: datetime, plan_code: str
) -> None:
    pending = db.scalars(
        select(BillingCheckoutSession).where(
            BillingCheckoutSession.organization_id == organization_id,
            BillingCheckoutSession.plan_code == plan_code,
            BillingCheckoutSession.status == "pending",
        )
    ).all()
    for session_row in pending:
        session_row.status = "completed"
        session_row.completed_at = now


def record_invoice(
    db: Session,
    *,
    organization_id: UUID,
    provider: str,
    provider_invoice_id: str,
    amount_cents: int,
    currency: str,
    status: str,
    plan_code: str | None,
    period_start,
    period_end,
    issued_at: datetime,
    hosted_url: str | None,
) -> BillingInvoice:
    existing = db.scalar(
        select(BillingInvoice).where(
            BillingInvoice.provider == provider,
            BillingInvoice.provider_invoice_id == provider_invoice_id,
        )
    )
    if existing is not None:
        return existing
    set_db_request_context(db, user_id=UUID(int=0), organization_id=organization_id)
    invoice = BillingInvoice(
        organization_id=organization_id,
        provider=provider,
        provider_invoice_id=provider_invoice_id,
        amount_cents=amount_cents,
        currency=currency,
        status=status,
        plan_code=plan_code,
        period_start=period_start,
        period_end=period_end,
        issued_at=_as_utc(issued_at),
        hosted_url=hosted_url,
    )
    db.add(invoice)
    db.flush()
    return invoice


def list_invoices(db: Session, *, organization_id: UUID) -> list[BillingInvoice]:
    return list(
        db.scalars(
            select(BillingInvoice)
            .where(BillingInvoice.organization_id == organization_id)
            .order_by(BillingInvoice.issued_at.desc())
        ).all()
    )


# ---------------------------------------------------------------------------
# Demandes du portail : elles ne changent aucun état local, elles appellent le
# prestataire dont l'événement fera foi.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanChangeRequest:
    plan_code: str
    effective: str  # "immediate" | "period_end"
    note: str


def plan_change_request(*, current_plan_code: str | None, target_plan_code: str) -> PlanChangeRequest:
    """Une montée de gamme prend effet tout de suite, une descente en fin de période.

    L'inverse (appliquer une descente immédiatement) priverait le client d'un
    service déjà payé ; la montée immédiate, elle, est ce qu'il demande et ce
    qu'il paie au prorata.
    """

    if current_plan_code is None:
        return PlanChangeRequest(
            plan_code=target_plan_code,
            effective="immediate",
            note="aucun abonnement en cours : la souscription ouvre une nouvelle période",
        )
    if target_plan_code == current_plan_code:
        raise BillingStateError("L'organisation est déjà sur cette offre.")
    if is_upgrade(from_code=current_plan_code, to_code=target_plan_code):
        return PlanChangeRequest(
            plan_code=target_plan_code,
            effective="immediate",
            note="montée de gamme immédiate ; les quotas de la nouvelle période repartent à zéro",
        )
    return PlanChangeRequest(
        plan_code=target_plan_code,
        effective="period_end",
        note=(
            "descente de gamme programmée pour la fin de la période payée ; "
            "aucun remboursement partiel n'est calculé par ce produit"
        ),
    )


def schedule_pending_plan(
    db: Session,
    *,
    subscription: BillingSubscription,
    plan_code: str,
    effective_at: datetime,
) -> None:
    set_db_request_context(
        db, user_id=UUID(int=0), organization_id=subscription.organization_id
    )
    subscription.pending_plan_code = plan_code
    subscription.pending_plan_effective_at = _as_utc(effective_at)
    db.flush()


def new_provider_reference(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def usage_metric_source(metric: Metric) -> str:
    return metric.value


__all__ = [
    "ApplyOutcome",
    "BillingStateError",
    "Entitlement",
    "PlanChangeRequest",
    "apply_provider_event",
    "create_checkout_session",
    "list_invoices",
    "load_subscription",
    "load_subscription_by_provider_id",
    "new_provider_reference",
    "plan_change_request",
    "record_invoice",
    "resolve_entitlement",
    "schedule_pending_plan",
    "seats_in_use",
    "trial_window",
]
