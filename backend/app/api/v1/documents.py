"""Tenant-scoped secure document-import API.

Browser bytes go directly to a signed quarantine upload. They become a usable
``DocumentVersion`` only after the authenticated completion endpoint validates
metadata, scans malware and promotes the object to the clean bucket.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.documents.extraction import (
    DocumentExtractionConflictError,
    DocumentExtractionNotFoundError,
    list_document_segments,
    retry_document_extraction,
)
from app.documents.scanner import MalwareScanner
from app.documents.security import DocumentSecurityValidationError
from app.documents.service import (
    DocumentConflictError,
    DocumentNotFoundError,
    complete_document_upload,
    create_document,
    create_document_download,
    document_detail,
    request_document_upload,
)
from app.documents.storage import ObjectStorageError, ObjectStorageUnavailableError
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.document_schemas import (
    DocumentCreateRequest,
    DocumentDetailResponse,
    DocumentDownloadResponse,
    DocumentExtractionJobResponse,
    DocumentExtractionRetryResponse,
    DocumentResponse,
    DocumentSegmentResponse,
    DocumentUploadCompletionResponse,
    DocumentUploadInstructionResponse,
    DocumentUploadResponse,
    DocumentVersionResponse,
    DocumentVersionSegmentsResponse,
    DocumentVersionUploadRequest,
)
from app.models.domain import (
    Document,
    DocumentExtractionJob,
    DocumentSegment,
    DocumentUpload,
    DocumentVersion,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
upload_router = APIRouter(prefix="/api/v1/document-uploads", tags=["documents"])
version_router = APIRouter(prefix="/api/v1/document-versions", tags=["documents"])

# FastAPI dependency objects are intentionally built once rather than invoked in
# endpoint default values. This keeps the dependency graph explicit and avoids
# recreating permission closures for every route declaration.
DATABASE_DEPENDENCY = Depends(get_db)
DOCUMENTS_MANAGE_DEPENDENCY = Depends(require_permission("documents:manage", csrf_protected=True))
DOCUMENTS_READ_DEPENDENCY = Depends(require_permission("documents:read"))
DOCUMENTS_READ_CSRF_DEPENDENCY = Depends(require_permission("documents:read", csrf_protected=True))


def _present_document(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        document_key=document.document_key,
        title=document.title,
        document_type=document.document_type,
        status=document.status,
        supplier_id=document.supplier_id,
        product_id=document.product_id,
        tags=list(document.tags_json),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _present_extraction_job(job: DocumentExtractionJob | None) -> DocumentExtractionJobResponse | None:
    if job is None:
        return None
    return DocumentExtractionJobResponse(
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        available_at=job.available_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_code=job.error_code,
    )


def _present_segment(segment: DocumentSegment) -> DocumentSegmentResponse:
    return DocumentSegmentResponse(
        id=segment.id,
        sequence_number=segment.sequence_number,
        page_number=segment.page_number,
        segment_type=segment.segment_type,
        text=segment.text,
        start_offset=segment.start_offset,
        end_offset=segment.end_offset,
        bounding_box=segment.bounding_box_json,
        source_sha256=segment.source_sha256,
    )


def _present_version(version: DocumentVersion) -> DocumentVersionResponse:
    return DocumentVersionResponse(
        id=version.id,
        version_number=version.version_number,
        source_filename=version.source_filename,
        content_type=version.content_type,
        sha256=version.sha256,
        size_bytes=version.size_bytes,
        page_count=version.page_count,
        extraction_status=version.extraction_status,
        extraction_error_code=version.extraction_error_code,
        extraction_completed_at=version.extraction_completed_at,
        extraction_job=_present_extraction_job(version.extraction_job),
        created_at=version.created_at,
    )


def _present_upload(upload: DocumentUpload) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        id=upload.id,
        document_id=upload.document_id,
        status=upload.status,
        source_filename=upload.source_filename,
        declared_content_type=upload.declared_content_type,
        declared_size_bytes=upload.declared_size_bytes,
        expires_at=upload.expires_at,
        uploaded_sha256=upload.uploaded_sha256,
        scan_engine=upload.scan_engine,
        scan_completed_at=upload.scan_completed_at,
        scan_error_code=upload.scan_error_code,
        finalized_document_version_id=upload.finalized_document_version_id,
    )


def _detail_response(document: Document, versions: list[DocumentVersion], uploads: list[DocumentUpload]) -> DocumentDetailResponse:
    return DocumentDetailResponse(
        document=_present_document(document),
        versions=[_present_version(version) for version in versions],
        uploads=[_present_upload(upload) for upload in uploads],
    )


def _raise_document_error(exc: Exception) -> None:
    if isinstance(exc, (DocumentNotFoundError, DocumentExtractionNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (DocumentConflictError, DocumentExtractionConflictError)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, DocumentSecurityValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if isinstance(exc, (ObjectStorageUnavailableError, ObjectStorageError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le stockage documentaire sécurisé est momentanément indisponible.",
        ) from exc
    raise exc


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document_endpoint(
    body: DocumentCreateRequest,
    request: Request,
    principal: TenantPrincipal = DOCUMENTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> DocumentResponse:
    try:
        document = create_document(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            title=body.title,
            document_type=body.document_type,
            supplier_id=body.supplier_id,
            product_id=body.product_id,
            tags=body.tags,
            request_id=request_id_from_request(request),
        )
    except (DocumentNotFoundError, DocumentConflictError) as exc:
        _raise_document_error(exc)
    return _present_document(document)


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document_endpoint(
    document_id: str,
    principal: TenantPrincipal = DOCUMENTS_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> DocumentDetailResponse:
    from uuid import UUID

    try:
        parsed_id = UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document introuvable ou inaccessible.") from exc
    try:
        document, versions, uploads = document_detail(db, organization_id=principal.organization_id, document_id=parsed_id)
    except DocumentNotFoundError as exc:
        _raise_document_error(exc)
    return _detail_response(document, versions, uploads)


@router.post("/{document_id}/versions", response_model=DocumentUploadInstructionResponse, status_code=status.HTTP_201_CREATED)
def request_document_version_upload(
    document_id: str,
    body: DocumentVersionUploadRequest,
    request: Request,
    principal: TenantPrincipal = DOCUMENTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> DocumentUploadInstructionResponse:
    from uuid import UUID

    try:
        parsed_id = UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document introuvable ou inaccessible.") from exc
    storage = request.app.state.document_storage
    try:
        upload_request = request_document_upload(
            db,
            settings=settings,
            storage=storage,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            document_id=parsed_id,
            source_filename=body.source_filename,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            expected_sha256=body.expected_sha256,
            request_id=request_id_from_request(request),
        )
    except (DocumentNotFoundError, DocumentConflictError, DocumentSecurityValidationError, ObjectStorageError) as exc:
        _raise_document_error(exc)
    return DocumentUploadInstructionResponse(
        upload=_present_upload(upload_request.upload),
        upload_url=upload_request.upload_form.url,
        upload_fields=upload_request.upload_form.fields,
        max_size_bytes=body.size_bytes,
    )


@upload_router.post(
    "/{upload_id}/complete",
    response_model=DocumentUploadCompletionResponse,
    responses={
        201: {"model": DocumentUploadCompletionResponse, "description": "Version propre promue."},
        410: {"model": DocumentUploadCompletionResponse, "description": "Intent d’upload expiré."},
        422: {"model": DocumentUploadCompletionResponse, "description": "Fichier rejeté par les contrôles."},
        503: {"model": DocumentUploadCompletionResponse, "description": "Scanner ou stockage indisponible; aucune promotion."},
    },
)
def complete_document_upload_endpoint(
    upload_id: str,
    request: Request,
    response: Response,
    principal: TenantPrincipal = DOCUMENTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
):
    from uuid import UUID

    try:
        parsed_id = UUID(upload_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import documentaire introuvable ou inaccessible.") from exc
    storage = request.app.state.document_storage
    scanner: MalwareScanner = request.app.state.document_scanner
    try:
        result = complete_document_upload(
            db,
            settings=settings,
            storage=storage,
            scanner=scanner,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            upload_id=parsed_id,
            request_id=request_id_from_request(request),
        )
    except (DocumentNotFoundError, DocumentConflictError) as exc:
        _raise_document_error(exc)

    body = DocumentUploadCompletionResponse(
        upload=_present_upload(result.upload),
        document=_present_document(result.document),
        version=_present_version(result.version) if result.version else None,
        detail=result.detail,
    )
    response.status_code = result.status_code
    return body


@version_router.get("/{version_id}/segments", response_model=DocumentVersionSegmentsResponse)
def get_document_version_segments(
    version_id: str,
    principal: TenantPrincipal = DOCUMENTS_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> DocumentVersionSegmentsResponse:
    from uuid import UUID

    try:
        parsed_id = UUID(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version documentaire introuvable ou inaccessible.") from exc
    try:
        version, segments = list_document_segments(
            db,
            organization_id=principal.organization_id,
            document_version_id=parsed_id,
        )
    except DocumentExtractionNotFoundError as exc:
        _raise_document_error(exc)
    return DocumentVersionSegmentsResponse(
        version=_present_version(version),
        segments=[_present_segment(segment) for segment in segments],
    )


@version_router.post("/{version_id}/extraction/retry", response_model=DocumentExtractionRetryResponse)
def retry_document_version_extraction(
    version_id: str,
    request: Request,
    principal: TenantPrincipal = DOCUMENTS_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> DocumentExtractionRetryResponse:
    from uuid import UUID

    try:
        parsed_id = UUID(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version documentaire introuvable ou inaccessible.") from exc
    try:
        version, job = retry_document_extraction(
            db,
            settings=settings,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            document_version_id=parsed_id,
            request_id=request_id_from_request(request),
        )
    except (DocumentExtractionNotFoundError, DocumentExtractionConflictError) as exc:
        _raise_document_error(exc)
    return DocumentExtractionRetryResponse(
        version=_present_version(version),
        extraction_job=_present_extraction_job(job),
    )


@version_router.post("/{version_id}/download-url", response_model=DocumentDownloadResponse)
def create_download_url(
    version_id: str,
    request: Request,
    principal: TenantPrincipal = DOCUMENTS_READ_CSRF_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> DocumentDownloadResponse:
    from uuid import UUID

    try:
        parsed_id = UUID(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version documentaire introuvable ou inaccessible.") from exc
    try:
        _, url, expires_at = create_document_download(
            db,
            settings=settings,
            storage=request.app.state.document_storage,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            version_id=parsed_id,
            request_id=request_id_from_request(request),
        )
    except (DocumentNotFoundError, ObjectStorageError) as exc:
        _raise_document_error(exc)
    return DocumentDownloadResponse(url=url, expires_at=expires_at)
