"""C13 — portail client de facturation : offres, abonnement, usage, factures.

Le contrat de cette surface, en une phrase : **elle ne dit jamais qu'une chose a
eu lieu quand elle n'a pas eu lieu.**

* ``GET /plans`` est public et publie l'état des prix (``pricing_status``), qui
  encaisse réellement (``provider``) et si de l'argent est réellement encaissé
  (``collects_money``) ;
* ``POST /checkout`` crée une session chez le prestataire et retourne son URL.
  Aucun accès n'est accordé à ce stade : c'est l'événement signé du prestataire
  qui active l'abonnement ;
* les changements de plan et les résiliations sont **demandés** au prestataire.
  Quand celui-ci répond de façon synchrone (prestataire local), l'événement est
  appliqué tout de suite et ``state_synced`` vaut ``true``. Avec Stripe, l'état
  reste inchangé jusqu'au webhook : ``state_synced`` vaut ``false`` et le message
  le dit ;
* ``POST /webhook/{provider}`` vérifie la signature, journalise l'événement et
  ne l'applique qu'une fois (un rejeu répond ``duplicate: true``) ;
* la route de paiement simulé n'existe que si le prestataire est ``local`` **et**
  que l'environnement n'est pas de production. Elle dit « simulé » dans son
  propre payload.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import plans as catalogue
from app.billing import providers as provider_module
from app.billing import subscriptions as billing
from app.billing.http import require_active_subscription
from app.billing.subscriptions import BillingStateError, seats_in_use
from app.billing.usage import usage_snapshot
from app.core.config import settings
from app.core.database import get_db
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.billing_schemas import (
    BillingHealthRead,
    CancellationRequestBody,
    CancellationResponse,
    CheckoutRequestBody,
    CheckoutResponse,
    InvoiceListRead,
    InvoiceRead,
    LocalPaymentRequestBody,
    LocalPaymentResponse,
    PlanCatalogueRead,
    PlanChangeRequestBody,
    PlanChangeResponse,
    PlanQuotasRead,
    PlanRead,
    SubscriptionRead,
    UsageLineRead,
    UsageRead,
    WebhookAck,
)
from app.models.domain import BillingCheckoutSession, BillingProviderEvent, BillingSubscription

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
# Routeur réservé au prestataire de développement : inclus conditionnellement par
# ``app.main`` (jamais en environnement de production).
local_dev_router = APIRouter(prefix="/api/v1/billing", tags=["billing-local-dev"])

logger = logging.getLogger("vericlaim.billing")

DATABASE_DEPENDENCY = Depends(get_db)
BILLING_READ_DEPENDENCY = Depends(require_permission("billing:read"))
BILLING_MANAGE_DEPENDENCY = Depends(require_permission("billing:manage", csrf_protected=True))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _plan_read(plan: catalogue.Plan) -> PlanRead:
    return PlanRead(
        code=plan.code,
        name=plan.name,
        tagline=plan.tagline,
        price_cents_per_month_excl_vat=plan.price_cents_per_month_excl_vat,
        price_label=plan.price_label(),
        quotas=PlanQuotasRead(
            documents_per_month=plan.quotas.documents_per_month,
            ocr_pages_per_month=plan.quotas.ocr_pages_per_month,
            seats=plan.quotas.seats,
            retention_months=plan.quotas.retention_months,
        ),
        entitlements=sorted(plan.entitlements),
        limits_are_reference_values=plan.limits_are_reference_values,
    )


def _catalogue_read() -> PlanCatalogueRead:
    provider = provider_module.provider_for(settings)
    collecting = bool(provider is not None and provider.is_configured() and settings.billing_collects_money)
    notes = [
        "Aucun dépassement de quota n'est facturé : un quota atteint bloque l'action "
        "suivante avec une erreur explicite, et le quota repart à la période suivante.",
        "Les prix de cette page sont des valeurs de catalogue. Elles ne sont pas "
        "présentées comme validées commercialement tant que pricing_status le dit.",
    ]
    if not collecting:
        notes.append(
            "Aucun encaissement n'est possible avec la configuration actuelle "
            f"(prestataire « {settings.billing_provider} ») : les offres peuvent être "
            "consultées, aucune souscription payante n'est ouverte."
        )
    return PlanCatalogueRead(
        catalogue_version=catalogue.CATALOGUE_VERSION,
        pricing_status=catalogue.pricing_status(),
        pricing_confirmed=catalogue.PRICING_CONFIRMED,
        overage_policy="blocked_no_charge",
        provider=settings.billing_provider,
        collects_money=collecting,
        selling_enabled=provider is not None,
        plans=[_plan_read(plan) for plan in catalogue.PLANS],
        trial_plan_code=catalogue.DEFAULT_TRIAL_PLAN_CODE,
        trial_days=catalogue.TRIAL_DAYS,
        notes=notes,
    )


@router.get("/plans", response_model=PlanCatalogueRead)
def read_plan_catalogue() -> PlanCatalogueRead:
    """Les offres telles qu'elles sont appliquées — page publique des tarifs."""

    return _catalogue_read()


@router.get("/health", response_model=BillingHealthRead)
def read_billing_health() -> BillingHealthRead:
    """Ce que le module applique vraiment, et ce qu'il ne fait pas."""

    provider = provider_module.provider_for(settings)
    return BillingHealthRead(
        provider=settings.billing_provider,
        collects_money=bool(provider is not None and provider.is_configured() and settings.billing_collects_money),
        selling_enabled=provider is not None,
        pricing_status=catalogue.pricing_status(),
        trial_days=catalogue.TRIAL_DAYS,
        enforcement_points=[
            {"metric": "documents", "enforced_at": "création du document (POST /api/v1/documents)"},
            {
                "metric": "ocr_pages",
                "enforced_at": "mise en file de l'extraction, consommation enregistrée à la publication",
            },
            {"metric": "seats", "enforced_at": "invitation d'un membre"},
            {
                "metric": "retention",
                "enforced_at": "plan de purge (GET /api/v1/privacy/retention, POST /api/v1/privacy/purge)",
            },
            {
                "metric": "subscription_status",
                "enforced_at": "dépendance require_active_subscription sur les routes qui produisent",
            },
        ],
        limitations=[
            "Aucune facturation à l'usage, aucun dépassement facturé, aucune montée de gamme "
            "automatique : ces trois comportements n'existent pas dans ce code.",
            "L'adaptateur Stripe est écrit mais n'a jamais été exécuté : aucun compte ni clé "
            "n'existe dans cet environnement.",
            "Les prix du catalogue ne sont pas validés commercialement (pricing_status).",
            "Aucun calcul de TVA, aucune facture émise ici : les factures affichées sont celles "
            "du prestataire, recopiées localement.",
        ],
    )


def _organization_of(principal: TenantPrincipal):
    return principal.membership_view.organization


def _subscription_read(principal: TenantPrincipal, db: Session) -> SubscriptionRead:
    organization = _organization_of(principal)
    entitlement = billing.resolve_entitlement(db, organization=organization)
    seats = seats_in_use(db, organization_id=organization.id)
    actions: list[str] = []
    if not entitlement.granted:
        actions.append("subscribe")
    else:
        actions.append("change_plan")
    if entitlement.subscription is not None and entitlement.granted:
        actions.append("resume" if entitlement.cancel_at_period_end else "cancel")
    upgrades = catalogue.upgrade_paths(entitlement.plan_code or catalogue.NO_PLAN_CODE)
    downgrades = (
        tuple(
            plan
            for plan in catalogue.PLANS
            if plan.code != entitlement.plan_code
            and not catalogue.is_upgrade(
                from_code=entitlement.plan_code or catalogue.NO_PLAN_CODE, to_code=plan.code
            )
        )
        if entitlement.plan_code
        else ()
    )
    return SubscriptionRead(
        organization_id=str(organization.id),
        plan_code=entitlement.plan_code,
        plan_name=entitlement.plan.name if entitlement.plan else None,
        status=entitlement.status,
        granted=entitlement.granted,
        reason=entitlement.reason,
        # Aucun prestataire pour un essai dérivé : il n'y a pas de relation de
        # paiement, et inventer « local » ici ferait croire à un abonnement.
        provider=entitlement.provider,
        trial_ends_at=entitlement.trial_ends_at,
        trial_derived_from_organization_creation=entitlement.trial_derived,
        current_period_start=entitlement.current_period_start,
        current_period_end=entitlement.current_period_end,
        cancel_at_period_end=entitlement.cancel_at_period_end,
        pending_plan_code=entitlement.pending_plan_code,
        pending_plan_effective_at=entitlement.pending_plan_effective_at,
        seats_used=seats,
        seats_limit=entitlement.plan.quotas.seats if entitlement.plan else None,
        quotas=(
            PlanQuotasRead(
                documents_per_month=entitlement.plan.quotas.documents_per_month,
                ocr_pages_per_month=entitlement.plan.quotas.ocr_pages_per_month,
                seats=entitlement.plan.quotas.seats,
                retention_months=entitlement.plan.quotas.retention_months,
            )
            if entitlement.plan
            else None
        ),
        overage_policy="blocked_no_charge",
        available_actions=actions,
        upgrade_options=[_plan_read(plan) for plan in upgrades],
        downgrade_options=[_plan_read(plan) for plan in downgrades],
    )


@router.get("/subscription", response_model=SubscriptionRead)
def read_subscription(
    principal: TenantPrincipal = BILLING_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SubscriptionRead:
    return _subscription_read(principal, db)


@router.get("/usage", response_model=UsageRead)
def read_usage(
    principal: TenantPrincipal = BILLING_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> UsageRead:
    organization = _organization_of(principal)
    entitlement = billing.resolve_entitlement(db, organization=organization)
    seats = seats_in_use(db, organization_id=organization.id)
    snapshot = usage_snapshot(db, entitlement=entitlement, seats_used=seats)
    return UsageRead(
        organization_id=str(organization.id),
        plan_code=snapshot.plan_code,
        period_start=snapshot.period_start,
        period_end=snapshot.period_end,
        resets_at=snapshot.resets_at,
        overage_policy="blocked_no_charge",
        lines=[
            UsageLineRead(
                metric=line.metric.value,
                label=line.label,
                used=line.used,
                limit=line.limit,
                remaining=line.remaining,
                exceeded=line.exceeded,
                counted=line.counted,
            )
            for line in snapshot.lines
        ],
        note=(
            "La consommation est la somme du journal d'usage de la période en cours "
            "(aucun compteur séparé ne peut dériver). Les sièges sont comptés à l'instant, "
            "invitations en attente comprises. Les pages analysées sont comptées page par "
            "page, à partir du document réellement transcrit : une page dont le texte est "
            "natif comme une page lue par OCR. Un import refusé pour quota ne consomme aucune "
            "page, mais le document créé avant lui reste compté au titre des documents de la "
            "période."
        ),
    )


@router.get("/invoices", response_model=InvoiceListRead)
def list_invoices(
    principal: TenantPrincipal = BILLING_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> InvoiceListRead:
    organization = _organization_of(principal)
    invoices = billing.list_invoices(db, organization_id=organization.id)
    return InvoiceListRead(
        invoices=[
            InvoiceRead(
                provider=invoice.provider,
                provider_invoice_id=invoice.provider_invoice_id,
                amount_cents=invoice.amount_cents,
                currency=invoice.currency,
                status=invoice.status,
                plan_code=invoice.plan_code,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                issued_at=invoice.issued_at,
                hosted_url=invoice.hosted_url,
            )
            for invoice in invoices
        ],
        provider=settings.billing_provider,
        note=(
            "Ces factures sont celles émises par le prestataire de paiement et recopiées ici "
            "lors de leur événement. Le produit n'émet aucune facture fiscale."
        ),
    )


def _provider_or_409() -> provider_module.PaymentProvider:
    provider = provider_module.provider_for(settings)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "selling_disabled",
                "provider": settings.billing_provider,
                "message": (
                    "La souscription est fermée sur cette installation "
                    f"(prestataire de paiement « {settings.billing_provider} »). "
                    "Aucun paiement ne peut être encaissé, donc aucune souscription n'est ouverte."
                ),
            },
        )
    if not provider.is_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "provider_not_configured",
                "provider": settings.billing_provider,
                "message": "Le prestataire de paiement est déclaré mais incomplet : voir /api/v1/billing/health.",
            },
        )
    return provider


@router.post("/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
def create_checkout(
    body: CheckoutRequestBody,
    principal: TenantPrincipal = BILLING_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> CheckoutResponse:
    """Ouvre une session de paiement. Aucun accès n'est accordé avant son événement."""

    plan = catalogue.plan_or_none(body.plan_code)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_plan", "message": f"Offre inconnue : {body.plan_code!r}."},
        )
    provider = _provider_or_409()
    organization = _organization_of(principal)
    price_id = None
    if isinstance(provider, provider_module.StripeProvider):
        price_id = provider.price_id_for(plan.code)
    result = provider.create_checkout(
        provider_module.CheckoutRequest(
            organization=organization,
            plan_code=plan.code,
            price_id=price_id,
            success_url=f"{settings.frontend_url.rstrip('/')}/app?billing=success",
            cancel_url=f"{settings.frontend_url.rstrip('/')}/pricing?billing=cancelled",
            customer_email=principal.current.user.email,
        )
    )
    billing.create_checkout_session(
        db,
        organization=organization,
        plan=plan,
        provider_code=provider.code,
        checkout_url=result.url,
        provider_session_id=result.provider_session_id,
        ttl_seconds=settings.billing_checkout_ttl_seconds,
        actor_user_id=principal.user_id,
    )
    return CheckoutResponse(
        session_id=result.provider_session_id,
        url=result.url,
        provider=provider.code,
        expires_at=result.expires_at,
        money_collected=settings.billing_collects_money,
        state_synced=False,
        detail=(
            "Session de paiement créée. L'abonnement ne sera actif qu'après l'événement "
            "signé du prestataire : cette réponse n'accorde aucun accès."
            if settings.billing_collects_money
            else "Session créée avec le prestataire de développement : aucun débit réel n'aura lieu."
        ),
    )


def _apply_envelope(
    db: Session, *, provider_code: str, envelope: provider_module.ProviderEnvelope
) -> tuple[BillingProviderEvent, billing.ApplyOutcome, bool]:
    """Journalise puis applique un événement produit par un prestataire."""

    existing = db.scalar(
        select(BillingProviderEvent).where(
            BillingProviderEvent.provider == provider_code,
            BillingProviderEvent.provider_event_id == envelope.provider_event_id,
        )
    )
    if existing is not None:
        return (
            existing,
            billing.ApplyOutcome(
                bool(existing.applied), existing.apply_result or "déjà appliqué", existing.organization_id
            ),
            True,
        )
    event = BillingProviderEvent(
        organization_id=None,
        provider=provider_code,
        provider_event_id=envelope.provider_event_id,
        event_type=envelope.event_type,
        payload_json=envelope.payload,
        signature_verified=True,
        applied=False,
    )
    db.add(event)
    db.flush()
    outcome = billing.apply_provider_event(db, event=event)
    event.applied = outcome.applied
    event.apply_result = outcome.detail[:255]
    if outcome.organization_id is not None:
        event.organization_id = outcome.organization_id
    db.flush()
    return event, outcome, False


def _ingest_webhook(
    db: Session, *, provider_code: str, body: bytes, headers: Mapping[str, str]
) -> WebhookAck:
    provider = provider_module.provider_by_code(settings, provider_code)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "unknown_provider",
                "message": (
                    f"Aucun prestataire « {provider_code} » n'est actif sur cette installation "
                    f"(prestataire configuré : {settings.billing_provider})."
                ),
            },
        )
    try:
        envelope = provider.verify_event(body=body, headers=headers)
    except provider_module.ProviderIgnoredEvent as exc:
        logger.info("Événement prestataire hors périmètre", extra={"provider": provider_code})
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={"code": "ignored_event", "message": str(exc)},
        ) from exc
    except provider_module.ProviderSignatureError as exc:
        logger.warning(
            "Signature d'événement refusée",
            extra={"event": "billing_webhook_signature_rejected", "provider": provider_code},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_signature", "message": str(exc)},
        ) from exc
    except provider_module.ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "unreadable_event", "message": str(exc)},
        ) from exc

    event, outcome, duplicate = _apply_envelope(db, provider_code=provider_code, envelope=envelope)
    return WebhookAck(
        provider=provider_code,
        event_id=event.provider_event_id,
        event_type=event.event_type,
        applied=outcome.applied,
        duplicate=duplicate,
        detail=outcome.detail,
    )


@router.post("/webhook/{provider_code}", response_model=WebhookAck)
async def provider_webhook(provider_code: str, request: Request, db: Session = DATABASE_DEPENDENCY) -> WebhookAck:
    """Point d'entrée des prestataires. Sans session, mais **avec** signature."""

    body = await request.body()
    return _ingest_webhook(db, provider_code=provider_code, body=body, headers=dict(request.headers))


@router.post("/subscription/plan", response_model=PlanChangeResponse)
def change_plan(
    body: PlanChangeRequestBody,
    principal: TenantPrincipal = BILLING_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> PlanChangeResponse:
    """Demande un changement d'offre ; l'effet dépend du prestataire, pas du souhait."""

    plan = catalogue.plan_or_none(body.plan_code)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_plan", "message": f"Offre inconnue : {body.plan_code!r}."},
        )
    organization = _organization_of(principal)
    entitlement = billing.resolve_entitlement(db, organization=organization)
    subscription = entitlement.subscription
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "no_subscription",
                "message": (
                    "Aucun abonnement en cours : passez par POST /api/v1/billing/checkout "
                    "pour ouvrir une période payée."
                ),
            },
        )
    if not entitlement.granted:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "subscription_inactive",
                "status": entitlement.status,
                "message": entitlement.reason,
            },
        )
    try:
        request_change = billing.plan_change_request(
            current_plan_code=subscription.plan_code, target_plan_code=plan.code
        )
    except BillingStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_change", "message": str(exc)},
        ) from exc

    # C13 — l'offre **de départ** est relevée ici, avant toute application.
    #
    # Défaut mesuré par la preuve HTTP : `current_plan_code` était lu sur la ligne
    # d'abonnement **après** l'application de l'événement prestataire, donc après que la
    # montée d'offre a changé le plan. La réponse annonçait alors « offre précédente : pro »
    # à une organisation qui venait de passer de Starter à Pro — le champ disait le
    # contraire de ce qu'il nomme, au moment précis où un client vérifie ce qu'il vient
    # d'acheter.
    previous_plan_code = subscription.plan_code

    provider = _provider_or_409()
    if subscription.provider_subscription_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "no_provider_subscription",
                "message": "L'abonnement local n'a pas d'identifiant prestataire : impossible de le modifier.",
            },
        )
    price_id = (
        provider.price_id_for(plan.code) if isinstance(provider, provider_module.StripeProvider) else None
    )
    outcome = provider.change_plan(
        provider_subscription_id=subscription.provider_subscription_id,
        plan_code=plan.code,
        price_id=price_id,
        effective=request_change.effective,
    )
    applied = False
    if outcome.envelope is not None:
        _, apply_outcome, _ = _apply_envelope(
            db, provider_code=provider.code, envelope=outcome.envelope
        )
        applied = apply_outcome.applied
    if request_change.effective == "period_end":
        billing.schedule_pending_plan(
            db,
            subscription=subscription,
            plan_code=plan.code,
            effective_at=entitlement.current_period_end,
        )
        applied = False
    db.flush()
    return PlanChangeResponse(
        requested_plan_code=plan.code,
        current_plan_code=previous_plan_code,
        effective=request_change.effective,
        applied=applied,
        detail=f"{request_change.note} {outcome.detail}".strip(),
        state_synced=outcome.state_synced and applied,
        subscription=_subscription_read(principal, db),
    )


@router.post("/subscription/cancel", response_model=CancellationResponse)
def cancel_subscription(
    body: CancellationRequestBody,
    principal: TenantPrincipal = BILLING_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> CancellationResponse:
    organization = _organization_of(principal)
    entitlement = billing.resolve_entitlement(db, organization=organization)
    subscription = entitlement.subscription
    if subscription is None or subscription.provider_subscription_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "no_subscription",
                "message": "Aucun abonnement payant à résilier depuis cette organisation.",
            },
        )
    provider = _provider_or_409()
    outcome = provider.cancel(
        provider_subscription_id=subscription.provider_subscription_id,
        at_period_end=body.at_period_end,
    )
    applied = False
    if outcome.envelope is not None:
        _, apply_outcome, _ = _apply_envelope(db, provider_code=provider.code, envelope=outcome.envelope)
        applied = apply_outcome.applied
    db.flush()
    return CancellationResponse(
        applied=applied,
        detail=outcome.detail,
        state_synced=outcome.state_synced and applied,
        subscription=_subscription_read(principal, db),
    )


@router.post("/subscription/resume", response_model=CancellationResponse)
def resume_subscription(
    principal: TenantPrincipal = BILLING_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> CancellationResponse:
    """Annule une résiliation déjà programmée, tant que la période court."""

    organization = _organization_of(principal)
    entitlement = billing.resolve_entitlement(db, organization=organization)
    subscription = entitlement.subscription
    if subscription is None or subscription.provider_subscription_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_subscription", "message": "Aucun abonnement à reprendre."},
        )
    if not entitlement.cancel_at_period_end:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "no_pending_cancellation",
                "message": "Aucune résiliation n'est programmée sur cet abonnement.",
            },
        )
    provider = _provider_or_409()
    outcome = provider.resume(provider_subscription_id=subscription.provider_subscription_id)
    applied = False
    if outcome.envelope is not None:
        _, apply_outcome, _ = _apply_envelope(db, provider_code=provider.code, envelope=outcome.envelope)
        applied = apply_outcome.applied
    db.flush()
    return CancellationResponse(
        applied=applied,
        detail=outcome.detail,
        state_synced=outcome.state_synced and applied,
        subscription=_subscription_read(principal, db),
    )


@local_dev_router.post("/local/checkout/{session_id}/pay", response_model=LocalPaymentResponse)
def simulate_local_payment(
    session_id: str,
    body: LocalPaymentRequestBody,
    principal: TenantPrincipal = BILLING_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> LocalPaymentResponse:
    """Paiement **simulé** du prestataire de développement. Aucun débit réel.

    Cette route n'existe que lorsque le prestataire configuré est ``local`` et que
    l'environnement n'est pas de production (voir ``app.main``). Elle fabrique
    l'événement avec le même signataire que le prestataire, puis le fait passer
    par **le même chemin de webhook** : signature vérifiée, journalisation,
    application unique. C'est ce qui rend le cycle complet démontrable sans
    prétendre qu'un paiement a eu lieu.
    """

    organization = _organization_of(principal)
    session_row = db.scalar(
        select(BillingCheckoutSession).where(
            BillingCheckoutSession.provider_session_id == session_id,
            BillingCheckoutSession.organization_id == organization.id,
        )
    )
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "unknown_session", "message": "Session de paiement inconnue."},
        )
    if session_row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "session_not_pending",
                "message": f"Cette session est déjà {session_row.status} : rien n'a été rejoué.",
            },
        )
    plan = catalogue.plan_or_none(body.plan_code or session_row.plan_code)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_plan", "message": "Offre inconnue pour cette session."},
        )
    provider = provider_module.provider_by_code(settings, "local")
    if not isinstance(provider, provider_module.LocalProvider):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "unknown_provider", "message": "Prestataire local inactif."},
        )
    now = _utcnow()
    period_end = _add_one_month(now)
    envelope = provider.subscription_activated(
        organization_id=organization.id,
        plan_code=plan.code,
        provider_session_id=session_row.provider_session_id,
        provider_subscription_id=f"sub_local_{organization.id.hex[:12]}",
        period_start=now,
        period_end=period_end,
        trial_end=None,
    )
    ack = _ingest_webhook(
        db,
        provider_code="local",
        body=envelope.body,
        headers=envelope.headers,
    )
    return LocalPaymentResponse(
        simulated=True,
        money_collected=False,
        detail=(
            "Paiement simulé par le prestataire de développement : aucun euro n'a été encaissé, "
            "aucune carte n'a été demandée. L'état d'abonnement a été mis à jour par l'événement "
            "signé, exactement comme le ferait un prestataire réel."
        ),
        webhook=ack,
        subscription=_subscription_read(principal, db),
    )


def _add_one_month(moment: datetime) -> datetime:
    from app.billing.usage import add_months

    return add_months(moment, 1)


__all__ = ["local_dev_router", "router"]
