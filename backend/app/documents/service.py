"""Transactional service layer for secure, tenant-scoped document ingestion."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.documents.extraction import enqueue_document_extraction
from app.documents.purge import (
    deferred_purge_drain,
    pending_purge_count,
    pending_purges,
    quarantine_prefix,
    schedule_upload_purge,
    trim_pending_purges,
)
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


logger = logging.getLogger("vericlaim.documents")


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


@dataclass(frozen=True)
class DocumentPage:
    """One opaque page of the tenant vault, same cursor shape as the catalogue."""

    items: list[Document]
    next_sort_key: str | None
    next_id: UUID | None


def list_documents(
    db: Session,
    *,
    organization_id: UUID,
    limit: int,
    status_filter: DocumentStatus | None = None,
    document_type: DocumentType | None = None,
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    query: str | None = None,
    after_sort_key: str | None = None,
    after_id: UUID | None = None,
) -> DocumentPage:
    """Return one page of the tenant's document lifecycle.

    The document detail endpoint is addressed by identifier, which left a tenant
    unable to enumerate its own vault: after a page reload the whole import
    history was invisible, and an import that died half-way (quarantined shell,
    no promoted version) could not be found in order to be retried.

    Paging follows the catalogue convention - an opaque cursor over
    ``(lower(title), id)`` - so a page boundary stays stable while documents are
    being imported concurrently. Retired rows stay hidden from every query
    except an explicit ``status=archived`` one: archival is a visibility
    transition rather than an erasure, so a tenant must still be able to prove
    what it retired and when, while the active workflow sees only what is in
    play.
    """
    conditions = [Document.organization_id == organization_id]
    if status_filter is DocumentStatus.ARCHIVED:
        conditions.append(Document.status == DocumentStatus.ARCHIVED)
    else:
        conditions.append(Document.deleted_at.is_(None))
        if status_filter is not None:
            conditions.append(Document.status == status_filter)
    if document_type is not None:
        conditions.append(Document.document_type == document_type)
    if supplier_id is not None:
        conditions.append(Document.supplier_id == supplier_id)
    if product_id is not None:
        conditions.append(Document.product_id == product_id)
    if query:
        pattern = f"%{query.strip().lower()}%"
        conditions.append(func.lower(Document.title).like(pattern))

    sort_key = func.lower(Document.title)
    statement = select(Document).where(*conditions)
    if after_sort_key is not None and after_id is not None:
        statement = statement.where(
            or_(
                sort_key > after_sort_key.lower(),
                and_(sort_key == after_sort_key.lower(), Document.id > after_id),
            )
        )
    rows = list(db.scalars(statement.order_by(sort_key.asc(), Document.id.asc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    return DocumentPage(
        items=page,
        next_sort_key=page[-1].title.lower() if has_more and page else None,
        next_id=page[-1].id if has_more and page else None,
    )


def archive_document(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    document_id: UUID,
    request_id: str | None,
) -> tuple[Document, bool]:
    """Retire a document from the active tenant vault without destroying evidence.

    Archival is the lifecycle transition the model already defined and every
    worker already honoured (``ARCHIVED``/``DELETED`` and ``deleted_at`` are read
    by the extraction state machine to stop touching a retired document), but no
    endpoint could ever set it. Imports abandoned mid-flight therefore had no
    way out of the vault.

    The clean object, its derived text and every audit event are deliberately
    left untouched: this is a visibility transition, not a purge, and no API
    caller can trigger an irreversible storage deletion.
    """
    document = db.scalar(
        select(Document).where(
            Document.organization_id == organization_id,
            Document.id == document_id,
        )
    )
    if document is None:
        raise DocumentNotFoundError("Document introuvable ou inaccessible.")
    if document.deleted_at is not None:
        # Re-archiving is a no-op, not an error: a retried request must not
        # report a failure for a document that is already retired.
        return document, True

    document.deleted_at = utcnow()
    document.status = DocumentStatus.ARCHIVED
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document",
        entity_id=document.id,
        action="document.archived",
        payload={"document_key": document.document_key, "stored_objects_retained": True},
        request_id=request_id,
    )
    db.flush()
    return document, False


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
    """Compensation d'un geste non commité, jamais une preuve de libération.

    Ce helper ne sert qu'a annuler un objet cree par la transaction courante avant son
    commit (copy promotion ratee, rollback possible). Pour tout ce qui doit etre libere
    APRES un commit, le chemin est le registre de purge: ``schedule_upload_purge`` puis
    la passe de reprise qui relit reellement le stockage. Un booleen « supprime » ne vaut
    rien devant S3, qui repond 200 meme quand rien n'est parti.
    """

    try:
        storage.delete(bucket=bucket, key=key)
        return True
    except ObjectStorageError:
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
    # Le rejet doit d'abord devenir durable: supprimer les octets avant le commit qui
    # enregistre le rejet detruirait la seule preuve materialisee d'un verdict, et un
    # rollback laisserait un document refuse sans objet. La libération est donc
    # enregistree dans la MEME transaction et exécutée après le commit.
    purge_state = schedule_upload_purge(
        db,
        storage=storage,
        settings=settings,
        upload=upload,
        organization_id=organization_id,
        reason="rejected",
        requested_at=utcnow(),
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
            # « scheduled » et non « deleted »: a cette heure rien n'a encore ete libere,
            # et l'evenement de fermeture du registre portera l'etat prouve.
            "quarantine_purge": purge_state,
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
        purge_state = schedule_upload_purge(
            db,
            storage=storage,
            settings=settings,
            upload=upload,
            organization_id=organization_id,
            reason="expired",
            requested_at=utcnow(),
        )
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            entity_type="document_upload",
            entity_id=upload.id,
            action="document.upload_expired",
            payload={"document_id": str(document.id), "quarantine_purge": purge_state},
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
            expected_etag=metadata.etag,
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
    # La quarantine ne doit pas survivre a la promotion, mais sa liberation n'est
    # autorisee qu'une fois la version promue durable: le marqueur est pose ici, dans la
    # transaction de la transition, et le geste reel part apres le commit.
    purge_state = schedule_upload_purge(
        db,
        storage=storage,
        settings=settings,
        upload=upload,
        organization_id=organization_id,
        reason="promoted",
        requested_at=utcnow(),
    )
    try:
        db.flush()
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
                "quarantine_purge": purge_state,
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
        # Object storage and SQL cannot share a transaction. If persistence or its
        # audit event fails before this service returns, compensate by deleting the
        # clean object this very transaction created, rather than leaving an untracked
        # downloadable binary. Crash and commit-window recovery is no longer a gap:
        # the purge marker committed with the transition is what the retained-purge
        # pass replays, and the ledger only closes on a re-read of the storage.
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


# A completion request can legitimately still be scanning when its link TTL is
# reached, so an intent is only swept after the whole scan budget has elapsed.
UPLOAD_SWEEP_GRACE_SECONDS = 90

#: Budget de la reprise automatique. Au-dela, le worker arrete de marteler le stockage,
#: mais le marqueur reste pose: l'etat qui autorise une nouvelle tentative ne doit pas
#: dependre du budget d'un demon, et un operateur peut le rouvrir.
PURGE_MAX_ATTEMPTS = 12


@dataclass(frozen=True)
class PurgeRetrySummary:
    """Bilan d'une passe de reprise des liberations que le stockage n'a pas laisse prouver."""

    examined: int = 0
    released: int = 0
    retained: int = 0
    failed_rows: int = 0

    @property
    def changed(self) -> bool:
        return self.released > 0 or self.retained > 0 or self.failed_rows > 0


def _upload_object_prefix(upload: DocumentUpload) -> str:
    return quarantine_prefix(
        organization_id=upload.organization_id,
        document_id=upload.document_id,
        upload_id=upload.id,
    )


def sweep_expired_document_uploads(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    organization_id: UUID,
    limit: int = 25,
) -> int:
    """Close upload intents the client abandoned and queue their quarantine release.

    The presigned POST expires by itself; nothing else did. An intent left in
    ``PENDING_UPLOAD`` (tab closed, network drop) or in ``SCANNING`` (request killed
    mid-scan) stayed open forever, which meant unscanned bytes were kept in the
    quarantine bucket, the tenant was shown a document perpetually in ``QUARANTINED``
    with no way to tell it was dead, and the only transition out of that state
    required the very client call that never happened.

    This is housekeeping, not a verdict: it never promotes, never deletes a clean
    object and never touches an intent that already produced a version. Two properties
    are deliberate:

    - each row runs in its own savepoint, so one unreadable object or one document in
      an unexpected shape cannot roll back the rest of the batch;
    - the quarantine bytes are not destroyed here. The transition, its audit event and
      the purge marker commit together, and the release runs after that commit - an
      abandoned intent whose state did not stick must not lose its only artifact.
    """
    cutoff = utcnow() - timedelta(
        seconds=UPLOAD_SWEEP_GRACE_SECONDS + settings.document_clamav_timeout_seconds
    )
    candidates = list(
        db.scalars(
            select(DocumentUpload)
            .where(
                DocumentUpload.organization_id == organization_id,
                DocumentUpload.status.in_({DocumentUploadStatus.PENDING_UPLOAD, DocumentUploadStatus.SCANNING}),
                DocumentUpload.expires_at <= cutoff,
            )
            .order_by(DocumentUpload.expires_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not candidates:
        return 0

    swept = 0
    with deferred_purge_drain(db):
        for upload in candidates:
            savepoint = db.begin_nested()
            queued_before = pending_purge_count(db)
            try:
                upload.status = DocumentUploadStatus.EXPIRED
                upload.scan_completed_at = utcnow()
                upload.scan_error_code = "upload_abandoned"
                purge_state = schedule_upload_purge(
                    db,
                    storage=storage,
                    settings=settings,
                    upload=upload,
                    organization_id=organization_id,
                    reason="abandoned",
                    requested_at=utcnow(),
                )
                document = db.scalar(
                    select(Document).where(
                        Document.organization_id == organization_id,
                        Document.id == upload.document_id,
                        Document.deleted_at.is_(None),
                    )
                )
                stranded = False
                if document is not None and document.status == DocumentStatus.QUARANTINED:
                    version_count = db.scalar(
                        select(func.count())
                        .select_from(DocumentVersion)
                        .where(
                            DocumentVersion.organization_id == organization_id,
                            DocumentVersion.document_id == document.id,
                        )
                    )
                    if not version_count:
                        # No version was ever promoted, so this row is a failed import
                        # rather than a document awaiting a control.
                        document.status = DocumentStatus.FAILED
                        stranded = True
                append_audit_event(
                    db,
                    organization_id=organization_id,
                    actor_user_id=None,
                    entity_type="document_upload",
                    entity_id=upload.id,
                    action="document.upload_swept",
                    payload={
                        "document_id": str(upload.document_id),
                        "reason": "upload_abandoned",
                        "quarantine_purge": purge_state,
                        "document_marked_failed": stranded,
                    },
                )
                db.flush()
            except Exception:
                savepoint.rollback()
                trim_pending_purges(db, queued_before)
                logger.warning(
                    "Unable to sweep an abandoned document upload; continuing with the next row",
                    extra={"organization_id": str(organization_id), "upload_id": str(upload.id)},
                    exc_info=True,
                )
                continue
            savepoint.commit()
            swept += 1
    return swept


def _record_failed_purge_attempt(db: Session, *, upload_id: UUID) -> None:
    """Compte une tentative qui n'a meme pas pu atteindre le stockage.

    Le geste a eu lieu et a echoue: le compter evite qu'un bucket injoignable consomme
    la file de reprise sans jamais atteindre le budget qui declenche l'escalade vers un
    operateur. Le marqueur, lui, reste pose - c'est lui qui rend la reprise possible.
    """

    with deferred_purge_drain(db):
        fresh = db.get(DocumentUpload, upload_id)
        if fresh is not None:
            fresh.purge_attempts += 1
            fresh.purge_last_error = "storage_error"
            db.flush()


def retry_retained_document_purges(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    organization_id: UUID | None = None,
    limit: int = 25,
) -> PurgeRetrySummary:
    """Rejoue les liberations d'objets que le stockage n'a pas laisse prouver.

    Le registre ne se ferme qu'apres relecture reelle du prefixe: une reponse 200 de
    ``delete_objects`` n'affirme rien. La passe est idempotente - relire un prefixe deja
    vide ne produit aucun geste et referme le registre, ce qui absorbe un double passage,
    un retry manuel ou un worker redemarre; resistente a la concurrence - ``SKIP LOCKED``
    empeche deux workers de disputer la meme ligne; isolante - une ligne en echec est
    enregistree comme toujours retenue et n'arrete pas les suivantes; et honnete - aucune
    ligne ne passe a ``purge_required = false`` sans que la liste complete ait pu etre
    relue vide.
    """
    # A presigned POST remains replayable after completion/rejection. Do not close
    # its ledger (or exhaust its retry budget) until TTL + sweep grace has elapsed;
    # otherwise a replay after initial cleanup recreates an untracked orphan.
    capability_cutoff = utcnow() - timedelta(
        seconds=UPLOAD_SWEEP_GRACE_SECONDS + settings.document_clamav_timeout_seconds
    )
    statement = (
        select(DocumentUpload)
        .where(
            DocumentUpload.purge_required.is_(True),
            DocumentUpload.expires_at <= capability_cutoff,
            DocumentUpload.purge_attempts < PURGE_MAX_ATTEMPTS,
        )
        .order_by(DocumentUpload.purge_requested_at.asc(), DocumentUpload.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    # The worker sweeps and retries in the SAME transaction. Its newly scheduled
    # markers are not durable yet, even after a flush or a released savepoint.
    # Only after_commit may execute these actions; a later pass closes the ledger.
    queued_upload_ids = [action.upload_id for action in pending_purges(db)]
    if queued_upload_ids:
        statement = statement.where(DocumentUpload.id.not_in(queued_upload_ids))
    if organization_id is not None:
        statement = statement.where(DocumentUpload.organization_id == organization_id)

    examined = released = retained = failed = 0
    for upload in list(db.scalars(statement).all()):
        if organization_id is not None and upload.organization_id != organization_id:
            # Defense en profondeur: une ligne d'un autre tenant ne doit jamais etre
            # touchee par cette passe, meme si le filtre appelant avait ete mal cable.
            logger.error(
                "Refusing to purge a document upload outside the requested tenant",
                extra={"organization_id": str(organization_id), "upload_id": str(upload.id)},
            )
            continue
        examined += 1
        try:
            prefix = _upload_object_prefix(upload)
            result = storage.delete_prefix(bucket=settings.document_quarantine_bucket, prefix=prefix)
        except Exception:
            _record_failed_purge_attempt(db, upload_id=upload.id)
            failed += 1
            logger.warning(
                "Retained document purge could not be replayed; the ledger stays open",
                extra={"organization_id": str(upload.organization_id), "upload_id": str(upload.id)},
                exc_info=True,
            )
            continue
        savepoint = db.begin_nested()
        try:
            upload.purge_attempts += 1
            if result.fully_released:
                upload.purge_required = False
                upload.purge_kind = None
                upload.purge_reason = None
                upload.purge_last_error = None
                upload.purge_requested_at = None
                append_audit_event(
                    db,
                    organization_id=upload.organization_id,
                    actor_user_id=None,
                    entity_type="document_upload",
                    entity_id=upload.id,
                    action="document.upload_purge_released",
                    payload={
                        "document_id": str(upload.document_id),
                        "quarantine_purge": result.state,
                        "released_count": len(result.deleted_keys),
                        "listed_before_release": result.listed_keys,
                        "purge_attempts": upload.purge_attempts,
                    },
                )
                released += 1
            else:
                exhausted = upload.purge_attempts >= PURGE_MAX_ATTEMPTS
                upload.purge_last_error = (
                    "purge_attempts_exhausted" if exhausted else (result.error_code or "retained")
                )[:64]
                append_audit_event(
                    db,
                    organization_id=upload.organization_id,
                    actor_user_id=None,
                    entity_type="document_upload",
                    entity_id=upload.id,
                    action="upload_object_purge_retained",
                    payload={
                        "document_id": str(upload.document_id),
                        "prefix": prefix,
                        "retained_count": len(result.retained_keys),
                        "retained_keys_sample": list(result.retained_keys)[:5],
                        "listing_complete": result.listing_complete,
                        "purge_error": result.error_code,
                        "purge_attempts": upload.purge_attempts,
                        "retries_scheduled": not exhausted,
                    },
                )
                retained += 1
            db.flush()
        except Exception:
            savepoint.rollback()
            failed += 1
            logger.warning(
                "Retained document purge could not be recorded; the ledger stays open",
                extra={"organization_id": str(upload.organization_id), "upload_id": str(upload.id)},
                exc_info=True,
            )
            continue
        savepoint.commit()
    return PurgeRetrySummary(examined=examined, released=released, retained=retained, failed_rows=failed)
