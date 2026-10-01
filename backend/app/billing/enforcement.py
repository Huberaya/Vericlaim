"""C13 — application réelle des quotas et du statut d'abonnement.

Ce module est le seul endroit qui décide si une action payante est autorisée. Il
est appelé par les routes qui consomment (import de document, OCR, invitation) et
par le calcul de la rétention appliquée.

Ce qu'il refuse, et comment :

* **statut d'abonnement** — essai terminé, paiement refusé, période payée finie
  sans renouvellement confirmé : l'action est refusée en nommant la raison exacte
  et la date concernée. La lecture, elle, reste ouverte ;
* **quota atteint** — 402 avec la métrique, la consommation, la limite, la date de
  remise à zéro et les offres supérieures. **Aucun dépassement n'est facturé** et
  aucune montée de gamme n'est automatique : ``overage_policy`` vaut toujours
  ``blocked_no_charge``.

Une nuance volontaire : :func:`settle_usage` enregistre une consommation sans
pouvoir la refuser. Quand l'OCR a déjà tourné, refuser d'écrire la consommation
ferait disparaître le coût réel du journal — exactement l'inverse d'un journal
d'usage. C'est la tentative *suivante* qui est refusée.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.billing.plans import (
    ENTITLEMENTS,
    METRIC_LABELS,
    Metric,
    Plan,
    upgrade_paths,
)
from app.billing.subscriptions import (
    Entitlement,
    resolve_entitlement,
    seats_in_use,
)
from app.billing.usage import (
    UsageEventResult,
    consumed,
    metering_period,
    period_key_for,
    record_usage,
    utcnow,
)
from app.models.domain import Organization

OVERAGE_POLICY = "blocked_no_charge"


@dataclass(frozen=True)
class QuotaCheck:
    entitlement: Entitlement
    plan: Plan
    metric: Metric
    used: int
    limit: int
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True)
class RetentionWindow:
    declared_months: int | None
    effective_months: int | None
    ceiling_months: int | None
    limited_by_plan: bool
    plan_code: str | None
    note: str


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


METRIC_UNITS: Final[dict[Metric, tuple[str, str]]] = {
    Metric.DOCUMENTS: ("document", "documents"),
    Metric.OCR_PAGES: ("page", "pages"),
    Metric.SEATS: ("siège", "sièges"),
    Metric.RETENTION: ("mois", "mois"),
}


def quota_error_payload(
    *,
    metric: Metric,
    used: int,
    limit: int,
    requested: int,
    plan: Plan,
    entitlements: Entitlement | None = None,
    resets_at: datetime | None = None,
) -> dict[str, Any]:
    label = METRIC_LABELS[metric]
    reset_label = f"{_as_utc(resets_at):%d/%m/%Y}" if resets_at is not None else "la prochaine période"
    # C21 : le refus doit dire **combien** est demandé et **combien** il reste. Un
    # « quota atteint » sans chiffres oblige l'utilisateur à deviner la taille de son
    # document — et masque le fait que le contrôle porte sur les pages réelles.
    singular, plural = METRIC_UNITS[metric]
    unit = singular if requested == 1 else plural
    remaining = max(limit - used, 0)
    return {
        "code": "quota_exceeded",
        "metric": metric.value,
        "metric_label": label,
        "used": used,
        "limit": limit,
        "requested": requested,
        "plan_code": plan.code,
        "resets_at": _as_utc(resets_at).isoformat() if resets_at is not None else None,
        "overage_policy": OVERAGE_POLICY,
        "message": (
            f"Quota atteint : {used}/{limit} {label} sur l'offre {plan.name}. "
            f"Cette opération demande {requested} {unit} et il en reste {remaining} sur la période. "
            f"Elle est refusée **avant** d'être engagée : rien n'a été enregistré et **aucun dépassement ne sera facturé** "
            f"(aucune facturation à l'usage n'existe dans le produit). "
            f"Le quota repart au premier jour de la période suivante ({reset_label}), "
            f"ou immédiatement en passant à une offre supérieure."
        ),
        "upgrade_options": [
            {
                "code": plan_code.code,
                "name": plan_code.name,
                "price_label": plan_code.price_label(),
                "documents_per_month": plan_code.quotas.documents_per_month,
                "ocr_pages_per_month": plan_code.quotas.ocr_pages_per_month,
                "seats": plan_code.quotas.seats,
                "retention_months": plan_code.quotas.retention_months,
                "limits_are_reference_values": plan_code.limits_are_reference_values,
            }
            for plan_code in upgrade_paths(plan.code)
        ],
        "portal_path": "/api/v1/billing/subscription",
    }


def subscription_error_payload(entitlement: Entitlement) -> dict[str, Any]:
    return {
        "code": "subscription_inactive",
        "status": entitlement.status,
        "plan_code": entitlement.plan_code,
        "reason": entitlement.reason,
        "trial_ends_at": entitlement.trial_ends_at.isoformat() if entitlement.trial_ends_at else None,
        "current_period_end": entitlement.current_period_end.isoformat(),
        "overage_policy": OVERAGE_POLICY,
        "message": (
            f"{entitlement.reason} Les imports, analyses et rapports signés sont refusés ; "
            "la consultation de ce qui existe déjà reste possible."
        ),
        "portal_path": "/api/v1/billing/subscription",
    }


class SubscriptionInactiveError(RuntimeError):
    """L'organisation n'a pas d'abonnement en état de payer."""

    def __init__(self, entitlement: Entitlement) -> None:
        super().__init__(entitlement.reason)
        self.entitlement = entitlement

    @property
    def detail(self) -> dict[str, Any]:
        return subscription_error_payload(self.entitlement)


class EntitlementNotIncludedError(RuntimeError):
    """Le plan souscrit n'inclut pas ce droit (et aucun essai ne le rattrape)."""

    def __init__(self, *, entitlement: Entitlement, code: str, label: str, enforced_by: str) -> None:
        super().__init__(f"Le plan {entitlement.plan_code or 'inconnu'} n'inclut pas : {label}.")
        self.entitlement = entitlement
        self.entitlement_code = code
        self.label = label
        self.enforced_by = enforced_by

    @property
    def detail(self) -> dict[str, Any]:
        return {
            "code": "entitlement_not_included",
            "entitlement": self.entitlement_code,
            "entitlement_label": self.label,
            "plan_code": self.entitlement.plan_code,
            "enforced_by": self.enforced_by,
            "overage_policy": OVERAGE_POLICY,
            "message": (
                f"L'offre {self.entitlement.plan_code or 'en cours'} n'inclut pas : {self.label}. "
                "Aucun accès n'est ouvert à l'essai pour ce droit."
            ),
            "upgrade_options": [
                {
                    "code": candidate.code,
                    "name": candidate.name,
                    "price_label": candidate.price_label(),
                }
                for candidate in upgrade_paths(self.entitlement.plan_code or "")
                if self.entitlement_code in candidate.entitlements
            ],
            "portal_path": "/api/v1/billing/subscription",
        }


class QuotaExceededError(RuntimeError):
    """Le quota mesuré de la période est atteint."""

    def __init__(self, check: QuotaCheck, *, requested: int) -> None:
        super().__init__(f"Quota {check.metric.value} atteint ({check.used}/{check.limit}).")
        self.check = check
        self.requested = requested

    @property
    def detail(self) -> dict[str, Any]:
        return quota_error_payload(
            metric=self.check.metric,
            used=self.check.used,
            limit=self.check.limit,
            requested=self.requested,
            plan=self.check.plan,
            resets_at=self.check.period_end,
        )


def load_organization(db: Session, *, organization_id: UUID) -> Organization:
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise LookupError(f"Organisation introuvable : {organization_id}")
    return organization


def entitlement_for(db: Session, *, organization_id: UUID, now: datetime | None = None) -> Entitlement:
    return resolve_entitlement(db, organization=load_organization(db, organization_id=organization_id), now=now)


def require_granted_entitlement(
    db: Session, *, organization_id: UUID, now: datetime | None = None
) -> Entitlement:
    entitlement = resolve_entitlement(db, organization=load_organization(db, organization_id=organization_id), now=now)
    if not entitlement.granted:
        raise SubscriptionInactiveError(entitlement)
    return entitlement


def assert_quota_available(
    db: Session,
    *,
    organization_id: UUID,
    metric: Metric,
    quantity: int = 1,
    now: datetime | None = None,
) -> QuotaCheck:
    """Vérifie *avant* de consommer : aucune ressource n'est engagée si le quota est plein."""

    entitlement = require_granted_entitlement(db, organization_id=organization_id, now=now)
    plan = entitlement.plan
    if plan is None:  # pragma: no cover - un abonnement accordé a toujours un plan
        raise SubscriptionInactiveError(entitlement)
    period = metering_period(entitlement=entitlement, now=now)
    used = consumed(
        db,
        organization_id=organization_id,
        metric=metric,
        period_key=period_key_for(period),
    )
    limit = plan.quotas.limit_for(metric)
    check = QuotaCheck(
        entitlement=entitlement,
        plan=plan,
        metric=metric,
        used=used,
        limit=limit,
        period_start=period[0],
        period_end=period[1],
    )
    if used + quantity > limit:
        raise QuotaExceededError(check, requested=quantity)
    return check


def consume_quota(
    db: Session,
    *,
    organization_id: UUID,
    metric: Metric,
    quantity: int,
    source_type: str,
    source_id: str | None,
    actor_user_id: UUID | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> UsageEventResult:
    """Vérifie puis enregistre. Le contrôle et l'écriture sont indissociables."""

    check = assert_quota_available(
        db, organization_id=organization_id, metric=metric, quantity=quantity, now=now
    )
    return record_usage(
        db,
        organization_id=organization_id,
        metric=metric,
        quantity=quantity,
        source_type=source_type,
        source_id=source_id,
        period_key=period_key_for((check.period_start, check.period_end)),
        occurred_at=now,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
    )


def settle_usage(
    db: Session,
    *,
    organization_id: UUID,
    metric: Metric,
    quantity: int,
    source_type: str,
    source_id: str | None,
    actor_user_id: UUID | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
    period_start: datetime | None = None,
) -> UsageEventResult:
    """Enregistre une consommation déjà engagée, sans la refuser.

    Utilisé après l'OCR : le travail a eu lieu, le journal doit le porter, sinon
    la consommation affichée serait inférieure à la consommation réelle.
    """

    start = period_start
    if start is None:
        organization = load_organization(db, organization_id=organization_id)
        entitlement = resolve_entitlement(db, organization=organization, now=now)
        start = metering_period(entitlement=entitlement, now=now)[0]
    return record_usage(
        db,
        organization_id=organization_id,
        metric=metric,
        quantity=quantity,
        source_type=source_type,
        source_id=source_id,
        period_key=period_key_for((start, start)),
        occurred_at=now,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
    )


def assert_seat_available(
    db: Session, *, organization_id: UUID, now: datetime | None = None
) -> tuple[Entitlement, int, int]:
    """Vérifie qu'un siège est libre avant d'inviter quelqu'un."""

    entitlement = require_granted_entitlement(db, organization_id=organization_id, now=now)
    plan = entitlement.plan
    if plan is None:  # pragma: no cover
        raise SubscriptionInactiveError(entitlement)
    used = seats_in_use(db, organization_id=organization_id)
    if used + 1 > plan.quotas.seats:
        check = QuotaCheck(
            entitlement=entitlement,
            plan=plan,
            metric=Metric.SEATS,
            used=used,
            limit=plan.quotas.seats,
            period_start=entitlement.current_period_start,
            period_end=entitlement.current_period_end,
        )
        raise QuotaExceededError(check, requested=1)
    return entitlement, used, plan.quotas.seats


def assert_entitlement_included(
    db: Session, *, organization_id: UUID, code: str, now: datetime | None = None
) -> Entitlement:
    """Vérifie un droit binaire du plan (clé d'API, par exemple)."""

    entitlement = resolve_entitlement(
        db, organization=load_organization(db, organization_id=organization_id), now=now
    )
    if entitlement.plan is not None and code in entitlement.plan.entitlements:
        return entitlement
    definition = next((item for item in ENTITLEMENTS if item.code == code), None)
    raise EntitlementNotIncludedError(
        entitlement=entitlement,
        code=code,
        label=definition.label if definition else code,
        enforced_by=definition.enforced_by if definition else "code produit",
    )


def effective_retention_months(
    db: Session,
    *,
    organization_id: UUID,
    declared_months: int | None,
    now: datetime | None = None,
) -> RetentionWindow:
    """La durée de conservation réellement appliquée, et pourquoi.

    La durée déclarée par le client est un plafond qu'il se fixe ; l'offre est un
    plafond que le produit applique. La plus courte des deux gagne, et le produit
    publie les deux — purger selon une durée que le client n'a pas déclarée sans
    le dire serait une perte de données silencieuse.
    """

    entitlement = resolve_entitlement(db, organization=load_organization(db, organization_id=organization_id), now=now)
    ceiling = entitlement.plan.quotas.retention_months if entitlement.plan else None
    if declared_months is None:
        return RetentionWindow(
            declared_months=None,
            effective_months=None,
            ceiling_months=ceiling,
            limited_by_plan=False,
            plan_code=entitlement.plan_code,
            note=(
                "Aucune durée déclarée : rien n'est purgé au titre de la conservation. "
                f"L'offre {entitlement.plan_code or 'inconnue'} couvre au plus {ceiling} mois "
                "si une durée est déclarée."
                if ceiling is not None
                else "Aucune durée déclarée et aucune offre active : rien n'est purgé."
            ),
        )
    if ceiling is None:
        return RetentionWindow(
            declared_months=declared_months,
            effective_months=declared_months,
            ceiling_months=None,
            limited_by_plan=False,
            plan_code=None,
            note="Aucune offre active : la durée déclarée est appliquée telle quelle.",
        )
    effective = min(declared_months, ceiling)
    limited = declared_months > ceiling
    return RetentionWindow(
        declared_months=declared_months,
        effective_months=effective,
        ceiling_months=ceiling,
        limited_by_plan=limited,
        plan_code=entitlement.plan_code,
        note=(
            f"Durée déclarée ({declared_months} mois) supérieure à ce que couvre l'offre "
            f"{entitlement.plan_code} ({ceiling} mois) : la conservation effectivement appliquée "
            f"est de {effective} mois. Le produit ne promet pas une durée qu'il ne finance pas."
            if limited
            else f"Durée déclarée couverte par l'offre {entitlement.plan_code} ({ceiling} mois)."
        ),
    )


def remaining_ocr_pages(
    db: Session, *, organization_id: UUID, now: datetime | None = None
) -> tuple[int, int]:
    """Le solde de pages OCR de la période, sans lever d'exception."""

    entitlement = resolve_entitlement(db, organization=load_organization(db, organization_id=organization_id), now=now)
    plan = entitlement.plan
    if plan is None:  # pragma: no cover
        return 0, 0
    period = metering_period(entitlement=entitlement, now=now)
    used = consumed(
        db,
        organization_id=organization_id,
        metric=Metric.OCR_PAGES,
        period_key=period_key_for(period),
    )
    limit = plan.quotas.ocr_pages_per_month
    return max(limit - used, 0), limit


def now_utc() -> datetime:
    return utcnow()


__all__ = [
    "OVERAGE_POLICY",
    "EntitlementNotIncludedError",
    "QuotaCheck",
    "QuotaExceededError",
    "RetentionWindow",
    "SubscriptionInactiveError",
    "assert_entitlement_included",
    "assert_quota_available",
    "assert_seat_available",
    "consume_quota",
    "effective_retention_months",
    "entitlement_for",
    "load_organization",
    "now_utc",
    "quota_error_payload",
    "remaining_ocr_pages",
    "require_granted_entitlement",
    "settle_usage",
    "subscription_error_payload",
]
