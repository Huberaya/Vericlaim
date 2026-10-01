"""C13 — la porte HTTP du module : dépendance de garde et traduction des refus.

Une seule dépendance, :func:`require_active_subscription`, porte le contrôle du
statut d'abonnement. Elle est marquée (`vericlaim_billing_gate`) pour qu'un test
structurel puisse vérifier, sur l'application réellement montée, que **toutes**
les routes payantes la portent — une garde écrite et jamais branchée est un
décor, et c'est exactement le défaut trouvé au chantier C24.

Les routes de lecture ne la portent pas : un client en défaut de paiement garde
l'accès à ce qu'il a déjà produit. Les routes de vie privée (`/api/v1/privacy/*`)
ne la portent **jamais** : un droit à l'effacement ou à la portabilité ne se
conditionne pas au paiement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.billing.enforcement import (
    QuotaExceededError,
    SubscriptionInactiveError,
    entitlement_for,
    load_organization,
)
from app.billing.subscriptions import resolve_entitlement
from app.core.database import get_db
from app.identity.dependencies import TenantPrincipal, get_tenant_principal

GATE_MARKER = "vericlaim_billing_gate"

# Préfixes surveillés : toute route non-GET sous ces préfixes doit porter la
# garde, sauf exemption écrite et motivée ci-dessous.
SENSITIVE_WRITE_PREFIXES: tuple[str, ...] = (
    "/api/v1/documents",
    "/api/v1/analyses",
    "/api/v1/evidence",
    "/api/v1/reports",
    "/api/v1/claims",
    "/api/v1/validations",
    "/api/v1/enterprise/api-keys",
    "/api/v1/pilot/import-catalog",
    "/api/v1/organizations/current/members/invitations",
    "/api/v1/enterprise/legal-holds",
    "/api/v1/enterprise/scim/v2/Users",
)

# Exemptions : chacune a une raison qui n'est pas « c'était plus simple ».
EXEMPT_WRITE_ROUTES: tuple[tuple[str, str, str], ...] = (
    (
        "PUT",
        "/api/v1/pilot/retention-policy",
        "Déclarer sa politique de conservation n'est pas une consommation payante ; "
        "l'effet du plan est publié par /api/v1/privacy/retention.",
    ),
    (
        "PATCH",
        "/api/v1/evidence/{evidence_id}",
        "Corriger une preuve déjà enregistrée est un acte de tenue de dossier, pas une nouvelle "
        "consommation : un client en défaut de paiement doit pouvoir rectifier ses données.",
    ),
    (
        "DELETE",
        "/api/v1/evidence/{evidence_id}",
        "Retirer une preuve déjà enregistrée réduit la consommation ; le bloquer n'aurait pas de sens.",
    ),
    (
        "PATCH",
        "/api/v1/evidence-links/{link_id}",
        "Modifier un rapprochement allégation↔preuve existant ne consomme rien.",
    ),
    (
        "DELETE",
        "/api/v1/evidence-links/{link_id}",
        "Défaire un rapprochement ne consomme rien.",
    ),
    (
        "PATCH",
        "/api/v1/evidence-requests/{request_id}",
        "Corriger une demande fournisseur existante ne consomme rien.",
    ),
    (
        "DELETE",
        "/api/v1/evidence-requests/{request_id}",
        "Annuler une demande fournisseur arrête une dépense, il serait absurde de le refuser.",
    ),
    (
        "DELETE",
        "/api/v1/enterprise/api-keys/{key_id}",
        "Révoquer une clé compromise doit toujours être possible, même en défaut de paiement.",
    ),
    (
        "POST",
        "/api/v1/enterprise/legal-holds",
        "Un gel légal répond à une obligation (litige, enquête) : il ne peut pas dépendre d'un paiement.",
    ),
    (
        "POST",
        "/api/v1/enterprise/scim/v2/Users",
        "Provisionnement d'identité piloté par l'annuaire du client, pas un achat à l'unité.",
    ),
)


def require_active_subscription() -> Callable[..., TenantPrincipal]:
    """Refuse une action payante quand l'abonnement ne la couvre pas."""

    def dependency(
        principal: TenantPrincipal = Depends(get_tenant_principal),
        db: Session = Depends(get_db),
    ) -> TenantPrincipal:
        entitlement = resolve_entitlement(
            db, organization=principal.membership_view.organization
        )
        if not entitlement.granted:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=SubscriptionInactiveError(entitlement).detail,
            )
        return principal

    setattr(dependency, GATE_MARKER, True)
    dependency.__name__ = "require_active_subscription"
    return dependency


def route_has_billing_gate(route: object) -> bool:
    """Vrai si la route dépend de la garde d'abonnement (dépendances incluses)."""

    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    seen: set[int] = set()
    stack = [dependant]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        call = getattr(current, "call", None)
        if call is not None and getattr(call, GATE_MARKER, False):
            return True
        stack.extend(getattr(current, "dependencies", []) or [])
    return False


def billing_http_exception(exc: Exception) -> HTTPException:
    """Traduit un refus de facturation en réponse HTTP explicite."""

    if isinstance(exc, QuotaExceededError):
        return HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=exc.detail)
    if isinstance(exc, SubscriptionInactiveError):
        return HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=exc.detail)
    raise TypeError(f"Refus de facturation inattendu : {type(exc).__name__}")


def subscription_status(
    db: Session, *, organization_id, now: datetime | None = None
) -> dict[str, object]:
    """Vue publique de l'état d'abonnement (utilisée par le portail et l'export)."""

    organization = load_organization(db, organization_id=organization_id)
    entitlement = entitlement_for(db, organization_id=organization_id, now=now)
    return {
        "plan_code": entitlement.plan_code,
        "status": entitlement.status,
        "granted": entitlement.granted,
        "reason": entitlement.reason,
        "provider": entitlement.provider,
        "trial_ends_at": entitlement.trial_ends_at,
        "trial_derived_from_organization_creation": entitlement.trial_derived,
        "current_period_start": entitlement.current_period_start,
        "current_period_end": entitlement.current_period_end,
        "cancel_at_period_end": entitlement.cancel_at_period_end,
        "pending_plan_code": entitlement.pending_plan_code,
        "pending_plan_effective_at": entitlement.pending_plan_effective_at,
        "organization_created_at": organization.created_at,
    }


__all__ = [
    "EXEMPT_WRITE_ROUTES",
    "GATE_MARKER",
    "SENSITIVE_WRITE_PREFIXES",
    "billing_http_exception",
    "require_active_subscription",
    "route_has_billing_gate",
    "subscription_status",
]
