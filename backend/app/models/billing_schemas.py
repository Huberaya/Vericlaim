"""C13 — schémas de l'API de facturation.

Tous les modèles interdisent les champs supplémentaires : une réponse qui
n'expose pas ce qu'elle prétend exposer est un défaut, pas une tolérance. Chaque
champ qui pourrait induire en erreur est accompagné de ce qui le rend vérifiable
(``money_collected``, ``state_synced``, ``pricing_status``, ``overage_policy``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlanQuotasRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents_per_month: int
    ocr_pages_per_month: int
    seats: int
    retention_months: int


class PlanRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    tagline: str
    price_cents_per_month_excl_vat: int | None
    price_label: str
    quotas: PlanQuotasRead
    entitlements: list[str]
    limits_are_reference_values: bool


class PlanCatalogueRead(BaseModel):
    """Le catalogue **tel qu'il est appliqué**, et l'état de ses prix."""

    model_config = ConfigDict(extra="forbid")

    catalogue_version: str
    pricing_status: str
    pricing_confirmed: bool
    overage_policy: str
    #: Qui encaisse réellement, et si de l'argent est réellement encaissé.
    provider: str
    collects_money: bool
    selling_enabled: bool
    plans: list[PlanRead]
    trial_plan_code: str
    trial_days: int
    notes: list[str]


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    plan_code: str | None
    plan_name: str | None
    status: str
    granted: bool
    reason: str
    provider: str | None
    trial_ends_at: datetime | None
    trial_derived_from_organization_creation: bool
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    pending_plan_code: str | None
    pending_plan_effective_at: datetime | None
    seats_used: int
    seats_limit: int | None
    quotas: PlanQuotasRead | None
    overage_policy: str
    available_actions: list[str]
    #: Ce qu'un changement de plan ferait, dit avant de le demander.
    upgrade_options: list[PlanRead]
    downgrade_options: list[PlanRead]


class UsageLineRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    label: str
    used: int
    limit: int | None
    remaining: int | None
    exceeded: bool
    counted: bool


class UsageRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    plan_code: str | None
    period_start: datetime
    period_end: datetime
    resets_at: datetime
    overage_policy: str
    lines: list[UsageLineRead]
    note: str


class InvoiceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_invoice_id: str
    amount_cents: int
    currency: str
    status: str
    plan_code: str | None
    period_start: date | None
    period_end: date | None
    issued_at: datetime
    hosted_url: str | None


class InvoiceListRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoices: list[InvoiceRead]
    provider: str | None
    note: str


class CheckoutRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: str


class CheckoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    url: str
    provider: str
    expires_at: datetime
    #: Vrai seulement si le prestataire configuré encaisse réellement.
    money_collected: bool
    state_synced: bool
    detail: str


class PlanChangeRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: str


class PlanChangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_plan_code: str
    current_plan_code: str | None
    effective: str
    applied: bool
    detail: str
    state_synced: bool
    subscription: SubscriptionRead


class CancellationRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_period_end: bool = True
    reason: str | None = None


class CancellationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: bool
    detail: str
    state_synced: bool
    subscription: SubscriptionRead


class WebhookAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    event_id: str
    event_type: str
    applied: bool
    duplicate: bool
    detail: str


class LocalPaymentRequestBody(BaseModel):
    """Réservé au prestataire de développement : aucun encaissement réel."""

    model_config = ConfigDict(extra="forbid")

    plan_code: str | None = None
    amount_cents: int | None = Field(default=None, ge=0)


class LocalPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulated: bool
    money_collected: bool
    detail: str
    webhook: WebhookAck
    subscription: SubscriptionRead


class BillingHealthRead(BaseModel):
    """Ce que le module peut faire, dit sans le simuler."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    collects_money: bool
    selling_enabled: bool
    pricing_status: str
    trial_days: int
    enforcement_points: list[dict[str, Any]]
    limitations: list[str]
