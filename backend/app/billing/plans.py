"""C13 — catalogue des plans, de leurs quotas et de leurs droits.

Le catalogue est en Python et versionné avec le code, pas en base. Un plan est
une offre commerciale : si la limite vivait en base, deux organisations payant le
même prix pourraient se voir appliquer deux limites différentes sans que
personne ne puisse dire laquelle est la bonne. Les tests et les preuves lisent
donc exactement ce que le produit applique.

Trois avertissements, volontairement dans le code et pas seulement dans un
document :

* les **prix sont provisoires** (``PRICING_CONFIRMED = False``) et l'API les
  publie comme tels : ils n'ont été validés par personne ;
* les quotas de l'offre « enterprise » sont des **plafonds de référence**, pas
  des plafonds négociés ; aucun ajustement par organisation n'existe dans ce code ;
* la **rétention est un plafond appliqué**, pas seulement un argument commercial :
  elle borne la durée réellement appliquée par le plan de purge
  (voir :func:`app.billing.enforcement.effective_retention_months`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Metric(str, Enum):
    """Ce qui est compté, et rien de plus.

    ``DOCUMENTS`` et ``OCR_PAGES`` sont des compteurs par période. ``SEATS`` est
    une jauge instantanée (des sièges ne « se consomment » pas). ``RETENTION``
    n'est pas une quantité mais une fenêtre en mois.
    """

    DOCUMENTS = "documents"
    OCR_PAGES = "ocr_pages"
    SEATS = "seats"
    RETENTION = "retention"


COUNTED_METRICS: Final[tuple[Metric, ...]] = (Metric.DOCUMENTS, Metric.OCR_PAGES)
GAUGE_METRICS: Final[tuple[Metric, ...]] = (Metric.SEATS,)
WINDOW_METRICS: Final[tuple[Metric, ...]] = (Metric.RETENTION,)

# Libellés publiés tels quels dans les erreurs et dans le portail. Une erreur de
# quota qui dit « limite atteinte » sans dire laquelle oblige le client à appeler
# le support : c'est exactement ce que le chantier doit éviter.
METRIC_LABELS: Final[dict[Metric, str]] = {
    Metric.DOCUMENTS: "documents importés ce mois-ci",
    Metric.OCR_PAGES: "pages analysées ce mois-ci",  # C21 : comptées qu'une OCR ait été nécessaire ou non

    Metric.SEATS: "sièges actifs",
    Metric.RETENTION: "durée de conservation couverte par le plan",
}


@dataclass(frozen=True)
class Entitlement:
    """Un droit binaire du plan, appliqué par du code (jamais décoratif)."""

    code: str
    label: str
    enforced_by: str


# Seuls figurent ici les droits réellement appliqués quelque part dans le code.
# Une fonctionnalité absente du produit n'a rien à faire dans une grille tarifaire.
ENTITLEMENTS: Final[tuple[Entitlement, ...]] = (
    Entitlement(
        code="api_keys",
        label="Clés d'API et intégrations machine à machine",
        enforced_by="/api/v1/enterprise/api-keys (POST ; 402 si l'offre ne l'inclut pas)",
    ),
)


@dataclass(frozen=True)
class PlanQuotas:
    documents_per_month: int
    ocr_pages_per_month: int
    seats: int
    retention_months: int

    def limit_for(self, metric: Metric) -> int:
        if metric is Metric.DOCUMENTS:
            return self.documents_per_month
        if metric is Metric.OCR_PAGES:
            return self.ocr_pages_per_month
        if metric is Metric.SEATS:
            return self.seats
        if metric is Metric.RETENTION:
            return self.retention_months
        raise ValueError(f"Métrique inconnue sans limite : {metric!r}")


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    tagline: str
    # ``None`` signifie « sur devis » : le produit ne publie alors aucun prix.
    price_cents_per_month_excl_vat: int | None
    quotas: PlanQuotas
    entitlements: frozenset[str]
    # Les plafonds de l'offre « enterprise » sont des valeurs de référence, pas
    # des valeurs contractuelles : le portail doit le dire.
    limits_are_reference_values: bool = False

    def price_label(self) -> str:
        if self.price_cents_per_month_excl_vat is None:
            return "sur devis"
        euros = self.price_cents_per_month_excl_vat / 100
        formatted = f"{euros:,.2f}".replace(",", " ").replace(".", ",")
        return f"{formatted} € HT / mois"


STARTER = Plan(
    code="starter",
    name="Starter",
    tagline="Pour tester la conformité de vos premières publicités.",
    price_cents_per_month_excl_vat=4900,
    quotas=PlanQuotas(
        documents_per_month=30,
        ocr_pages_per_month=300,
        seats=2,
        retention_months=12,
    ),
    entitlements=frozenset(),
)

PRO = Plan(
    code="pro",
    name="Pro",
    tagline="Pour une équipe marketing et achats qui publie chaque semaine.",
    price_cents_per_month_excl_vat=19900,
    quotas=PlanQuotas(
        documents_per_month=300,
        ocr_pages_per_month=3_000,
        seats=10,
        retention_months=36,
    ),
    entitlements=frozenset({"api_keys"}),
)

ENTERPRISE = Plan(
    code="enterprise",
    name="Enterprise",
    tagline="Pour les groupes : volume élevé, conservation longue, intégrations.",
    price_cents_per_month_excl_vat=None,
    quotas=PlanQuotas(
        documents_per_month=3_000,
        ocr_pages_per_month=30_000,
        seats=100,
        retention_months=120,
    ),
    entitlements=frozenset({"api_keys"}),
    limits_are_reference_values=True,
)

PLANS: Final[tuple[Plan, ...]] = (STARTER, PRO, ENTERPRISE)
PLAN_BY_CODE: Final[dict[str, Plan]] = {plan.code: plan for plan in PLANS}

# Le catalogue est daté : un changement de quota doit pouvoir être relié au jour
# où il a pris effet, sinon une facture contestée six mois plus tard est
# indéfendable.
CATALOGUE_VERSION: Final[str] = "2026-09-30"

# ``False`` tant qu'un humain n'a pas validé les prix. L'API le publie et la page
# publique l'affiche : présenter un prix provisoire comme définitif serait une
# allégation commerciale non fondée.
PRICING_CONFIRMED: Final[bool] = False

PRICING_STATUS_VERIFIED: Final[str] = "validés"
PRICING_STATUS_PROVISIONAL: Final[str] = (
    "provisoires — valeurs de catalogue non validées par la direction commerciale"
)

# Toute organisation démarre un essai du plan Pro. L'essai n'est pas une ligne en
# base : il se calcule à partir de ``organizations.created_at``
# (voir :mod:`app.billing.subscriptions`). Conséquence voulue : un essai ne peut
# pas être « relancé » par un appel d'API, seulement par la création d'une
# nouvelle organisation.
DEFAULT_TRIAL_PLAN_CODE: Final[str] = "pro"
TRIAL_DAYS: Final[int] = 14

# Le plan appliqué à une organisation qui n'a ni abonnement ni essai en cours
# n'existe pas : la résolution retourne alors « aucune offre » et l'accès aux
# actions payantes est refusé. Voir ``subscriptions.resolve_entitlement``.
NO_PLAN_CODE: Final[str] = "none"


def pricing_status() -> str:
    return PRICING_STATUS_VERIFIED if PRICING_CONFIRMED else PRICING_STATUS_PROVISIONAL


def plan_or_none(code: str | None) -> Plan | None:
    if code is None:
        return None
    return PLAN_BY_CODE.get(code)


def plan_for_code_or_raise(code: str) -> Plan:
    plan = PLAN_BY_CODE.get(code)
    if plan is None:
        raise KeyError(f"Plan inconnu : {code!r}")
    return plan


def upgrade_paths(code: str) -> tuple[Plan, ...]:
    """Plans strictement supérieurs, du moins cher au plus cher.

    Un plan « sur devis » n'a pas de prix comparable : il est placé après tous
    les plans à prix publié.
    """

    def sort_key(plan: Plan) -> tuple[int, int]:
        if plan.price_cents_per_month_excl_vat is None:
            return (1, 0)
        return (0, plan.price_cents_per_month_excl_vat)

    ordered = sorted(PLANS, key=sort_key)
    current_index = next((index for index, plan in enumerate(ordered) if plan.code == code), None)
    if current_index is None:
        return tuple(ordered)
    return tuple(ordered[current_index + 1 :])


def is_upgrade(*, from_code: str, to_code: str) -> bool:
    return any(plan.code == to_code for plan in upgrade_paths(from_code))


def entitlement_codes(plan: Plan | None) -> frozenset[str]:
    return frozenset() if plan is None else plan.entitlements


def entitlement_is_included(plan: Plan | None, code: str) -> bool:
    return code in entitlement_codes(plan)
