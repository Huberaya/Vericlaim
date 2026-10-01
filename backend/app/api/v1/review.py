"""Authenticated API routes for Human Review Validations and Supplier Evidence Requests (Chantier 6.3)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.billing.http import require_active_subscription
from app.analyses.service import AnalysisNotFoundError, get_analysis_version_by_number
from app.core.database import get_db
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.domain import EvidenceRequest, EvidenceRequestStatus, Validation
from app.models.review_schemas import (
    EvidenceRequestCreateRequest,
    EvidenceRequestListResponse,
    EvidenceRequestResponse,
    EvidenceRequestUpdateRequest,
    TemplateGenerationRequest,
    TemplateGenerationResponse,
    ValidationCreateRequest,
    ValidationResponse,
)
from app.review.service import (
    ReviewConflictError,
    ReviewInputError,
    ReviewNotFoundError,
    create_evidence_request,
    delete_evidence_request,
    generate_supplier_request_template,
    get_evidence_request,
    list_evidence_requests,
    list_validations,
    record_validation,
    remind_evidence_request,
    send_evidence_request,
    update_evidence_request,
)


validations_router = APIRouter(prefix="/api/v1/validations", tags=["validations"])

# C13 — garde d'abonnement : cette route produit un artefact payant ou une
# dépense réelle (OCR, e-mail tiers, rapport signé). Voir app/billing/http.py.
SUBSCRIPTION_GATE = Depends(require_active_subscription())
analysis_validations_router = APIRouter(prefix="/api/v1/analyses", tags=["analyses-validations"])
evidence_requests_router = APIRouter(prefix="/api/v1/evidence-requests", tags=["evidence-requests"])

DATABASE_DEPENDENCY = Depends(get_db)
VALIDATION_MANAGE_DEPENDENCY = Depends(require_permission("validation:manage", csrf_protected=True))
VALIDATION_READ_DEPENDENCY = Depends(require_permission("validation:read"))
REQUESTS_MANAGE_DEPENDENCY = Depends(require_permission("evidence_requests:manage", csrf_protected=True))
REQUESTS_READ_DEPENDENCY = Depends(require_permission("evidence_requests:read"))


def _present_validation(val: Validation) -> ValidationResponse:
    reviewer_name = val.reviewer_user.display_name if val.reviewer_user else None
    return ValidationResponse(
        id=val.id,
        analysis_version_id=val.analysis_version_id,
        claim_id=val.claim_id,
        decision=val.decision,
        reviewer_user_id=val.reviewer_user_id,
        reviewer_display_name=reviewer_name,
        comment=val.comment,
        rationale=val.rationale,
        decided_at=val.decided_at,
        created_at=val.created_at,
    )


def _present_evidence_request(req: EvidenceRequest) -> EvidenceRequestResponse:
    return EvidenceRequestResponse(
        id=req.id,
        supplier_id=req.supplier_id,
        supplier_name=req.supplier.legal_name if req.supplier else None,
        product_id=req.product_id,
        product_name=req.product.name if req.product else None,
        claim_id=req.claim_id,
        claim_text=req.claim.claim_text if req.claim else None,
        status=req.status,
        subject=req.subject,
        message=req.message,
        requested_items=list(req.requested_items_json or []),
        due_at=req.due_at,
        sent_at=req.sent_at,
        last_reminded_at=req.last_reminded_at,
        created_by_user_id=req.created_by_user_id,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


def _raise_review_error(exc: Exception) -> None:
    if isinstance(exc, (ReviewNotFoundError, AnalysisNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ReviewConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, ReviewInputError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    raise exc


# ---------------------------------------------------------------------------
# Validations
# ---------------------------------------------------------------------------


@validations_router.post("", response_model=ValidationResponse, status_code=status.HTTP_201_CREATED, dependencies=[SUBSCRIPTION_GATE])
def record_human_validation(
    body: ValidationCreateRequest,
    request: Request,
    principal: TenantPrincipal = VALIDATION_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ValidationResponse:
    try:
        val = record_validation(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            analysis_version_id=body.analysis_version_id,
            claim_id=body.claim_id,
            decision=body.decision,
            comment=body.comment,
            rationale=body.rationale,
            request_id=request_id_from_request(request),
        )
        return _present_validation(val)
    except Exception as exc:
        _raise_review_error(exc)
        raise


@analysis_validations_router.get("/{analysis_id}/versions/{version_number}/validations", response_model=list[ValidationResponse])
def get_version_validations(
    analysis_id: UUID,
    version_number: int,
    principal: TenantPrincipal = VALIDATION_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> list[ValidationResponse]:
    try:
        version = get_analysis_version_by_number(
            db,
            organization_id=principal.organization_id,
            analysis_id=analysis_id,
            version_number=version_number,
        )
        validations = list_validations(db, organization_id=principal.organization_id, analysis_version_id=version.id)
        return [_present_validation(v) for v in validations]
    except Exception as exc:
        _raise_review_error(exc)
        raise


# ---------------------------------------------------------------------------
# Evidence Requests
# ---------------------------------------------------------------------------


@evidence_requests_router.post("", response_model=EvidenceRequestResponse, status_code=status.HTTP_201_CREATED, dependencies=[SUBSCRIPTION_GATE])
def create_new_evidence_request(
    body: EvidenceRequestCreateRequest,
    request: Request,
    principal: TenantPrincipal = REQUESTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceRequestResponse:
    try:
        req = create_evidence_request(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            supplier_id=body.supplier_id,
            product_id=body.product_id,
            claim_id=body.claim_id,
            subject=body.subject,
            message=body.message,
            requested_items=body.requested_items,
            due_at=body.due_at,
            request_id=request_id_from_request(request),
        )
        return _present_evidence_request(req)
    except Exception as exc:
        _raise_review_error(exc)
        raise


@evidence_requests_router.get("", response_model=EvidenceRequestListResponse)
def list_organization_evidence_requests(
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    status_filter: EvidenceRequestStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    principal: TenantPrincipal = REQUESTS_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceRequestListResponse:
    try:
        page = list_evidence_requests(
            db,
            organization_id=principal.organization_id,
            limit=limit,
            cursor=cursor,
            supplier_id=supplier_id,
            product_id=product_id,
            status=status_filter,
        )
        return EvidenceRequestListResponse(
            items=[_present_evidence_request(item) for item in page.items],
            next_cursor=page.next_cursor,
        )
    except Exception as exc:
        _raise_review_error(exc)
        raise


@evidence_requests_router.get("/{request_id}", response_model=EvidenceRequestResponse)
def get_request_detail(
    request_id: UUID,
    principal: TenantPrincipal = REQUESTS_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceRequestResponse:
    try:
        req = get_evidence_request(db, organization_id=principal.organization_id, request_id=request_id)
        return _present_evidence_request(req)
    except Exception as exc:
        _raise_review_error(exc)
        raise


@evidence_requests_router.patch("/{request_id}", response_model=EvidenceRequestResponse)
def update_request(
    request_id: UUID,
    body: EvidenceRequestUpdateRequest,
    request: Request,
    principal: TenantPrincipal = REQUESTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceRequestResponse:
    try:
        changes = body.model_dump(exclude_unset=True)
        req = update_evidence_request(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            request_id=request_id,
            changes=changes,
            req_id=request_id_from_request(request),
        )
        return _present_evidence_request(req)
    except Exception as exc:
        _raise_review_error(exc)
        raise


@evidence_requests_router.post("/{request_id}/send", response_model=EvidenceRequestResponse, dependencies=[SUBSCRIPTION_GATE])
def send_request_to_supplier(
    request_id: UUID,
    request: Request,
    principal: TenantPrincipal = REQUESTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceRequestResponse:
    try:
        req = send_evidence_request(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            request_id=request_id,
            req_id=request_id_from_request(request),
        )
        return _present_evidence_request(req)
    except Exception as exc:
        _raise_review_error(exc)
        raise


@evidence_requests_router.post("/{request_id}/remind", response_model=EvidenceRequestResponse, dependencies=[SUBSCRIPTION_GATE])
def record_supplier_reminder(
    request_id: UUID,
    request: Request,
    principal: TenantPrincipal = REQUESTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> EvidenceRequestResponse:
    try:
        req = remind_evidence_request(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            request_id=request_id,
            req_id=request_id_from_request(request),
        )
        return _present_evidence_request(req)
    except Exception as exc:
        _raise_review_error(exc)
        raise


@evidence_requests_router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_and_delete_request(
    request_id: UUID,
    request: Request,
    principal: TenantPrincipal = REQUESTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> None:
    try:
        delete_evidence_request(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            request_id=request_id,
            req_id=request_id_from_request(request),
        )
    except Exception as exc:
        _raise_review_error(exc)
        raise


@evidence_requests_router.post("/generate-template", response_model=TemplateGenerationResponse, dependencies=[SUBSCRIPTION_GATE])
def generate_request_template(
    body: TemplateGenerationRequest,
    principal: TenantPrincipal = REQUESTS_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> TemplateGenerationResponse:
    try:
        template = generate_supplier_request_template(
            db,
            organization_id=principal.organization_id,
            claim_id=body.claim_id,
            supplier_id=body.supplier_id,
            product_id=body.product_id,
            target_evidence_type=body.target_evidence_type,
        )
        return TemplateGenerationResponse(**template)
    except Exception as exc:
        _raise_review_error(exc)
        raise
