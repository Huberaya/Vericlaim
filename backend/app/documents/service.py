"""Transactional service layer for secure, tenant-scoped document ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.documents.extraction import enqueue_document_extraction
from app.documents.scanner import MalwareScanError, MalwareScanner, MalwareScannerUnavailableError
from app.documents.security import (
    DocumentSecurityValidationError,
    expected_content_type,
    inspect_document_bytes,
    is_sha256,
    normalize_content_type,
    sanitize_filename,
)
from app.documents.storage import (
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    ObjectStorageUnavailableError,
    PresignedUpload,
)
from app.identity.service import append_audit_event
from app.models.domain import (
    Document,
    DocumentStatus,
    DocumentType,
    DocumentUpload,
    DocumentUploadStatus,
    DocumentVersion,
    ExtractionStatus,
    Product,
    Supplier,
)


class DocumentServiceError(RuntimeError):
    pass


class DocumentNotFoundError(DocumentServiceError):
    pass


class DocumentConflictError(DocumentServiceError):
    pass


@dataclass(frozen=True)
class UploadRequest:
    upload: DocumentUpload
    upload_form: PresignedUpload


@dataclass(frozen=True)
class UploadFinalization:
    document: Document
    upload: DocumentUpload
    version: DocumentVersion | None
    status_code: int
    detail: str | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _active_document_statement(organization_id: UUID, document_id: UUID):
    return select(Document).where(
        Document.organization_id == organization_id,
        Document.id == document_id,
        Document.deleted_at.is_(None),
    )


def get_document(db: Session, *, organization_id: UUID, document_id: UUID, lock: bool = False) -> Document:
    statement = _active_document_statement(organization_id, document_id)
    if lock:
        statement = statement.with_for_update()
    document = db.scalar(statement)
    if document is None:
        raise DocumentNotFoundError("Document introuvable ou inaccessible.")
    return document


def _assert_references_belong_to_organization(
    db: Session,
    *,
    organization_id: UUID,
    supplier_id: UUID | None,
    product_id: UUID | None,
) -> None:
    supplier: Supplier | None = None
    product: Product | None = None
    if supplier_id is not None:
        supplier = db.scalar(
            select(Supplier).where(
                Supplier.id == supplier_id,
                Supplier.organization_id == organization_id,
                Supplier.deleted_at.is_(None),
            )
        )
        if supplier is None:
            raise DocumentNotFoundError("Fournisseur introuvable ou inaccessible.")
    if product_id is not None:
        product = db.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.organization_id == organization_id,
                Product.deleted_at.is_(None),
            )
        )
        if product is None:
            raise DocumentNotFoundError("Produit introuvable ou inaccessible.")
    if supplier is not None and product is not None and product.supplier_id != supplier.id:
        raise DocumentConflictError("Le produit ne relève pas du fournisseur sélectionné.")


def create_document(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    title: str,
    document_type: DocumentType,
    supplier_id: UUID | None,
    product_id: UUID | None,
    tags: list[str],
    request_id: str | None,
) -> Document:
    _assert_references_belong_to_organization(
        db,
        organization_id=organization_id,
        supplier_id=supplier_id,
        product_id=product_id,
    )
    document = Document(
        organization_id=organization_id,
        supplier_id=supplier_id,
        product_id=product_id,
        document_key=f"DOC-{uuid4().hex.upper()}",
        title=title,
        document_type=document_type,
        status=DocumentStatus.QUARANTINED,
        tags_json=tags,
        created_by_user_id=actor_user_id,
    )
    db.add(document)
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document",
        entity_id=document.id,
        action="document.created",
        payload={"document_type": document_type.value, "tag_count": len(tags)},
        request_id=request_id,
    )
    return document


def request_document_upload(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    organization_id: UUID,
    actor_user_id: UUID,
    document_id: UUID,
    source_filename: str,
    content_type: str,
    size_bytes: int,
    expected_sha256: str | None,
    request_id: str | None,
) -> UploadRequest:
    document = get_document(db, organization_id=organization_id, document_id=document_id)
    if size_bytes <= 0:
        raise DocumentSecurityValidationError("La taille déclarée du fichier doit être positive.")
    if expected_sha256 is not None and not is_sha256(expected_sha256):
        raise DocumentSecurityValidationError("L’empreinte SHA-256 attendue est invalide.")
    expected_sha256 = expected_sha256.lower() if expected_sha256 else None
    safe_filename = sanitize_filename(source_filename)
    expected_type = expected_content_type(safe_filename)
    normalized_content_type = normalize_content_type(content_type)
    if normalized_content_type == "image/jpg":
        normalized_content_type = "image/jpeg"
    if normalized_content_type != expected_type:
        raise DocumentSecurityValidationError("Le type MIME déclaré doit correspondre à l’extension autorisée.")
    if size_bytes > settings.max_upload_bytes:
        raise DocumentSecurityValidationError(f"Fichier trop volumineux: maximum {settings.max_upload_bytes} octets.")

    upload_id = uuid4()
    quarantine_key = f"quarantine/{organization_id}/{document.id}/{upload_id}/source"
    expires_at = utcnow() + timedelta(seconds=settings.document_upload_ttl_seconds)
    upload = DocumentUpload(
        id=upload_id,
        organization_id=organization_id,
        document_id=document.id,
        requested_by_user_id=actor_user_id,
        quarantine_storage_key=quarantine_key,
        source_filename=safe_filename,
        declared_content_type=normalized_content_type,
        declared_size_bytes=size_bytes,
        expected_sha256=expected_sha256,
        status=DocumentUploadStatus.PENDING_UPLOAD,
        expires_at=expires_at,
    )
    db.add(upload)
    db.flush()
    upload_form = storage.create_presigned_upload(
        bucket=settings.document_quarantine_bucket,
        key=quarantine_key,
        content_type=normalized_content_type,
        max_size_bytes=size_bytes,
        expires_in_seconds=settings.document_upload_ttl_seconds,
    )
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document_upload",
        entity_id=upload.id,
        action="document.upload_requested",
        payload={
            "document_id": str(document.id),
            "declared_content_type": normalized_content_type,
            "declared_size_bytes": size_bytes,
        },
        request_id=request_id,
    )
    return UploadRequest(upload=upload, upload_form=upload_form)


def _get_upload(db: Session, *, organization_id: UUID, upload_id: UUID, lock: bool = False) -> DocumentUpload:
    statement = select(DocumentUpload).where(
        DocumentUpload.organization_id == organization_id,
        DocumentUpload.id == upload_id,
    )
    if lock:
        statement = statement.with_for_update()
    upload = db.scalar(statement)
    if upload is None:
        raise DocumentNotFoundError("Import documentaire introuvable ou inaccessible.")
    return upload


def _best_effort_delete(storage: ObjectStorage, *, bucket: str, key: str) -> bool:
    try:
        storage.delete(bucket=bucket, key=key)
        return True
    except ObjectStorageError:
        # The object remains in an inaccessible quarantine bucket. The audit
        # event marks that lifecycle cleanup remains necessary.
        return False


def _record_rejection(
    db: Session,
    *,
    upload: DocumentUpload,
    actor_user_id: UUID,
    organization_id: UUID,
    reason: str,
    request_id: str | None,
    storage: ObjectStorage,
    settings: Settings,
) -> UploadFinalization:
    upload.status = DocumentUploadStatus.REJECTED
    upload.scan_completed_at = utcnow()
    upload.scan_error_code = reason
    removed = _best_effort_delete(
        storage,
        bucket=settings.document_quarantine_bucket,
        key=upload.quarantine_storage_key,
    )
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document_upload",
        entity_id=upload.id,
        action="document.upload_rejected",
        payload={
            "document_id": str(upload.document_id),
            "reason": reason,
            "quarantine_deleted": removed,
            "uploaded_sha256": upload.uploaded_sha256,
        },
        request_id=request_id,
    )
    document = get_document(db, organization_id=organization_id, document_id=upload.document_id)
    if document.status == DocumentStatus.QUARANTINED:
        document.status = DocumentStatus.FAILED
    db.flush()
    return UploadFinalization(
        document=document,
        upload=upload,
        version=None,
        status_code=422,
        detail="Le fichier a été rejeté par les contrôles de sécurité.",
    )


def _record_scan_failure(
    db: Session,
    *,
    upload: DocumentUpload,
    actor_user_id: UUID,
    organization_id: UUID,
    reason: str,
    request_id: str | None,
) -> UploadFinalization:
    upload.status = DocumentUploadStatus.FAILED
    upload.scan_completed_at = utcnow()
    upload.scan_error_code = reason
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document_upload",
        entity_id=upload.id,
        action="document.upload_scan_failed",
        payload={"document_id": str(upload.document_id), "reason": reason},
        request_id=request_id,
    )
    document = get_document(db, organization_id=organization_id, document_id=upload.document_id)
    db.flush()
    return UploadFinalization(
        document=document,
        upload=upload,
        version=None,
        status_code=503,
        detail="Le contrôle antivirus est indisponible; le fichier reste en quarantaine.",
    )


def complete_document_upload(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    organization_id: UUID,
    actor_user_id: UUID,
    upload_id: UUID,
    request_id: str | None,
) -> UploadFinalization:
    upload = _get_upload(db, organization_id=organization_id, upload_id=upload_id, lock=True)
    document = get_document(db, organization_id=organization_id, document_id=upload.document_id, lock=True)

    if upload.status == DocumentUploadStatus.CLEAN and upload.finalized_document_version_id:
        version = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.id == upload.finalized_document_version_id,
            )
        )
        return UploadFinalization(document=document, upload=upload, version=version, status_code=200)
    if upload.status in {DocumentUploadStatus.REJECTED, DocumentUploadStatus.EXPIRED}:
        raise DocumentConflictError("Cet import ne peut plus être finalisé.")
    if upload.status == DocumentUploadStatus.SCANNING:
        raise DocumentConflictError("Cet import est déjà en cours de contrôle.")

    if _as_utc(upload.expires_at) <= utcnow():
        upload.status = DocumentUploadStatus.EXPIRED
        removed = _best_effort_delete(
            storage,
            bucket=settings.document_quarantine_bucket,
            key=upload.quarantine_storage_key,
        )
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            entity_type="document_upload",
            entity_id=upload.id,
            action="document.upload_expired",
            payload={"document_id": str(document.id), "quarantine_deleted": removed},
            request_id=request_id,
        )
        db.flush()
        return UploadFinalization(
            document=document,
            upload=upload,
            version=None,
            status_code=410,
            detail="Le lien d’import a expiré; créez une nouvelle version.",
        )

    try:
        metadata = storage.head(bucket=settings.document_quarantine_bucket, key=upload.quarantine_storage_key)
        if metadata.size_bytes != upload.declared_size_bytes or metadata.size_bytes > settings.max_upload_bytes:
            return _record_rejection(
                db,
                upload=upload,
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                reason="size_mismatch",
                request_id=request_id,
                storage=storage,
                settings=settings,
            )
        if normalize_content_type(metadata.content_type) != upload.declared_content_type:
            return _record_rejection(
                db,
                upload=upload,
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                reason="content_type_mismatch",
                request_id=request_id,
                storage=storage,
                settings=settings,
            )
        payload = storage.read_bytes(
            bucket=settings.document_quarantine_bucket,
            key=upload.quarantine_storage_key,
            max_size_bytes=settings.max_upload_bytes,
        )
    except ObjectNotFoundError:
        return _record_rejection(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="object_missing",
            request_id=request_id,
            storage=storage,
            settings=settings,
        )
    except (ObjectStorageError, ObjectStorageUnavailableError):
        return _record_scan_failure(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="storage_unavailable",
            request_id=request_id,
        )

    # A conditional server-side copy below binds the clean promotion to this
    # exact object revision. Without an entity tag a browser could replace the
    # quarantine object after it has been scanned but before it is copied.
    if not metadata.etag:
        return _record_scan_failure(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="source_etag_unavailable",
            request_id=request_id,
        )

    try:
        inspected = inspect_document_bytes(
            payload,
            filename=upload.source_filename,
            declared_content_type=upload.declared_content_type,
            max_size_bytes=settings.max_upload_bytes,
        )
    except DocumentSecurityValidationError:
        return _record_rejection(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="file_signature_invalid",
            request_id=request_id,
            storage=storage,
            settings=settings,
        )
    upload.uploaded_content_type = inspected.content_type
    upload.uploaded_size_bytes = inspected.size_bytes
    upload.uploaded_sha256 = inspected.sha256
    upload.object_etag = metadata.etag
    if upload.expected_sha256 and upload.expected_sha256 != inspected.sha256:
        return _record_rejection(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="checksum_mismatch",
            request_id=request_id,
            storage=storage,
            settings=settings,
        )

    upload.status = DocumentUploadStatus.SCANNING
    upload.scan_started_at = utcnow()
    db.flush()
    try:
        scan = scanner.scan(payload)
    except MalwareScannerUnavailableError:
        return _record_scan_failure(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="scanner_unavailable",
            request_id=request_id,
        )
    except MalwareScanError:
        return _record_scan_failure(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="scanner_error",
            request_id=request_id,
        )

    upload.scan_engine = scan.engine
    upload.scan_signature_version = scan.signature_version
    upload.scan_completed_at = utcnow()
    if not scan.is_clean:
        return _record_rejection(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="malware_detected",
            request_id=request_id,
            storage=storage,
            settings=settings,
        )

    next_version_number = int(
        db.scalar(
            select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.document_id == document.id,
            )
        )
        or 0
    ) + 1
    version_id = uuid4()
    clean_key = f"clean/{organization_id}/{document.id}/versions/{version_id}/source"
    try:
        storage.copy(
            source_bucket=settings.document_quarantine_bucket,
            source_key=upload.quarantine_storage_key,
            source_etag=metadata.etag,
            destination_bucket=settings.document_clean_bucket,
            destination_key=clean_key,
            content_type=inspected.content_type,
        )
    except ObjectStorageError:
        return _record_scan_failure(
            db,
            upload=upload,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            reason="promotion_failed",
            request_id=request_id,
        )

    version = DocumentVersion(
        id=version_id,
        organization_id=organization_id,
        document_id=document.id,
        version_number=next_version_number,
        source_filename=inspected.source_filename,
        content_type=inspected.content_type,
        storage_key=clean_key,
        sha256=inspected.sha256,
        size_bytes=inspected.size_bytes,
        extraction_status=ExtractionStatus.PENDING,
    )
    db.add(version)
    upload.status = DocumentUploadStatus.CLEAN
    upload.finalized_document_version_id = version.id
    upload.scan_error_code = None
    if document.status in {DocumentStatus.QUARANTINED, DocumentStatus.FAILED}:
        document.status = DocumentStatus.UPLOADED
    try:
        db.flush()
        removed = _best_effort_delete(
            storage,
            bucket=settings.document_quarantine_bucket,
            key=upload.quarantine_storage_key,
        )
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            entity_type="document_version",
            entity_id=version.id,
            action="document.upload_clean",
            payload={
                "document_id": str(document.id),
                "upload_id": str(upload.id),
                "version_number": version.version_number,
                "content_type": version.content_type,
                "size_bytes": version.size_bytes,
                "sha256": version.sha256,
                "scanner": scan.engine,
                "quarantine_deleted": removed,
            },
            request_id=request_id,
        )
        enqueue_document_extraction(
            db,
            settings=settings,
            document=document,
            version=version,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        db.flush()
    except Exception:
        # Object storage and SQL cannot share a transaction. If persistence or
        # its audit event fails before the service returns, compensate by
        # deleting the just-created clean object rather than leaving an
        # untracked downloadable binary. A post-commit reconciliation job is
        # still needed for crash/commit-window recovery at scale.
        _best_effort_delete(storage, bucket=settings.document_clean_bucket, key=clean_key)
        raise
    return UploadFinalization(document=document, upload=upload, version=version, status_code=201)


def get_document_version(db: Session, *, organization_id: UUID, version_id: UUID) -> DocumentVersion:
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.id == version_id,
        )
    )
    if version is None:
        raise DocumentNotFoundError("Version documentaire introuvable ou inaccessible.")
    return version


def create_document_download(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    organization_id: UUID,
    actor_user_id: UUID,
    version_id: UUID,
    request_id: str | None,
) -> tuple[DocumentVersion, str, datetime]:
    version = get_document_version(db, organization_id=organization_id, version_id=version_id)
    if version.extraction_status == ExtractionStatus.FAILED:
        # A failed extraction is unrelated to malware scanning; the original
        # clean evidence remains downloadable for human review.
        pass
    expires_at = utcnow() + timedelta(seconds=settings.document_download_ttl_seconds)
    url = storage.create_presigned_download(
        bucket=settings.document_clean_bucket,
        key=version.storage_key,
        download_filename=version.source_filename,
        expires_in_seconds=settings.document_download_ttl_seconds,
    )
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document_version",
        entity_id=version.id,
        action="document.download_url_issued",
        payload={"expires_at": expires_at.isoformat()},
        request_id=request_id,
    )
    return version, url, expires_at


def document_detail(db: Session, *, organization_id: UUID, document_id: UUID) -> tuple[Document, list[DocumentVersion], list[DocumentUpload]]:
    document = get_document(db, organization_id=organization_id, document_id=document_id)
    versions = list(
        db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.organization_id == organization_id, DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number.desc())
        ).all()
    )
    uploads = list(
        db.scalars(
            select(DocumentUpload)
            .where(DocumentUpload.organization_id == organization_id, DocumentUpload.document_id == document.id)
            .order_by(DocumentUpload.created_at.desc())
        ).all()
    )
    return document, versions, uploads
