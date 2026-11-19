"""C13 — journal d'usage : ce qui est compté, quand, et par quelle source.

Le journal est **append-only**. Aucune colonne « compteur » n'existe : la
consommation d'une période est la somme des événements de cette période. Un
compteur incrémenté dérive dès qu'un chemin d'écriture l'oublie, et un compteur
qui dérive ne s'explique pas à un client qui conteste sa limite.

Chaque événement porte une clé d'idempotence dérivée de sa source
(`document:<uuid>`, `ocr:<version_uuid>`). Un import rejoué, un job repris après
un redémarrage du worker, un double appel d'API : la consommation reste exacte.

La période de comptage est celle de l'abonnement (celle qui est payée) et, à
défaut d'abonnement (essai dérivé), le mois calendaire. Ce choix est visible dans
la réponse de l'API, qui publie les dates de la période en cours : un utilisateur
à qui l'on annonce « 30 documents par mois » doit lire la même chose que ce que
le code applique.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing.plans import METRIC_LABELS, Metric, Plan
from app.identity.service import set_db_request_context
from app.models.domain import BillingUsageEvent

if TYPE_CHECKING:  # pragma: no cover - import de typage uniquement
    from app.billing.subscriptions import Entitlement

# Un acteur système (worker, tâche planifiée) n'a pas d'utilisateur : le contexte
# RLS exige un UUID, on reprend la convention des workers (`UUID(int=0)`).
SYSTEM_ACTOR_USER_ID = UUID(int=0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def month_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """Le mois calendaire UTC contenant ``moment``, bornes incluses/exclues."""

    moment = _as_utc(moment)
    start = datetime.combine(moment.date().replace(day=1), time.min, tzinfo=timezone.utc)
    if start.month == 12:
        following = start.replace(year=start.year + 1, month=1)
    else:
        following = start.replace(month=start.month + 1)
    return start, following


def add_months(moment: datetime, months: int) -> datetime:
    """Ajoute des mois à une date UTC, en gardant le jour quand il existe."""

    moment = _as_utc(moment)
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = moment.day
    while day > 28:
        try:
            return moment.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1
    return moment.replace(year=year, month=month, day=day)


def metering_period(*, entitlement: "Entitlement", now: datetime | None = None) -> tuple[datetime, datetime]:
    """La période à laquelle une consommation est rattachée, et sa fin (remise à zéro)."""

    moment = _as_utc(now) if now else utcnow()
    if entitlement.subscription is not None:
        start = _as_utc(entitlement.subscription.current_period_start)
        end = _as_utc(entitlement.subscription.current_period_end)
        if end > start:
            return start, end
    return month_bounds(moment)


def period_key_for(period: tuple[datetime, datetime]) -> str:
    """L'identifiant exact de la période de comptage (ISO-8601 UTC).

    Une date ne suffit pas : deux périodes payées peuvent commencer le même jour
    (une montée de gamme, par exemple) et une clé au jour près les confondrait,
    ce qui remettrait à zéro un quota déjà consommé.
    """

    return _as_utc(period[0]).astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class UsageEventResult:
    event_id: UUID
    recorded: bool  # False = l'événement existait déjà (idempotence), rien n'a été ajouté


def record_usage(
    db: Session,
    *,
    organization_id: UUID,
    metric: Metric,
    quantity: int,
    source_type: str,
    source_id: str | None,
    period_key: str,
    occurred_at: datetime | None = None,
    actor_user_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> UsageEventResult:
    """Enregistre une consommation, au plus une fois par clé d'idempotence.

    Le contexte de locataire est reposé avant l'écriture : `set_config(...,
    is_local=true)` ne survit pas à un `COMMIT`, et une écriture sans contexte
    est refusée par RLS sur PostgreSQL — un oubli serait visible en production
    mais pas en test SQLite, donc on ne compte pas sur l'appelant pour y penser.
    """

    if quantity <= 0:
        raise ValueError("Une consommation doit être strictement positive.")
    key = idempotency_key or f"{source_type}:{source_id or organization_id}"
    existing = db.scalar(
        select(BillingUsageEvent).where(
            BillingUsageEvent.organization_id == organization_id,
            BillingUsageEvent.idempotency_key == key,
        )
    )
    if existing is not None:
        return UsageEventResult(event_id=existing.id, recorded=False)

    set_db_request_context(
        db,
        user_id=actor_user_id or SYSTEM_ACTOR_USER_ID,
        organization_id=organization_id,
    )
    event = BillingUsageEvent(
        organization_id=organization_id,
        metric=metric.value,
        quantity=quantity,
        period_key=period_key,
        occurred_at=_as_utc(occurred_at) if occurred_at else utcnow(),
        source_type=source_type,
        source_id=source_id,
        idempotency_key=key,
        recorded_by_user_id=actor_user_id,
    )
    db.add(event)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # Course entre deux workers : la contrainte d'unicité a tranché, la ligne
        # existe désormais, et la consommation ne doit pas être comptée deux fois.
        db.rollback()
        set_db_request_context(
            db,
            user_id=actor_user_id or SYSTEM_ACTOR_USER_ID,
            organization_id=organization_id,
        )
        existing = db.scalar(
            select(BillingUsageEvent).where(
                BillingUsageEvent.organization_id == organization_id,
                BillingUsageEvent.idempotency_key == key,
            )
        )
        if existing is None:  # pragma: no cover - défensif
            raise
        return UsageEventResult(event_id=existing.id, recorded=False)
    return UsageEventResult(event_id=event.id, recorded=True)


def consumed(db: Session, *, organization_id: UUID, metric: Metric, period_key: str) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(BillingUsageEvent.quantity), 0)).where(
            BillingUsageEvent.organization_id == organization_id,
            BillingUsageEvent.metric == metric.value,
            BillingUsageEvent.period_key == period_key,
        )
    )
    return int(total or 0)


def consumed_since(db: Session, *, organization_id: UUID, metric: Metric, since: datetime) -> int:
    """Consommation depuis une date précise (utilisée par le test de fumée et l'export)."""

    total = db.scalar(
        select(func.coalesce(func.sum(BillingUsageEvent.quantity), 0)).where(
            BillingUsageEvent.organization_id == organization_id,
            BillingUsageEvent.metric == metric.value,
            BillingUsageEvent.occurred_at >= _as_utc(since),
        )
    )
    return int(total or 0)


@dataclass(frozen=True)
class UsageLine:
    metric: Metric
    label: str
    used: int
    limit: int | None
    remaining: int | None
    exceeded: bool
    counted: bool


@dataclass(frozen=True)
class UsageSnapshot:
    plan_code: str | None
    period_start: datetime
    period_end: datetime
    resets_at: datetime
    lines: tuple[UsageLine, ...]

    def line(self, metric: Metric) -> UsageLine:
        for line in self.lines:
            if line.metric is metric:
                return line
        raise KeyError(metric)


def usage_snapshot(
    db: Session,
    *,
    entitlement: "Entitlement",
    now: datetime | None = None,
    seats_used: int | None = None,
) -> UsageSnapshot:
    """La consommation réelle de la période, limites incluses.

    ``seats_used`` est fourni par l'appelant (le nombre de sièges occupés dépend
    des adhésions, pas du journal d'usage) ; à défaut, la ligne est publiée sans
    mesure plutôt qu'avec un chiffre inventé.
    """

    moment = _as_utc(now) if now else utcnow()
    period = metering_period(entitlement=entitlement, now=moment)
    key = period_key_for(period)
    plan: Plan | None = entitlement.plan
    lines: list[UsageLine] = []
    for metric in (Metric.DOCUMENTS, Metric.OCR_PAGES):
        used = consumed(db, organization_id=entitlement.organization_id, metric=metric, period_key=key)
        limit = plan.quotas.limit_for(metric) if plan else None
        lines.append(
            UsageLine(
                metric=metric,
                label=METRIC_LABELS[metric],
                used=used,
                limit=limit,
                remaining=None if limit is None else max(limit - used, 0),
                exceeded=limit is not None and used >= limit,
                counted=True,
            )
        )
    seat_limit = plan.quotas.seats if plan else None
    lines.append(
        UsageLine(
            metric=Metric.SEATS,
            label=METRIC_LABELS[Metric.SEATS],
            used=seats_used if seats_used is not None else 0,
            limit=seat_limit,
            remaining=None if seat_limit is None or seats_used is None else max(seat_limit - seats_used, 0),
            exceeded=seat_limit is not None and seats_used is not None and seats_used >= seat_limit,
            counted=False,
        )
    )
    return UsageSnapshot(
        plan_code=plan.code if plan else None,
        period_start=period[0],
        period_end=period[1],
        resets_at=period[1],
        lines=tuple(lines),
    )


def periods_between(start: datetime, end: datetime) -> list[date]:
    """Les premiers jours des mois calendaires couverts (export et rapprochements)."""

    cursor, stop = month_bounds(start)[0], _as_utc(end)
    months: list[date] = []
    while cursor < stop:
        months.append(cursor.date())
        cursor = add_months(cursor, 1)
    return months


def retention_cutoff(*, now: datetime, months: int) -> datetime:
    """La date avant laquelle un document n'est plus couvert par le plan."""

    return add_months(_as_utc(now), -months)


def days_between(start: datetime, end: datetime) -> int:
    return max((_as_utc(end) - _as_utc(start)).days, 0)


def next_reset_label(resets_at: datetime) -> str:
    return _as_utc(resets_at).strftime("%d/%m/%Y")


__all__ = [
    "SYSTEM_ACTOR_USER_ID",
    "UsageEventResult",
    "UsageLine",
    "UsageSnapshot",
    "add_months",
    "consumed",
    "consumed_since",
    "days_between",
    "metering_period",
    "month_bounds",
    "next_reset_label",
    "period_key_for",
    "periods_between",
    "record_usage",
    "retention_cutoff",
    "usage_snapshot",
    "utcnow",
]
