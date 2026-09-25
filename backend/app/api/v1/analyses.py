"""Authenticated API for persistent deterministic claim analyses."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyses.service import (
    AnalysisConflictError,
    AnalysisEnqueueResult,
    AnalysisIdempotencyConflictError,
    AnalysisInputError,
    AnalysisNotFoundError,
    create_analysis,
    get_analysis,
    get_analysis_detection_job,
    get_analysis_version_by_number,
    get_claim,
    list_analysis_versions,
    list_version_claims,
    retry_analysis,
)
from app.core.config import settings
from app.core.database import get_db
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.analysis_schemas import (
    AnalysisCreateRequest,
    AnalysisDetailResponse,
    AnalysisDetectionJobResponse,
    AnalysisEnqueueResponse,
    AnalysisResponse,
    AnalysisVersionDetailResponse,
    AnalysisVersionResponse,
    ClaimCitationResponse,
    ClaimResponse,
)
from app.models.domain import (
    Analysis,
    AnalysisDetectionJob,
    AnalysisVersion,
    Claim,
    DocumentSegment,
)

router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])
claims_router = APIRouter(prefix="/api/v1/claims", tags=["claims"])

PRE_AUDIT_DISCLAIMER = (
    "VeriClaim AI fournit une détection automatisée d’allégations pour le pré-audit et la gestion du risque. "
    "Ce résultat ne constitue pas un avis juridique, une certification ni une décision d’autorité."
)

DATABASE_DEPENDENCY = Depends(get_db)
AUDIT_RUN_DEPENDENCY = Depends(require_permission("audit:run", csrf_protected=True))
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))


def _present_analysis(analysis: Analysis) -> AnalysisResponse:
    return AnalysisResponse(
        id=analysis.id,
        analysis_key=analysis.analysis_key,
        status=analysis.status,
        supplier_id=analysis.supplier_id,
        product_id=analysis.product_id,
        requested_by_user_id=analysis.requested_by_user_id,
        completed_at=analysis.completed_at,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )


def _result_for_response(version: AnalysisVersion, *, include_claim_manifest: bool) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result = dict(version.result_json or {})
    input_manifest = result.get("input_manifest") if isinstance(result.get("input_manifest"), dict) else None
    if not include_claim_manifest:
        result.pop("claims", None)
    return input_manifest, result


def _present_version(version: AnalysisVersion, *, include_claim_manifest: bool = False) -> AnalysisVersionResponse:
    input_manifest, result = _result_for_response(version, include_claim_manifest=include_claim_manifest)
    return AnalysisVersionResponse(
        id=version.id,
        analysis_id=version.analysis_id,
        version_number=version.version_number,
        status=version.status,
        engine_version=version.engine_version,
        rulebook_version=version.rulebook_version,
        input_manifest_sha256=version.input_manifest_sha256,
        result_sha256=version.result_sha256,
        input_manifest=input_manifest,
        result=result,
        started_at=version.started_at,
        completed_at=version.completed_at,
        created_at=version.created_at,
    )


def _present_job(job: AnalysisDetectionJob) -> AnalysisDetectionJobResponse:
    return AnalysisDetectionJobResponse(
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        available_at=job.available_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_code=job.error_code,
    )


def _segment_map(db: Session, *, organization_id: UUID, claims: list[Claim]) -> dict[UUID, DocumentSegment]:
    segment_ids = [claim.document_segment_id for claim in claims if claim.document_segment_id is not None]
    if not segment_ids:
        return {}
    segments = db.scalars(
        select(DocumentSegment).where(
            DocumentSegment.organization_id == organization_id,
            DocumentSegment.id.in_(segment_ids),
        )
    ).all()
    return {segment.id: segment for segment in segments}


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _present_claim(claim: Claim, *, segment: DocumentSegment | None) -> ClaimResponse:
    attributes = dict(claim.attributes_json or {})
    citation = ClaimCitationResponse(
        document_segment_id=claim.document_segment_id,
        document_version_id=(segment.document_version_id if segment is not None else _as_uuid(attributes.get("document_version_id"))),
        document_id=_as_uuid(attributes.get("document_id")),
        page_number=segment.page_number if segment is not None else attributes.get("page_number"),
        segment_type=segment.segment_type if segment is not None else None,
        segment_start_offset=segment.start_offset if segment is not None else attributes.get("segment_start_offset_in_document"),
        segment_end_offset=segment.end_offset if segment is not None else attributes.get("segment_end_offset_in_document"),
        source_sha256=segment.source_sha256 if segment is not None else attributes.get("segment_source_sha256"),
    )
    return ClaimResponse(
        id=claim.id,
        analysis_version_id=claim.analysis_version_id,
        document_segment_id=claim.document_segment_id,
        claim_type=claim.claim_type,
        category=claim.category,
        claim_text=claim.claim_text,
        normalized_text=claim.normalized_text,
        language=claim.language,
        start_offset=claim.start_offset,
        end_offset=claim.end_offset,
        source=claim.source,
        confidence_score=claim.confidence_score,
        status=claim.status,
        detector_version=claim.detector_version,
        attributes=attributes,
        citation=citation,
        created_at=claim.created_at,
    )


def _enqueue_response(result: AnalysisEnqueueResult) -> AnalysisEnqueueResponse:
    return AnalysisEnqueueResponse(
        analysis=_present_analysis(result.analysis),
        version=_present_version(result.version),
        detection_job=_present_job(result.job),
        idempotent_replay=result.idempotent_replay,
        disclaimer=PRE_AUDIT_DISCLAIMER,
    )


def _raise_analysis_error(exc: Exception) -> None:
    if isinstance(exc, AnalysisNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, AnalysisIdempotencyConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, AnalysisConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, AnalysisInputError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    raise exc


@router.post("", response_model=AnalysisEnqueueResponse, status_code=status.HTTP_202_ACCEPTED)
def enqueue_analysis(
    body: AnalysisCreateRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    db: Session = DATABASE_DEPENDENCY,
    principal: TenantPrincipal = AUDIT_RUN_DEPENDENCY,
) -> AnalysisEnqueueResponse:
    try:
        result = create_analysis(
            db,
            settings=settings,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            document_version_ids=body.document_version_ids,
            supplier_id=body.supplier_id,
            product_id=body.product_id,
            analysis_key=body.analysis_key,
            idempotency_key=idempotency_key,
            request_id=request_id_from_request(request),
        )
        return _enqueue_response(result)
    except (AnalysisNotFoundError, AnalysisConflictError, AnalysisInputError) as exc:
        _raise_analysis_error(exc)


@router.get("/{analysis_id}", response_model=AnalysisDetailResponse)
def read_analysis(
    analysis_id: UUID,
    db: Session = DATABASE_DEPENDENCY,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
) -> AnalysisDetailResponse:
    try:
        analysis = get_analysis(db, organization_id=principal.organization_id, analysis_id=analysis_id)
        versions = list_analysis_versions(db, organization_id=principal.organization_id, analysis_id=analysis_id)
        rendered_versions = [_present_version(version) for version in versions]
        return AnalysisDetailResponse(
            analysis=_present_analysis(analysis),
            versions=rendered_versions,
            latest_version=rendered_versions[-1] if rendered_versions else None,
            disclaimer=PRE_AUDIT_DISCLAIMER,
        )
    except (AnalysisNotFoundError, AnalysisConflictError, AnalysisInputError) as exc:
        _raise_analysis_error(exc)


@router.get("/{analysis_id}/versions/{version_number}", response_model=AnalysisVersionDetailResponse)
def read_analysis_version(
    analysis_id: UUID,
    version_number: int,
    db: Session = DATABASE_DEPENDENCY,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
) -> AnalysisVersionDetailResponse:
    try:
        analysis = get_analysis(db, organization_id=principal.organization_id, analysis_id=analysis_id)
        version = get_analysis_version_by_number(
            db,
            organization_id=principal.organization_id,
            analysis_id=analysis_id,
            version_number=version_number,
        )
        claims = list_version_claims(db, organization_id=principal.organization_id, analysis_version_id=version.id)
        segments = _segment_map(db, organization_id=principal.organization_id, claims=claims)
        job = get_analysis_detection_job(db, organization_id=principal.organization_id, analysis_version_id=version.id)
        return AnalysisVersionDetailResponse(
            analysis=_present_analysis(analysis),
            version=_present_version(version, include_claim_manifest=True),
            detection_job=_present_job(job) if job is not None else None,
            claims=[_present_claim(claim, segment=segments.get(claim.document_segment_id)) for claim in claims],
            disclaimer=PRE_AUDIT_DISCLAIMER,
        )
    except (AnalysisNotFoundError, AnalysisConflictError, AnalysisInputError) as exc:
        _raise_analysis_error(exc)


@router.post("/{analysis_id}/retry", response_model=AnalysisEnqueueResponse, status_code=status.HTTP_202_ACCEPTED)
def enqueue_analysis_retry(
    analysis_id: UUID,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    db: Session = DATABASE_DEPENDENCY,
    principal: TenantPrincipal = AUDIT_RUN_DEPENDENCY,
) -> AnalysisEnqueueResponse:
    try:
        result = retry_analysis(
            db,
            settings=settings,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            analysis_id=analysis_id,
            idempotency_key=idempotency_key,
            request_id=request_id_from_request(request),
        )
        return _enqueue_response(result)
    except (AnalysisNotFoundError, AnalysisConflictError, AnalysisInputError) as exc:
        _raise_analysis_error(exc)


@claims_router.get("/{claim_id}", response_model=ClaimResponse)
def read_claim(
    claim_id: UUID,
    db: Session = DATABASE_DEPENDENCY,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
) -> ClaimResponse:
    try:
        claim = get_claim(db, organization_id=principal.organization_id, claim_id=claim_id)
        segments = _segment_map(db, organization_id=principal.organization_id, claims=[claim])
        return _present_claim(claim, segment=segments.get(claim.document_segment_id))
    except (AnalysisNotFoundError, AnalysisConflictError, AnalysisInputError) as exc:
        _raise_analysis_error(exc)
