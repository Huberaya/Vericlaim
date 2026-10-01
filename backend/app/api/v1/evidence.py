"""Authenticated API routes for persistent evidence registry and claim linking."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.http import require_active_subscription
from app.analyses.service import (
    AnalysisNotFoundError,
    get_analysis,
    get_claim,
)
from app.api.v1.analyses import PRE_AUDIT_DISCLAIMER, _present_claim, _segment_map
from app.core.database import get_db
from app.analyses.service import get_analysis_version_by_number
from app.evidence.coverage import (
    ACCEPTED_EVIDENCE_TYPES,
    STATE_MEANINGS,
    ClaimCoverage,
    coverage_for_analysis_version,
)
from app.models.evidence_schemas import (
    AnalysisCoverageResponse,
    ClaimCoverageResponse,
    CoverageFindingResponse,
    CoverageSummaryResponse,
    EvidenceCheckResponse,
    EvidenceSuggestionResponse,
)
from app.models.schemas import paris_today
from app.evidence.service import (
    attach_observed_states,
    EvidenceConflictError,
    EvidenceInputError,
    EvidenceNotFoundError,
    create_evidence,
    delete_evidence,
    get_analysis_evidence_matrix,
    get_evidence,
    link_claim_evidence,
    list_claim_evidence_links,
    list_evidence,
    unlink_claim_evidence,
    update_claim_evidence_link,
    update_evidence,
)
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.domain import (
    AnalysisVersion,
    Evidence,
    EvidenceLink,
    EvidenceStatus,
    EvidenceType,
)
from app.models.evidence_schemas import (
    ClaimWithEvidenceLinksResponse,
    EvidenceCreateRequest,
    EvidenceCreateResponse,
    EvidenceLinkCreateRequest,
    EvidenceLinkResponse,
    EvidenceLinkUpdateRequest,
    EvidenceListResponse,
    EvidenceMatrixResponse,
    EvidenceResponse,
    EvidenceUpdateRequest,
)


router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])

# C13 — garde d'abonnement : cette route produit un artefact payant ou une
# dépense réelle (OCR, e-mail tiers, rapport signé). Voir app/billing/http.py.
SUBSCRIPTION_GATE = Depends(require_active_subscription())
evidence_links_router = APIRouter(prefix="/api/v1/evidence-links", tags=["evidence-links"])
claims_evidence_router = APIRouter(prefix="/api/v1/claims", tags=["claims-evidence"])
analyses_evidence_router = APIRouter(prefix="/api/v1/analyses", tags=["analyses-evidence"])

DATABASE_DEPENDENCY = Depends(get_db)
EVIDENCE_MANAGE_DEPENDENCY = Depends(require_permission("evidence:manage", csrf_protected=True))
EVIDENCE_READ_DEPENDENCY = Depends(require_permission("evidence:read"))
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))


def _present_evidence(evidence: Evidence) -> EvidenceResponse:
    return EvidenceResponse(
        id=evidence.id,
        evidence_type=evidence.evidence_type,
        status=evidence.status,
        reference=evidence.reference,
        issuer=evidence.issuer,
        issued_on=evidence.issued_on,
        expires_on=evidence.expires_on,
        product_scope=evidence.product_scope,
        supplier_id=evidence.supplier_id,
        product_id=evidence.product_id,
        document_version_id=evidence.document_version_id,
        certificate_id=evidence.certificate_id,
        evidence_metadata=dict(evidence.evidence_metadata_json or {}),
        verified_at=evidence.verified_at,
        verified_by_user_id=evidence.verified_by_user_id,
        created_at=evidence.created_at,
        updated_at=evidence.updated_at,
    )


def _present_evidence_link(link: EvidenceLink, *, include_evidence: bool = True) -> EvidenceLinkResponse:
    ev_resp = _present_evidence(link.evidence) if (include_evidence and link.evidence is not None) else None
    return EvidenceLinkResponse(
        id=link.id,
        claim_id=link.claim_id,
        evidence_id=link.evidence_id,
        relation=link.relation,
        coverage_status=link.coverage_status,
        observed_state=getattr(link, "observed_state_value", None),
        validity_as_of=link.validity_as_of,
        confidence_score=link.confidence_score,
        rationale=link.rationale,
        reviewed_by_user_id=link.reviewed_by_user_id,
        reviewed_at=link.reviewed_at,
        created_at=link.created_at,
        evidence=ev_resp,
    )


def _raise_evidence_error(exc: Exception) -> None:
    if isinstance(exc, EvidenceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, EvidenceConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, EvidenceInputError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    raise exc


@router.post("", response_model=EvidenceCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[SUBSCRIPTION_GATE])
def create_new_evidence(
    body: EvidenceCreateRequest,
    request: Request,
    principal: TenantPrincipal = EVIDENCE_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceCreateResponse:
    try:
        evidence = create_evidence(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            evidence_type=body.evidence_type,
            reference=body.reference,
            issuer=body.issuer,
            issued_on=body.issued_on,
            expires_on=body.expires_on,
            product_scope=body.product_scope,
            supplier_id=body.supplier_id,
            product_id=body.product_id,
            document_version_id=body.document_version_id,
            certificate_id=body.certificate_id,
            status=body.status,
            metadata=body.evidence_metadata,
            request_id=request_id_from_request(request),
        )
        return EvidenceCreateResponse(
            evidence=_present_evidence(evidence),
            idempotent_replay=False,
        )
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@router.get("", response_model=EvidenceListResponse)
def list_organization_evidence(
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    evidence_type: EvidenceType | None = None,
    status_filter: EvidenceStatus | None = Query(default=None, alias="status"),
    query: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    principal: TenantPrincipal = EVIDENCE_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceListResponse:
    try:
        page = list_evidence(
            db,
            organization_id=principal.organization_id,
            limit=limit,
            cursor=cursor,
            supplier_id=supplier_id,
            product_id=product_id,
            evidence_type=evidence_type,
            status=status_filter,
            query=query,
        )
        return EvidenceListResponse(
            items=[_present_evidence(ev) for ev in page.items],
            next_cursor=page.next_cursor,
        )
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@router.get("/{evidence_id}", response_model=EvidenceResponse)
def get_evidence_detail(
    evidence_id: UUID,
    principal: TenantPrincipal = EVIDENCE_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceResponse:
    try:
        evidence = get_evidence(db, organization_id=principal.organization_id, evidence_id=evidence_id)
        return _present_evidence(evidence)
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@router.patch("/{evidence_id}", response_model=EvidenceResponse)
def update_existing_evidence(
    evidence_id: UUID,
    body: EvidenceUpdateRequest,
    request: Request,
    principal: TenantPrincipal = EVIDENCE_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceResponse:
    try:
        changes = body.model_dump(exclude_unset=True)
        evidence = update_evidence(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            evidence_id=evidence_id,
            changes=changes,
            request_id=request_id_from_request(request),
        )
        return _present_evidence(evidence)
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@router.delete("/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_evidence(
    evidence_id: UUID,
    request: Request,
    principal: TenantPrincipal = EVIDENCE_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> None:
    try:
        delete_evidence(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            evidence_id=evidence_id,
            request_id=request_id_from_request(request),
        )
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


# ---------------------------------------------------------------------------
# Claim Evidence Links
# ---------------------------------------------------------------------------


@claims_evidence_router.post("/{claim_id}/evidence-links", response_model=EvidenceLinkResponse, status_code=status.HTTP_201_CREATED, dependencies=[SUBSCRIPTION_GATE])
def link_claim_to_evidence(
    claim_id: UUID,
    body: EvidenceLinkCreateRequest,
    request: Request,
    principal: TenantPrincipal = EVIDENCE_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceLinkResponse:
    try:
        link = link_claim_evidence(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            claim_id=claim_id,
            evidence_id=body.evidence_id,
            relation=body.relation,
            coverage_status=body.coverage_status,
            validity_as_of=body.validity_as_of,
            confidence_score=body.confidence_score,
            rationale=body.rationale,
            request_id=request_id_from_request(request),
        )
        return _present_evidence_link(link)
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@claims_evidence_router.get("/{claim_id}/evidence-links", response_model=list[EvidenceLinkResponse])
def get_claim_links(
    claim_id: UUID,
    principal: TenantPrincipal = EVIDENCE_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> list[EvidenceLinkResponse]:
    try:
        # Verify claim exists
        get_claim(db, organization_id=principal.organization_id, claim_id=claim_id)
        links = list_claim_evidence_links(db, organization_id=principal.organization_id, claim_id=claim_id)
        # C17 — la déclaration et le constat sont rendus ensemble : un lien sans décision
        # humaine (`pending`) affiche quand même ce que l'examen de la pièce observe.
        attach_observed_states(db, organization_id=principal.organization_id, links=links)
        return [_present_evidence_link(link) for link in links]
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@evidence_links_router.patch("/{link_id}", response_model=EvidenceLinkResponse)
def update_existing_link(
    link_id: UUID,
    body: EvidenceLinkUpdateRequest,
    request: Request,
    principal: TenantPrincipal = EVIDENCE_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceLinkResponse:
    try:
        changes = body.model_dump(exclude_unset=True)
        link = update_claim_evidence_link(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            link_id=link_id,
            changes=changes,
            request_id=request_id_from_request(request),
        )
        return _present_evidence_link(link)
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@evidence_links_router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_claim(
    link_id: UUID,
    request: Request,
    principal: TenantPrincipal = EVIDENCE_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> None:
    try:
        unlink_claim_evidence(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            link_id=link_id,
            request_id=request_id_from_request(request),
        )
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


# ---------------------------------------------------------------------------
# C17 — État de couverture probatoire par allégation
# ---------------------------------------------------------------------------

COVERAGE_DISCLAIMER = (
    "Cet état de couverture est un **constat daté sur des faits déclarés**, pas une "
    "validation juridique. Les dates de validité proviennent des pièces enregistrées par "
    "le client : le produit ne les corrobore pas auprès d'un registre indépendant (aucun "
    "registre n'est configuré). Une preuve jugée « couvrante » signifie qu'elle est datée, "
    "de type pertinent et rattachée au périmètre de l'audit — cela ne prouve pas que son "
    "contenu soit exact ni que l'allégation soit conforme."
)

COVERAGE_RULES: dict[str, str] = {
    "precedence": (
        "L'état retenu d'une allégation est le plus restrictif entre ce que les faits montrent "
        "et ce que le relecteur a déclaré : « " + " < ".join(
            ["missing", "expired", "out_of_scope", "not_covering", "partial", "covered"][::-1]
        ) + " »."
    ),
    "expiry": (
        "Une preuve est expirée si sa date de fin de validité déclarée est antérieure à la date "
        "de l'audit. Cette date n'est pas corroborée par une source indépendante."
    ),
    "validity_unknown": (
        "Pour les certificats, rapports de laboratoire et déclarations environnementales, "
        "l'absence de date de fin de validité interdit de conclure à la validité : l'état est "
        "au mieux « partial »."
    ),
    "scope": (
        "Le périmètre se compare par identifiants (produit, puis fournisseur). Un périmètre "
        "déclaré en texte libre n'est pas comparé : il est restitué, jamais interprété."
    ),
    "type": (
        "Chaque famille d'allégation a une liste publiée de types de preuve recevables "
        "(`/api/v1/analyses/{id}/coverage` les renvoie). Un type hors liste produit "
        "« not_covering », jamais une conclusion favorable."
    ),
    "suggestions": (
        "Les rapprochements proposés sont des suggestions motivées (même produit, même "
        "fournisseur, type pertinent, validité). Ce ne sont pas des preuves : le rattachement "
        "reste une décision humaine."
    ),
}


def _present_finding(finding) -> CoverageFindingResponse:
    return CoverageFindingResponse(
        code=finding.code,
        severity=finding.severity,
        message=finding.message,
        evidence_id=finding.evidence_id,
        facts=dict(finding.facts),
    )


def _present_check(check) -> EvidenceCheckResponse:
    return EvidenceCheckResponse(
        evidence_id=check.evidence_id,
        evidence_type=check.evidence_type,
        validity_state=check.validity_state,
        scope_state=check.scope_state,
        type_state=check.type_state,
        usable_as_of=check.usable,
        findings=[_present_finding(item) for item in check.findings],
    )


def _present_suggestion(suggestion) -> EvidenceSuggestionResponse:
    return EvidenceSuggestionResponse(
        evidence_id=suggestion.evidence_id,
        evidence_type=suggestion.evidence_type,
        reference=suggestion.reference,
        score=suggestion.score,
        reasons=list(suggestion.reasons),
        caveats=list(suggestion.caveats),
        usable_as_of=suggestion.usable_as_of,
    )


def _present_claim_coverage(coverage: ClaimCoverage) -> ClaimCoverageResponse:
    return ClaimCoverageResponse(
        claim_id=coverage.claim.id,
        claim_type=coverage.claim.claim_type,
        category=coverage.claim.category,
        claim_text=coverage.claim.claim_text,
        as_of=coverage.as_of,
        state=coverage.state,
        state_meaning=STATE_MEANINGS[coverage.state],
        declared_state=coverage.declared_state,
        observed_state=coverage.observed_state,
        is_sufficient=coverage.is_sufficient,
        explanation=coverage.explanation,
        checks=[_present_check(check) for check in coverage.checks],
        findings=[_present_finding(item) for item in coverage.findings],
        suggestions=[_present_suggestion(item) for item in coverage.suggestions],
        contradicting_link_ids=list(coverage.contradicting_links),
        linked_evidence_ids=[check.evidence_id for check in coverage.checks],
    )


def _summarise(coverages: list[ClaimCoverage]) -> CoverageSummaryResponse:
    counts: dict[str, int] = {}
    for coverage in coverages:
        counts[coverage.state] = counts.get(coverage.state, 0) + 1
    return CoverageSummaryResponse(
        total_claims=len(coverages),
        covered=counts.get("covered", 0),
        partial=counts.get("partial", 0),
        expired=counts.get("expired", 0),
        out_of_scope=counts.get("out_of_scope", 0),
        not_covering=counts.get("not_covering", 0),
        missing=counts.get("missing", 0),
        claims_with_declared_contradiction=sum(1 for item in coverages if item.contradicting_links),
    )


@analyses_evidence_router.get("/{analysis_id}/coverage", response_model=AnalysisCoverageResponse)
def get_analysis_coverage(
    analysis_id: UUID,
    version: int | None = Query(default=None),
    as_of: date | None = Query(default=None),
    suggestions: bool = Query(default=True),
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> AnalysisCoverageResponse:
    """L'état de couverture probatoire de chaque allégation d'une version d'analyse."""

    try:
        analysis = get_analysis(db, organization_id=principal.organization_id, analysis_id=analysis_id)
        if version is None:
            anl_version = db.scalar(
                select(AnalysisVersion)
                .where(
                    AnalysisVersion.organization_id == principal.organization_id,
                    AnalysisVersion.analysis_id == analysis_id,
                )
                .order_by(AnalysisVersion.version_number.desc())
            )
        else:
            anl_version = get_analysis_version_by_number(
                db,
                organization_id=principal.organization_id,
                analysis_id=analysis_id,
                version_number=version,
            )
        if anl_version is None:
            raise EvidenceNotFoundError("Version d’analyse introuvable.")
        evaluated_on = as_of or paris_today()
        coverages = coverage_for_analysis_version(
            db,
            organization_id=principal.organization_id,
            analysis=analysis,
            version=anl_version,
            as_of=evaluated_on,
            with_suggestions=suggestions,
        )
        rules = dict(COVERAGE_RULES)
        rules["accepted_evidence_types"] = "; ".join(
            f"{claim_type.value} → {', '.join(item.value for item in types)}"
            for claim_type, types in ACCEPTED_EVIDENCE_TYPES.items()
        )
        rules["as_of"] = (
            f"Couverture calculée à la date d'audit {evaluated_on.isoformat()}"
            + (" (fournie par l'appelant)" if as_of else " (date du jour à Paris)")
            + ". Le calcul est reproductible : la même date donne le même résultat."
        )
        return AnalysisCoverageResponse(
            analysis_id=analysis.id,
            analysis_version_id=anl_version.id,
            version_number=anl_version.version_number,
            as_of=evaluated_on,
            summary=_summarise(coverages),
            coverages=[_present_claim_coverage(item) for item in coverages],
            rules=rules,
            disclaimer=COVERAGE_DISCLAIMER,
        )
    except AnalysisNotFoundError as exc:
        # Une analyse d'une autre organisation est introuvable, pas interdite : la réponse ne
        # doit pas confirmer son existence.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


@claims_evidence_router.get("/{claim_id}/coverage", response_model=ClaimCoverageResponse)
def get_claim_coverage(
    claim_id: UUID,
    as_of: date | None = Query(default=None),
    principal: TenantPrincipal = EVIDENCE_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ClaimCoverageResponse:
    """La couverture d'une seule allégation, avec ses constats et ses suggestions."""

    try:
        claim = get_claim(db, organization_id=principal.organization_id, claim_id=claim_id)
        version = db.scalar(
            select(AnalysisVersion).where(
                AnalysisVersion.organization_id == principal.organization_id,
                AnalysisVersion.id == claim.analysis_version_id,
            )
        )
        if version is None:
            raise EvidenceNotFoundError("Version d’analyse introuvable.")
        analysis = get_analysis(
            db, organization_id=principal.organization_id, analysis_id=version.analysis_id
        )
        coverages = coverage_for_analysis_version(
            db,
            organization_id=principal.organization_id,
            analysis=analysis,
            version=version,
            as_of=as_of or paris_today(),
            with_suggestions=True,
        )
        for coverage in coverages:
            if coverage.claim.id == claim_id:
                return _present_claim_coverage(coverage)
        raise EvidenceNotFoundError("Allégation introuvable dans cette version d’analyse.")
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        _raise_evidence_error(exc)
        raise


# ---------------------------------------------------------------------------
# Analysis Claim ↔ Evidence Matrix
# ---------------------------------------------------------------------------


@analyses_evidence_router.get("/{analysis_id}/evidence-matrix", response_model=EvidenceMatrixResponse)
def get_analysis_matrix(
    analysis_id: UUID,
    version: int | None = Query(default=None),
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceMatrixResponse:
    try:
        analysis, anl_version, evaluations = get_analysis_evidence_matrix(
            db,
            organization_id=principal.organization_id,
            analysis_id=analysis_id,
            version_number=version,
        )

        # Build segments map for presenting claims
        all_claims = [
            get_claim(db, organization_id=principal.organization_id, claim_id=eval_item.claim_id)
            for eval_item in evaluations
        ]
        seg_map = _segment_map(db, organization_id=principal.organization_id, claims=all_claims)

        matrix_rows: list[ClaimWithEvidenceLinksResponse] = []
        with_evidence = 0
        missing_evidence = 0
        expired_evidence = 0
        out_of_scope_evidence = 0

        for claim_obj, eval_item in zip(all_claims, evaluations):
            presented_claim = _present_claim(claim_obj, segment=seg_map.get(claim_obj.document_segment_id))
            presented_links = [_present_evidence_link(lnk) for lnk in eval_item.links]

            # C17 — une allégation portant une pièce partiellement utilisable (périmètre non
            # comparable, validité incomplète) **a** une preuve rattachée : la compter dans
            # « claims_missing_evidence » serait faux. Le détail de ce qui manque est exposé
            # par la route de couverture, pas par ce compteur historique.
            if eval_item.coverage_status in {
                EvidenceStatus.PRESENT,
                EvidenceStatus.VERIFIED,
                EvidenceStatus.PARTIAL,
            }:
                with_evidence += 1
            elif eval_item.coverage_status == EvidenceStatus.EXPIRED:
                expired_evidence += 1
            elif eval_item.coverage_status == EvidenceStatus.OUT_OF_SCOPE:
                out_of_scope_evidence += 1
            elif eval_item.coverage_status in {EvidenceStatus.MISSING, EvidenceStatus.PENDING}:
                missing_evidence += 1

            matrix_rows.append(
                ClaimWithEvidenceLinksResponse(
                    claim=presented_claim,
                    evidence_links=presented_links,
                    coverage_status=eval_item.coverage_status,
                    is_sufficient=eval_item.is_sufficient,
                    explanation=eval_item.explanation,
                )
            )

        return EvidenceMatrixResponse(
            analysis_id=analysis.id,
            analysis_version_id=anl_version.id,
            version_number=anl_version.version_number,
            total_claims=len(evaluations),
            claims_with_evidence=with_evidence,
            claims_missing_evidence=missing_evidence,
            claims_expired_evidence=expired_evidence,
            claims_out_of_scope_evidence=out_of_scope_evidence,
            matrix_rows=matrix_rows,
            disclaimer=PRE_AUDIT_DISCLAIMER,
        )
    except Exception as exc:
        _raise_evidence_error(exc)
        raise
