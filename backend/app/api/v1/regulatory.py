"""Authenticated API endpoints for Regulatory Governance and Rule Book Repository (Chantier 7)."""

from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.engine.governance import (
    get_rule_detail,
    get_rulebook_changelog,
    get_rulebook_summary,
    list_rules,
)
from app.identity.dependencies import (
    TenantPrincipal,
    require_permission,
)
from app.models.regulatory_schemas import (
    ConfidenceLevel,
    Jurisdiction,
    LegalStatus,
    RegulatoryRuleDetail,
    RegulatoryRuleSummary,
    RuleBookChangelogEntry,
    RuleBookSummaryResponse,
    RuleReviewSubmissionRequest,
)

router = APIRouter(prefix="/api/v1/regulatory", tags=["regulatory-governance"])

RULES_READ_DEPENDENCY = Depends(require_permission("rules:read"))
RULES_MANAGE_DEPENDENCY = Depends(require_permission("rules:manage", csrf_protected=True))


@router.get("/rulebook", response_model=RuleBookSummaryResponse)
def get_rulebook_overview(
    principal: TenantPrincipal = RULES_READ_DEPENDENCY,
) -> RuleBookSummaryResponse:
    """Returns metadata, version hash, jurisdiction breakdown, and governance statistics of the active Rule Book."""
    return get_rulebook_summary()


@router.get("/rules", response_model=list[RegulatoryRuleSummary])
def list_regulatory_rules(
    jurisdiction: Jurisdiction | None = Query(default=None, description="Filtrer par juridiction (FR, EU, INTERNATIONAL)"),
    legal_status: LegalStatus | None = Query(default=None, description="Filtrer par statut juridique"),
    legal_force: str | None = Query(default=None, description="Filtrer par force juridique"),
    severity: str | None = Query(default=None, description="Filtrer par niveau de sévérité"),
    claim_type: str | None = Query(default=None, description="Filtrer par type d'allégation"),
    confidence_level: ConfidenceLevel | None = Query(default=None, description="Filtrer par niveau de confiance"),
    search: str | None = Query(default=None, description="Recherche textuelle dans les titres et références"),
    principal: TenantPrincipal = RULES_READ_DEPENDENCY,
) -> list[RegulatoryRuleSummary]:
    """Returns the list of versioned regulatory rules with optional filters."""
    return list_rules(
        jurisdiction=jurisdiction,
        legal_status=legal_status,
        legal_force=legal_force,
        severity=severity,
        claim_type=claim_type,
        confidence_level=confidence_level,
        search=search,
    )


@router.get("/rules/{rule_id}", response_model=RegulatoryRuleDetail)
def get_regulatory_rule(
    rule_id: str,
    principal: TenantPrincipal = RULES_READ_DEPENDENCY,
) -> RegulatoryRuleDetail:
    """Returns the full specification of a regulatory rule, including exact legal citations and source links."""
    rule = get_rule_detail(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Règle réglementaire '{rule_id}' introuvable dans le Rule Book actif.",
        )
    return rule


@router.get("/changelog", response_model=list[RuleBookChangelogEntry])
def get_changelog(
    principal: TenantPrincipal = RULES_READ_DEPENDENCY,
) -> list[RuleBookChangelogEntry]:
    """Returns the version history and field-level changelog of the Rule Book."""
    return get_rulebook_changelog()


@router.post("/rules/{rule_id}/review", response_model=RegulatoryRuleDetail)
def record_rule_review(
    rule_id: str,
    body: RuleReviewSubmissionRequest,
    request: Request,
    principal: TenantPrincipal = RULES_MANAGE_DEPENDENCY,
) -> RegulatoryRuleDetail:
    """Fail closed until reviews are durable, tenant-scoped and legally evidenced.

    The previous implementation mutated a process-global dictionary: one
    tenant's administrator could change every tenant's rule metadata, with no
    durable audit record and no effect after restart. Do not replace that with
    another implicit global approval.
    """
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "La validation juridique nécessite un workflow persisté, isolé par organisation et appuyé "
            "sur des preuves vérifiées. Aucune approbation globale n'a été appliquée."
        ),
    )
