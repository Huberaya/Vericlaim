"""Authenticated API routes for persistent evidence registry and claim linking."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.analyses.service import get_claim
from app.api.v1.analyses import PRE_AUDIT_DISCLAIMER, _present_claim, _segment_map
from app.core.database import get_db
from app.evidence.service import (
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
from app.models.domain import Evidence, EvidenceLink, EvidenceStatus, EvidenceType
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


@router.post("", response_model=EvidenceCreateResponse, status_code=status.HTTP_201_CREATED)
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


@claims_evidence_router.post("/{claim_id}/evidence-links", response_model=EvidenceLinkResponse, status_code=status.HTTP_201_CREATED)
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

            if eval_item.coverage_status in {EvidenceStatus.PRESENT, EvidenceStatus.VERIFIED}:
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
