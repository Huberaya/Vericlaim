"""Durable, tenant-scoped document extraction orchestration.

The queue is stored in PostgreSQL so enqueueing is transactional with a clean
MinIO promotion. Workers claim jobs under an explicit tenant RLS context, then
read only immutable clean objects whose SHA-256 still matches the promoted
``DocumentVersion``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.documents.security import (
    DocumentSecurityValidationError,
    inspect_document_bytes,
)
from app.documents.storage import ObjectNotFoundError, ObjectStorage, ObjectStorageError
from app.engine.document_extractor import (
    DocumentExtractionError,
    DocumentTextExtractor,
    ExtractionResult,
)
from app.identity.service import append_audit_event
from app.models.domain import (
    Document,
    DocumentExtractionJob,
    DocumentExtractionJobStatus,
    DocumentSegment,
    DocumentStatus,
    DocumentVersion,
    ExtractionStatus,
    SegmentType,
)

EXTRACTION_ENGINE_VERSION = "vericlaim-document-extractor-v1"


class DocumentExtractionServiceError(RuntimeError):
    """Base error safe to map at the document-extraction API boundary."""


class DocumentExtractionNotFoundError(DocumentExtractionServiceError):
    pass


class DocumentExtractionConflictError(DocumentExtractionServiceError):
    pass


class DocumentExtractionTerminalError(DocumentExtractionServiceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DocumentExtractionRetryableError(DocumentExtractionServiceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ClaimedExtractionJob:
    id: UUID
    organization_id: UUID
    document_version_id: UUID
    worker_id: str
    attempt_count: int


@dataclass(frozen=True)
class ExtractionCompletion:
    version: DocumentVersion
    job: DocumentExtractionJob
    segment_count: int
    requires_human_review: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _get_version(db: Session, *, organization_id: UUID, version_id: UUID, lock: bool = False) -> DocumentVersion:
    statement = select(DocumentVersion).where(
        DocumentVersion.organization_id == organization_id,
        DocumentVersion.id == version_id,
    )
    if lock:
        statement = statement.with_for_update()
    version = db.scalar(statement)
    if version is None:
        raise DocumentExtractionNotFoundError("Version documentaire introuvable ou inaccessible.")
    return version


def _get_document(db: Session, *, organization_id: UUID, document_id: UUID, lock: bool = False) -> Document:
    statement = select(Document).where(
        Document.organization_id == organization_id,
        Document.id == document_id,
        Document.deleted_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    document = db.scalar(statement)
    if document is None:
        raise DocumentExtractionNotFoundError("Document introuvable ou inaccessible.")
    return document


def _get_job_for_version(
    db: Session,
    *,
    organization_id: UUID,
    document_version_id: UUID,
    lock: bool = False,
) -> DocumentExtractionJob | None:
    statement = select(DocumentExtractionJob).where(
        DocumentExtractionJob.organization_id == organization_id,
        DocumentExtractionJob.document_version_id == document_version_id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _set_document_status_from_latest_version(db: Session, *, document: Document, organization_id: UUID) -> None:
    """Keep the logical document status aligned with its latest immutable version."""
    if document.status in {DocumentStatus.ARCHIVED, DocumentStatus.DELETED}:
        return
    latest = db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.document_id == document.id,
        )
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    if latest is None:
        document.status = DocumentStatus.QUARANTINED
    elif latest.extraction_status in {ExtractionStatus.PENDING, ExtractionStatus.RUNNING}:
        document.status = DocumentStatus.PROCESSING
    elif latest.extraction_status in {ExtractionStatus.COMPLETED, ExtractionStatus.REVIEW_REQUIRED}:
        document.status = DocumentStatus.READY
    elif latest.extraction_status == ExtractionStatus.FAILED:
        document.status = DocumentStatus.FAILED


def enqueue_document_extraction(
    db: Session,
    *,
    settings: Settings,
    document: Document,
    version: DocumentVersion,
    organization_id: UUID,
    actor_user_id: UUID | None,
    request_id: str | None,
) -> DocumentExtractionJob:
    """Create the one durable job for a newly promoted immutable version.

    This function deliberately performs no external side effect. It is called
    inside the same database transaction as the clean object/version promotion,
    so a worker cannot see a committed version without a corresponding job.
    """
    existing = version.extraction_job or _get_job_for_version(
        db,
        organization_id=organization_id,
        document_version_id=version.id,
    )
    if existing is not None:
        return existing
    job = DocumentExtractionJob(
        id=uuid4(),
        organization_id=organization_id,
        document_version_id=version.id,
        requested_by_user_id=actor_user_id,
        status=DocumentExtractionJobStatus.QUEUED,
        attempt_count=0,
        max_attempts=settings.document_extraction_max_attempts,
        available_at=utcnow(),
    )
    version.extraction_job = job
    db.add(job)
    if document.status not in {DocumentStatus.ARCHIVED, DocumentStatus.DELETED}:
        document.status = DocumentStatus.PROCESSING
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document_extraction_job",
        entity_id=job.id,
        action="document.extraction_queued",
        payload={
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "max_attempts": job.max_attempts,
            "engine_version": EXTRACTION_ENGINE_VERSION,
        },
        request_id=request_id,
    )
    return job


def get_document_extraction_job(
    db: Session,
    *,
    organization_id: UUID,
    document_version_id: UUID,
) -> DocumentExtractionJob | None:
    return _get_job_for_version(
        db,
        organization_id=organization_id,
        document_version_id=document_version_id,
    )


def list_document_segments(
    db: Session,
    *,
    organization_id: UUID,
    document_version_id: UUID,
) -> tuple[DocumentVersion, list[DocumentSegment]]:
    version = _get_version(db, organization_id=organization_id, version_id=document_version_id)
    segments = list(
        db.scalars(
            select(DocumentSegment)
            .where(
                DocumentSegment.organization_id == organization_id,
                DocumentSegment.document_version_id == version.id,
            )
            .order_by(DocumentSegment.sequence_number.asc())
        ).all()
    )
    return version, segments


def retry_document_extraction(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    actor_user_id: UUID,
    document_version_id: UUID,
    request_id: str | None,
) -> tuple[DocumentVersion, DocumentExtractionJob]:
    """Requeue only a failed extraction; completed evidence is never overwritten."""
    version = _get_version(db, organization_id=organization_id, version_id=document_version_id, lock=True)
    job = _get_job_for_version(
        db,
        organization_id=organization_id,
        document_version_id=version.id,
        lock=True,
    )
    if job is None:
        raise DocumentExtractionConflictError("Cette version ne possède pas de job d’extraction à relancer.")
    if job.status in {DocumentExtractionJobStatus.QUEUED, DocumentExtractionJobStatus.RUNNING}:
        return version, job
    if version.extraction_status != ExtractionStatus.FAILED or job.status != DocumentExtractionJobStatus.FAILED:
        raise DocumentExtractionConflictError(
            "Seule une extraction en échec peut être relancée; une extraction terminée reste immuable."
        )

    now = utcnow()
    job.status = DocumentExtractionJobStatus.QUEUED
    # Preserve the total attempt history while allowing a new bounded retry
    # cycle. The audit event records who explicitly requested that cycle.
    job.max_attempts = job.attempt_count + settings.document_extraction_max_attempts
    job.available_at = now
    job.locked_at = None
    job.lease_expires_at = None
    job.worker_id = None
    job.completed_at = None
    job.error_code = None
    version.extraction_status = ExtractionStatus.PENDING
    version.extraction_error_code = None
    version.extraction_completed_at = None
    document = _get_document(db, organization_id=organization_id, document_id=version.document_id, lock=True)
    if document.status not in {DocumentStatus.ARCHIVED, DocumentStatus.DELETED}:
        document.status = DocumentStatus.PROCESSING
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document_extraction_job",
        entity_id=job.id,
        action="document.extraction_retry_requested",
        payload={
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
        },
        request_id=request_id,
    )
    db.flush()
    return version, job


def _recover_expired_leases(
    db: Session,
    *,
    organization_id: UUID,
    request_id: str | None = None,
) -> None:
    now = utcnow()
    expired = list(
        db.scalars(
            select(DocumentExtractionJob)
            .where(
                DocumentExtractionJob.organization_id == organization_id,
                DocumentExtractionJob.status == DocumentExtractionJobStatus.RUNNING,
                DocumentExtractionJob.lease_expires_at.is_not(None),
                DocumentExtractionJob.lease_expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        ).all()
    )
    for job in expired:
        version = _get_version(db, organization_id=organization_id, version_id=job.document_version_id, lock=True)
        document = _get_document(db, organization_id=organization_id, document_id=version.document_id, lock=True)
        job.locked_at = None
        job.lease_expires_at = None
        job.worker_id = None
        job.error_code = "worker_lease_expired"
        if job.attempt_count < job.max_attempts:
            job.status = DocumentExtractionJobStatus.QUEUED
            job.available_at = now
            version.extraction_status = ExtractionStatus.PENDING
            version.extraction_error_code = "worker_lease_expired"
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=None,
                entity_type="document_extraction_job",
                entity_id=job.id,
                action="document.extraction_lease_requeued",
                payload={"document_id": str(document.id), "document_version_id": str(version.id)},
                request_id=request_id,
            )
        else:
            job.status = DocumentExtractionJobStatus.FAILED
            job.completed_at = now
            version.extraction_status = ExtractionStatus.FAILED
            version.extraction_error_code = "worker_lease_expired"
            version.extraction_completed_at = now
            db.flush()
            _set_document_status_from_latest_version(db, document=document, organization_id=organization_id)
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=None,
                entity_type="document_extraction_job",
                entity_id=job.id,
                action="document.extraction_failed",
                payload={
                    "document_id": str(document.id),
                    "document_version_id": str(version.id),
                    "error_code": "worker_lease_expired",
                    "attempt_count": job.attempt_count,
                },
                request_id=request_id,
            )
    if expired:
        db.flush()


def claim_next_document_extraction_job(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    worker_id: str,
) -> ClaimedExtractionJob | None:
    """Atomically lease one due job for one organisation.

    The worker enumerates global organisation IDs, then installs that specific
    tenant context before calling this function. Consequently every job/version
    query remains covered by the regular tenant RLS policy; no broad RLS bypass
    or broker credential is required for the MVP.
    """
    if db.info.get("current_organization_id") != organization_id:
        raise DocumentExtractionConflictError("Le worker doit définir le contexte organisationnel avant de réclamer un job.")
    _recover_expired_leases(db, organization_id=organization_id)
    now = utcnow()
    job = db.scalar(
        select(DocumentExtractionJob)
        .where(
            DocumentExtractionJob.organization_id == organization_id,
            DocumentExtractionJob.status == DocumentExtractionJobStatus.QUEUED,
            DocumentExtractionJob.available_at <= now,
        )
        .order_by(DocumentExtractionJob.available_at.asc(), DocumentExtractionJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    version = _get_version(db, organization_id=organization_id, version_id=job.document_version_id, lock=True)
    document = _get_document(db, organization_id=organization_id, document_id=version.document_id, lock=True)

    if version.extraction_status in {ExtractionStatus.COMPLETED, ExtractionStatus.REVIEW_REQUIRED}:
        job.status = DocumentExtractionJobStatus.COMPLETED
        job.completed_at = now
        job.lease_expires_at = None
        job.worker_id = None
        db.flush()
        return None
    if version.extraction_status == ExtractionStatus.FAILED and job.attempt_count >= job.max_attempts:
        job.status = DocumentExtractionJobStatus.FAILED
        job.completed_at = now
        db.flush()
        return None

    job.status = DocumentExtractionJobStatus.RUNNING
    job.attempt_count += 1
    job.locked_at = now
    job.lease_expires_at = now + timedelta(seconds=settings.document_extraction_lease_seconds)
    job.worker_id = worker_id
    job.started_at = job.started_at or now
    job.completed_at = None
    job.error_code = None
    version.extraction_status = ExtractionStatus.RUNNING
    version.extraction_error_code = None
    version.extraction_completed_at = None
    if document.status not in {DocumentStatus.ARCHIVED, DocumentStatus.DELETED}:
        document.status = DocumentStatus.PROCESSING
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=None,
        entity_type="document_extraction_job",
        entity_id=job.id,
        action="document.extraction_started",
        payload={
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "attempt_count": job.attempt_count,
            "lease_expires_at": job.lease_expires_at.isoformat(),
            "engine_version": EXTRACTION_ENGINE_VERSION,
        },
    )
    db.flush()
    return ClaimedExtractionJob(
        id=job.id,
        organization_id=organization_id,
        document_version_id=version.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
    )


def _best_effort_delete(storage: ObjectStorage, *, bucket: str, key: str) -> None:
    try:
        storage.delete(bucket=bucket, key=key)
    except ObjectStorageError:
        # A later reconciliation task can remove an unreferenced derived
        # artefact. It remains in the private clean bucket and is never exposed
        # through an API response.
        pass


def _derived_text_key(*, organization_id: UUID, document_id: UUID, version_id: UUID, job_id: UUID, attempt_count: int) -> str:
    return (
        f"derived/{organization_id}/{document_id}/versions/{version_id}/"
        f"extractions/{job_id}/attempt-{attempt_count}/text.txt"
    )


def _segment_type(value: str) -> SegmentType:
    return SegmentType.IMAGE_OCR if value == "image_ocr" else SegmentType.PARAGRAPH


def process_claimed_document_extraction(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    claim: ClaimedExtractionJob,
) -> ExtractionCompletion:
    """Extract one leased job and atomically publish its durable result.

    The source checksum is rechecked immediately before parsing. A ClamAV-clean
    object changed outside the controlled promotion path is therefore never fed
    to PyMuPDF/Pillow/Tesseract.
    """
    if db.info.get("current_organization_id") != claim.organization_id:
        raise DocumentExtractionConflictError("Le worker doit définir le contexte organisationnel avant le traitement.")
    job = db.scalar(
        select(DocumentExtractionJob).where(
            DocumentExtractionJob.organization_id == claim.organization_id,
            DocumentExtractionJob.id == claim.id,
        )
    )
    if job is None:
        raise DocumentExtractionNotFoundError("Job d’extraction introuvable.")
    if job.status != DocumentExtractionJobStatus.RUNNING or job.worker_id != claim.worker_id:
        raise DocumentExtractionConflictError("Le lease du job d’extraction n’est plus détenu par ce worker.")
    if job.lease_expires_at is None or _as_utc(job.lease_expires_at) <= utcnow():
        raise DocumentExtractionConflictError("Le lease du job d’extraction a expiré avant son traitement.")

    version = _get_version(db, organization_id=claim.organization_id, version_id=job.document_version_id)
    try:
        payload = storage.read_bytes(
            bucket=settings.document_clean_bucket,
            key=version.storage_key,
            max_size_bytes=settings.max_upload_bytes,
        )
    except ObjectNotFoundError as exc:
        raise DocumentExtractionTerminalError("clean_object_missing", "Le binaire propre associé à cette version est introuvable.") from exc
    except ObjectStorageError as exc:
        raise DocumentExtractionRetryableError("clean_storage_unavailable", "Le stockage documentaire est indisponible.") from exc

    if hashlib.sha256(payload).hexdigest() != version.sha256:
        raise DocumentExtractionTerminalError(
            "source_checksum_mismatch",
            "L’empreinte du binaire propre ne correspond plus à la version immuable.",
        )
    try:
        inspected = inspect_document_bytes(
            payload,
            filename=version.source_filename,
            declared_content_type=version.content_type,
            max_size_bytes=settings.max_upload_bytes,
        )
    except DocumentSecurityValidationError as exc:
        raise DocumentExtractionTerminalError(
            "source_integrity_invalid",
            "Le binaire propre ne satisfait plus les contrôles de format attendus.",
        ) from exc
    if inspected.sha256 != version.sha256:
        raise DocumentExtractionTerminalError(
            "source_checksum_mismatch",
            "L’empreinte du binaire propre ne correspond plus à la version immuable.",
        )

    extractor = DocumentTextExtractor(
        max_bytes=settings.max_upload_bytes,
        max_pages=settings.max_pdf_pages,
        max_chars=settings.max_source_chars,
        max_image_pixels=settings.document_max_image_pixels,
        ocr_timeout_seconds=settings.document_ocr_timeout_seconds,
        ocr_render_scale=settings.document_ocr_render_scale,
        segment_max_chars=settings.document_segment_max_chars,
        tesseract_languages=settings.tesseract_languages,
    )
    try:
        result = extractor.extract_result(version.source_filename, version.content_type, payload)
    except DocumentExtractionError as exc:
        raise DocumentExtractionTerminalError("extraction_unusable", str(exc)) from exc

    return _publish_extraction_result(
        db,
        settings=settings,
        storage=storage,
        claim=claim,
        result=result,
    )


def _publish_extraction_result(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    claim: ClaimedExtractionJob,
    result: ExtractionResult,
) -> ExtractionCompletion:
    # OCR can outlive a lease. Re-lock and validate the claim immediately
    # before writing a derived object or changing durable state, so a worker
    # that lost its lease can never overwrite a reclaimed attempt.
    current_job = db.scalar(
        select(DocumentExtractionJob)
        .where(
            DocumentExtractionJob.organization_id == claim.organization_id,
            DocumentExtractionJob.id == claim.id,
        )
        .with_for_update()
    )
    if current_job is None:
        raise DocumentExtractionNotFoundError("Job d’extraction introuvable.")
    if current_job.status != DocumentExtractionJobStatus.RUNNING or current_job.worker_id != claim.worker_id:
        raise DocumentExtractionConflictError("Le lease du job d’extraction n’est plus détenu par ce worker.")
    if current_job.lease_expires_at is None or _as_utc(current_job.lease_expires_at) <= utcnow():
        raise DocumentExtractionConflictError("Le lease du job d’extraction a expiré avant la publication.")
    job = current_job
    version = _get_version(db, organization_id=claim.organization_id, version_id=job.document_version_id, lock=True)
    document = _get_document(db, organization_id=claim.organization_id, document_id=version.document_id, lock=True)

    existing_segments = db.scalar(
        select(func.count())
        .select_from(DocumentSegment)
        .where(
            DocumentSegment.organization_id == claim.organization_id,
            DocumentSegment.document_version_id == version.id,
        )
    )
    if existing_segments:
        raise DocumentExtractionTerminalError(
            "segments_already_published",
            "Des segments existent déjà pour cette version immuable.",
        )

    text_bytes = result.text.encode("utf-8")
    extracted_text_sha256 = hashlib.sha256(text_bytes).hexdigest()
    output_key = _derived_text_key(
        organization_id=claim.organization_id,
        document_id=document.id,
        version_id=version.id,
        job_id=job.id,
        attempt_count=claim.attempt_count,
    )
    wrote_output = False
    try:
        try:
            storage.put_bytes(
                bucket=settings.document_clean_bucket,
                key=output_key,
                payload=text_bytes,
                content_type="text/plain; charset=utf-8",
            )
            wrote_output = True
        except ObjectStorageError as exc:
            raise DocumentExtractionRetryableError(
                "derived_storage_unavailable",
                "Le stockage du texte extrait est indisponible.",
            ) from exc

        now = utcnow()
        for sequence_number, segment in enumerate(result.segments):
            db.add(
                DocumentSegment(
                    id=uuid4(),
                    organization_id=claim.organization_id,
                    document_version_id=version.id,
                    sequence_number=sequence_number,
                    page_number=segment.page_number,
                    segment_type=_segment_type(segment.segment_type),
                    text=segment.text,
                    start_offset=segment.start_offset,
                    end_offset=segment.end_offset,
                    bounding_box_json=None,
                    # Segment offsets address the canonical transcript, while
                    # this hash anchors each citation to the original binary.
                    source_sha256=version.sha256,
                )
            )
        version.page_count = result.page_count
        version.extraction_engine_version = EXTRACTION_ENGINE_VERSION
        version.extracted_text_storage_key = output_key
        version.extracted_text_sha256 = extracted_text_sha256
        version.extraction_error_code = None
        version.extraction_completed_at = now
        version.extraction_status = (
            ExtractionStatus.REVIEW_REQUIRED if result.requires_human_review else ExtractionStatus.COMPLETED
        )
        job.status = DocumentExtractionJobStatus.COMPLETED
        job.completed_at = now
        job.lease_expires_at = None
        job.error_code = None
        db.flush()
        _set_document_status_from_latest_version(db, document=document, organization_id=claim.organization_id)
        append_audit_event(
            db,
            organization_id=claim.organization_id,
            actor_user_id=None,
            entity_type="document_extraction_job",
            entity_id=job.id,
            action=(
                "document.extraction_review_required"
                if result.requires_human_review
                else "document.extraction_completed"
            ),
            payload={
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "attempt_count": job.attempt_count,
                "page_count": result.page_count,
                "segment_count": len(result.segments),
                "extraction_method": result.method,
                "extracted_text_sha256": extracted_text_sha256,
                "engine_version": EXTRACTION_ENGINE_VERSION,
                "requires_human_review": result.requires_human_review,
            },
        )
        db.flush()
    except Exception:
        if wrote_output:
            _best_effort_delete(storage, bucket=settings.document_clean_bucket, key=output_key)
        raise
    return ExtractionCompletion(
        version=version,
        job=job,
        segment_count=len(result.segments),
        requires_human_review=result.requires_human_review,
    )


def record_document_extraction_failure(
    db: Session,
    *,
    settings: Settings,
    claim: ClaimedExtractionJob,
    error_code: str,
    retryable: bool,
) -> bool:
    """Record a safe error code and schedule bounded retries when appropriate."""
    if db.info.get("current_organization_id") != claim.organization_id:
        raise DocumentExtractionConflictError("Le worker doit définir le contexte organisationnel avant de consigner un échec.")
    job = db.scalar(
        select(DocumentExtractionJob)
        .where(
            DocumentExtractionJob.organization_id == claim.organization_id,
            DocumentExtractionJob.id == claim.id,
        )
        .with_for_update()
    )
    if job is None or job.status != DocumentExtractionJobStatus.RUNNING or job.worker_id != claim.worker_id:
        return False
    version = _get_version(db, organization_id=claim.organization_id, version_id=job.document_version_id, lock=True)
    document = _get_document(db, organization_id=claim.organization_id, document_id=version.document_id, lock=True)
    now = utcnow()
    job.error_code = error_code
    job.locked_at = None
    job.lease_expires_at = None
    job.worker_id = None

    if retryable and job.attempt_count < job.max_attempts:
        delay_seconds = min(
            settings.document_extraction_retry_base_seconds * (2 ** max(0, job.attempt_count - 1)),
            60 * 60,
        )
        job.status = DocumentExtractionJobStatus.QUEUED
        job.available_at = now + timedelta(seconds=delay_seconds)
        version.extraction_status = ExtractionStatus.PENDING
        version.extraction_error_code = error_code
        version.extraction_completed_at = None
        if document.status not in {DocumentStatus.ARCHIVED, DocumentStatus.DELETED}:
            document.status = DocumentStatus.PROCESSING
        append_audit_event(
            db,
            organization_id=claim.organization_id,
            actor_user_id=None,
            entity_type="document_extraction_job",
            entity_id=job.id,
            action="document.extraction_retry_scheduled",
            payload={
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "error_code": error_code,
                "attempt_count": job.attempt_count,
                "available_at": job.available_at.isoformat(),
            },
        )
        db.flush()
        return True

    job.status = DocumentExtractionJobStatus.FAILED
    job.completed_at = now
    version.extraction_status = ExtractionStatus.FAILED
    version.extraction_error_code = error_code
    version.extraction_completed_at = now
    db.flush()
    _set_document_status_from_latest_version(db, document=document, organization_id=claim.organization_id)
    append_audit_event(
        db,
        organization_id=claim.organization_id,
        actor_user_id=None,
        entity_type="document_extraction_job",
        entity_id=job.id,
        action="document.extraction_failed",
        payload={
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "error_code": error_code,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
        },
    )
    db.flush()
    return False
