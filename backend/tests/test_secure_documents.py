from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.extraction import (
    DocumentExtractionConflictError,
    DocumentExtractionTerminalError,
    claim_next_document_extraction_job,
    process_claimed_document_extraction,
    record_document_extraction_failure,
)
from app.documents.security import (
    DocumentSecurityValidationError,
    inspect_document_bytes,
)
from app.documents.storage import S3ObjectStorage
from app.engine.document_extractor import DocumentTextExtractor
from app.identity.service import set_db_request_context
from app.models.domain import (
    AuditEvent,
    DocumentExtractionJob,
    DocumentExtractionJobStatus,
    DocumentSegment,
    DocumentUpload,
    DocumentVersion,
    ExtractionStatus,
)
from app.workers.document_extraction_worker import DocumentExtractionWorker
from tests.auth_support import authenticate_client
from tests.document_support import FakeObjectStorage, FakeScanner, StoredObject

PDF_BYTES = b"%PDF-1.7\n% secure-document-test\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
TEXT_BYTES = "Première allégation environnementale.\n\nDeuxième passage vérifiable.".encode()
CONTEXT = {
    "as_of_date": "2026-09-24",
    "jurisdiction": "FR",
    "surface": "packaging",
    "consumer_facing": True,
    "product_identifier": "SKU-DOC-1",
}


def _create_document(client: TestClient, *, title: str = "Fiche fournisseur") -> dict[str, object]:
    response = client.post(
        "/api/v1/documents",
        json={"title": title, "document_type": "supplier_declaration", "tags": ["fournisseur", "test"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _request_upload(client: TestClient, document_id: str, *, filename: str = "fiche.pdf", payload: bytes = PDF_BYTES) -> dict[str, object]:
    response = client.post(
        f"/api/v1/documents/{document_id}/versions",
        json={
            "source_filename": filename,
            "content_type": "application/pdf" if filename.endswith(".pdf") else "text/plain",
            "size_bytes": len(payload),
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _install_document_fakes():
    from app.main import app

    old_storage = app.state.document_storage
    old_scanner = app.state.document_scanner
    storage = FakeObjectStorage()
    scanner = FakeScanner()
    app.state.document_storage = storage
    app.state.document_scanner = scanner
    return app, storage, scanner, old_storage, old_scanner


def _claim_extraction_job(*, organization_id: UUID, user_id: UUID):
    with SessionLocal() as db:
        set_db_request_context(db, user_id=user_id, organization_id=organization_id)
        claim = claim_next_document_extraction_job(
            db,
            settings=settings,
            organization_id=organization_id,
            worker_id="pytest-document-worker",
        )
        assert claim is not None
        db.commit()
    return claim


def test_security_boundary_requires_coherent_extension_mime_and_signature():
    inspected = inspect_document_bytes(
        PDF_BYTES,
        filename="preuve.pdf",
        declared_content_type="application/pdf; charset=binary",
        max_size_bytes=1024,
    )
    assert inspected.content_type == "application/pdf"
    assert inspected.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()

    try:
        inspect_document_bytes(
            PDF_BYTES,
            filename="preuve.txt",
            declared_content_type="text/plain",
            max_size_bytes=1024,
        )
    except DocumentSecurityValidationError as exc:
        assert "MIME" in str(exc)
    else:
        raise AssertionError("a PDF disguised as text must not reach a parser")

    try:
        inspect_document_bytes(
            PDF_BYTES,
            filename='preuve".pdf',
            declared_content_type="application/pdf",
            max_size_bytes=1024,
        )
    except DocumentSecurityValidationError as exc:
        assert "guillemet" in str(exc)
    else:
        raise AssertionError("a header-shaping filename must be rejected")

    try:
        inspect_document_bytes(
            b"\xff\xfeinvalid",
            filename="preuve.txt",
            declared_content_type="text/plain",
            max_size_bytes=1024,
        )
    except DocumentSecurityValidationError as exc:
        assert "UTF-8" in str(exc)
    else:
        raise AssertionError("non UTF-8 text must be rejected")


def test_s3_signed_capabilities_bind_server_key_size_encryption_and_source_etag():
    storage = S3ObjectStorage(
        endpoint_url="https://internal-storage.example.test",
        public_endpoint_url="https://uploads.example.test",
        region_name="eu-west-3",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
        sse_mode="aes256",
        sse_kms_key_id=None,
    )
    upload = storage.create_presigned_upload(
        bucket="quarantine-private",
        key="quarantine/server-generated-id/source",
        content_type="application/pdf",
        max_size_bytes=1024,
        expires_in_seconds=60,
    )
    assert upload.url.startswith("https://uploads.example.test/")
    assert upload.fields["key"] == "quarantine/server-generated-id/source"
    assert upload.fields["x-amz-server-side-encryption"] == "AES256"
    policy = json.loads(base64.b64decode(upload.fields["policy"]).decode("utf-8"))
    assert {"key": "quarantine/server-generated-id/source"} in policy["conditions"]
    assert {"Content-Type": "application/pdf"} in policy["conditions"]
    assert ["content-length-range", 1, 1024] in policy["conditions"]
    assert {"x-amz-server-side-encryption": "AES256"} in policy["conditions"]

    class RecordingClient:
        kwargs: dict[str, object] | None = None

        def copy_object(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    recording_client = RecordingClient()
    storage._client = recording_client  # type: ignore[attr-defined]  # Explicit adapter-boundary contract test.
    storage.copy(
        source_bucket="quarantine-private",
        source_key="quarantine/server-generated-id/source",
        source_etag='"etag-before-scan"',
        destination_bucket="clean-private",
        destination_key="clean/server-generated-id/source",
        content_type="application/pdf",
    )
    assert recording_client.kwargs is not None
    assert recording_client.kwargs["CopySourceIfMatch"] == '"etag-before-scan"'
    assert recording_client.kwargs["ServerSideEncryption"] == "AES256"


def test_document_upload_promotes_only_after_clean_scan_and_audits():
    app, storage, scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client)
            instruction = _request_upload(client, str(document["id"]))
            assert instruction["upload_method"] == "POST"
            assert "upload_url" in instruction and "upload_fields" in instruction
            assert "quarantine_storage_key" not in instruction

            storage.upload_latest(payload=PDF_BYTES)
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201, completed.text
            body = completed.json()
            assert body["upload"]["status"] == "clean"
            assert body["upload"]["uploaded_sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
            assert body["version"]["version_number"] == 1
            assert "storage_key" not in body["version"]
            assert "quarantine_storage_key" not in body["upload"]
            assert scanner.scanned_payloads == [PDF_BYTES]
            assert len(storage.copy_requests) == 1
            # The quarantine source is deleted after a successful promotion.
            assert all(bucket != "vericlaim-quarantine" for bucket, _ in storage.objects)
            assert any(bucket == "vericlaim-documents" for bucket, _ in storage.objects)

            # Completion is idempotent and cannot create a second immutable version.
            repeated = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["version"]["id"] == body["version"]["id"]
            assert len(storage.copy_requests) == 1

            detail = client.get(f"/api/v1/documents/{document['id']}")
            assert detail.status_code == 200
            detail_body = detail.json()
            assert len(detail_body["versions"]) == 1
            serialized_detail = json.dumps(detail_body)
            assert "storage_key" not in serialized_detail
            assert "extracted_text_storage_key" not in serialized_detail
            assert "quarantine_storage_key" not in serialized_detail

            download = client.post(f"/api/v1/document-versions/{body['version']['id']}/download-url")
            assert download.status_code == 200, download.text
            assert download.json()["url"] == "https://downloads.invalid/vericlaim-capability"
            assert storage.download_requests[0][0] == "vericlaim-documents"

            with SessionLocal() as db:
                version = db.scalar(select(DocumentVersion).where(DocumentVersion.id == UUID(body["version"]["id"])))
                assert version is not None
                events = list(
                    db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.organization_id == identity.organization_id,
                            AuditEvent.entity_id == version.id,
                        )
                    )
                )
                assert {event.action for event in events} >= {"document.upload_clean", "document.download_url_issued"}
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_document_rejection_never_promotes_malware_or_mismatched_bytes():
    app, storage, scanner, old_storage, old_scanner = _install_document_fakes()
    scanner.clean = False
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Certificat à contrôler")
            instruction = _request_upload(client, str(document["id"]))
            storage.upload_latest(payload=PDF_BYTES)

            rejected = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["upload"]["status"] == "rejected"
            assert rejected.json()["version"] is None
            assert storage.copy_requests == []
            assert storage.objects == {}

            # A mismatching magic signature is rejected before scanner/promotion.
            scanner.clean = True
            second = _request_upload(client, str(document["id"]), payload=PDF_BYTES)
            storage.upload_latest(payload=b"X" * len(PDF_BYTES), content_type="application/pdf")
            invalid = client.post(f"/api/v1/document-uploads/{second['upload']['id']}/complete")
            assert invalid.status_code == 422, invalid.text
            assert invalid.json()["upload"]["scan_error_code"] == "file_signature_invalid"
            assert storage.copy_requests == []
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_document_scanner_or_storage_failure_is_fail_closed_and_expired_intents_cannot_promote():
    app, storage, scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Dossier à contrôler")
            instruction = _request_upload(client, str(document["id"]))
            storage.upload_latest(payload=PDF_BYTES)
            scanner.available = False
            unavailable = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert unavailable.status_code == 503, unavailable.text
            assert unavailable.json()["upload"]["status"] == "failed"
            assert storage.copy_requests == []

            scanner.available = True
            expired_instruction = _request_upload(client, str(document["id"]))
            storage.upload_latest(payload=PDF_BYTES)
            with SessionLocal() as db:
                upload = db.scalar(select(DocumentUpload).where(DocumentUpload.id == UUID(expired_instruction["upload"]["id"])))
                assert upload is not None
                assert upload.organization_id == identity.organization_id
                upload.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
            expired = client.post(f"/api/v1/document-uploads/{expired_instruction['upload']['id']}/complete")
            assert expired.status_code == 410, expired.text
            assert expired.json()["upload"]["status"] == "expired"
            assert storage.copy_requests == []
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_quarantine_object_replacement_after_scan_cannot_be_promoted():
    app, storage, scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Pièce avec protection TOCTOU")
            instruction = _request_upload(client, str(document["id"]))
            bucket, key = storage.upload_latest(payload=PDF_BYTES)

            def replace_after_scan(_: bytes) -> None:
                storage.objects[(bucket, key)] = StoredObject(
                    payload=b"Z" * len(PDF_BYTES),
                    content_type="application/pdf",
                    etag="changed-after-scan",
                )

            scanner.on_scan = replace_after_scan
            response = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert response.status_code == 503, response.text
            assert response.json()["upload"]["status"] == "failed"
            assert response.json()["upload"]["scan_error_code"] == "promotion_failed"
            assert storage.copy_requests == []
            assert all(bucket_name != "vericlaim-documents" for bucket_name, _ in storage.objects)
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_document_routes_enforce_rbac_csrf_and_tenant_isolation():
    app, _storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as owner_client, TestClient(app) as foreign_client, TestClient(app) as viewer_client:
            owner = authenticate_client(owner_client, role_code="owner")
            foreign = authenticate_client(foreign_client, role_code="analyst")
            authenticate_client(viewer_client, role_code="viewer")
            document = _create_document(owner_client, title="Preuve propriétaire")

            assert foreign_client.get(f"/api/v1/documents/{document['id']}").status_code == 404
            assert foreign_client.post(
                f"/api/v1/documents/{document['id']}/versions",
                json={"source_filename": "fiche.pdf", "content_type": "application/pdf", "size_bytes": len(PDF_BYTES)},
            ).status_code == 404
            assert viewer_client.get(f"/api/v1/documents/{document['id']}").status_code == 404
            viewer_create = viewer_client.post("/api/v1/documents", json={"title": "Non autorisé"})
            assert viewer_create.status_code == 403

            owner_client.headers.pop("X-CSRF-Token", None)
            csrf_denied = owner_client.post("/api/v1/documents", json={"title": "Sans CSRF"})
            assert csrf_denied.status_code == 403
            assert "CSRF" in csrf_denied.json()["detail"]
            assert owner.organization_id != foreign.organization_id
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_durable_text_extraction_enqueues_worker_job_persists_segments_and_marks_document_ready(monkeypatch):
    app, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Déclaration OCR durable")
            instruction = _request_upload(
                client,
                str(document["id"]),
                filename="declaration.txt",
                payload=TEXT_BYTES,
            )
            storage.upload_latest(payload=TEXT_BYTES)
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201, completed.text
            version_id = UUID(completed.json()["version"]["id"])
            assert completed.json()["version"]["extraction_status"] == "pending"
            assert completed.json()["version"]["extraction_job"]["status"] == "queued"
            assert completed.json()["document"]["status"] == "processing"

            worker = DocumentExtractionWorker(
                settings=settings,
                storage=storage,
                session_factory=SessionLocal,
                worker_id="pytest-document-worker",
            )
            # Other tests may leave jobs for distinct organisations in the
            # shared in-memory fixture. Keep this worker-run deterministic while
            # still exercising the real tenant-aware claim/process path.
            monkeypatch.setattr(worker, "_active_organization_ids", lambda: [identity.organization_id])
            assert worker.run_once() is True

            with SessionLocal() as db:
                version = db.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id))
                job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.document_version_id == version_id))
                assert version is not None and job is not None
                assert version.extraction_status == ExtractionStatus.COMPLETED
                assert job.status == DocumentExtractionJobStatus.COMPLETED
                assert version.extracted_text_storage_key is not None
                assert storage.objects[("vericlaim-documents", version.extracted_text_storage_key)].payload == (
                    "Première allégation environnementale.\n\nDeuxième passage vérifiable.".encode()
                )
                assert db.scalar(
                    select(func.count()).select_from(DocumentSegment).where(DocumentSegment.document_version_id == version_id)
                ) == 2
            assert storage.put_requests
            assert storage.put_requests[0][0] == "vericlaim-documents"
            assert storage.put_requests[0][1].startswith(f"derived/{identity.organization_id}/")

            segments_response = client.get(f"/api/v1/document-versions/{version_id}/segments")
            assert segments_response.status_code == 200, segments_response.text
            segments_body = segments_response.json()
            assert segments_body["version"]["extraction_status"] == "completed"
            assert [segment["sequence_number"] for segment in segments_body["segments"]] == [0, 1]
            assert [segment["page_number"] for segment in segments_body["segments"]] == [1, 1]
            assert [segment["start_offset"] for segment in segments_body["segments"]] == [0, 39]
            assert all(segment["source_sha256"] == hashlib.sha256(TEXT_BYTES).hexdigest() for segment in segments_body["segments"])

            with TestClient(app) as foreign_client:
                authenticate_client(foreign_client, role_code="analyst")
                assert foreign_client.get(f"/api/v1/document-versions/{version_id}/segments").status_code == 404
                assert foreign_client.post(f"/api/v1/document-versions/{version_id}/extraction/retry").status_code == 404

            immutable_retry = client.post(f"/api/v1/document-versions/{version_id}/extraction/retry")
            assert immutable_retry.status_code == 409

            detail = client.get(f"/api/v1/documents/{document['id']}")
            assert detail.status_code == 200
            assert detail.json()["document"]["status"] == "ready"
            assert detail.json()["versions"][0]["extraction_job"]["status"] == "completed"

            with SessionLocal() as db:
                version = db.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id))
                assert version is not None
                assert version.extracted_text_sha256 == hashlib.sha256(
                    "Première allégation environnementale.\n\nDeuxième passage vérifiable.".encode()
                ).hexdigest()
                persisted_segments = list(
                    db.scalars(select(DocumentSegment).where(DocumentSegment.document_version_id == version.id)).all()
                )
                assert len(persisted_segments) == 2
                actions = set(
                    db.scalars(
                        select(AuditEvent.action).where(AuditEvent.organization_id == identity.organization_id)
                    ).all()
                )
                assert {"document.extraction_queued", "document.extraction_started", "document.extraction_completed"} <= actions
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_durable_extraction_retries_transient_failures_and_recovers_expired_leases():
    app, _storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Lease et retry durable")
            instruction = _request_upload(
                client,
                str(document["id"]),
                filename="lease.txt",
                payload=TEXT_BYTES,
            )
            app.state.document_storage.upload_latest(payload=TEXT_BYTES)
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201, completed.text
            version_id = UUID(completed.json()["version"]["id"])

            first_claim = _claim_extraction_job(
                organization_id=identity.organization_id,
                user_id=identity.user_id,
            )
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                scheduled = record_document_extraction_failure(
                    db,
                    settings=settings,
                    claim=first_claim,
                    error_code="clean_storage_unavailable",
                    retryable=True,
                )
                assert scheduled is True
                db.commit()

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.document_version_id == version_id))
                version = db.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id))
                assert job is not None and version is not None
                assert job.status == DocumentExtractionJobStatus.QUEUED
                # SQLite test drivers return timezone-naive values for
                # DateTime(timezone=True); production PostgreSQL keeps UTC.
                available_at = job.available_at.replace(tzinfo=timezone.utc)
                assert available_at > datetime.now(timezone.utc)
                assert version.extraction_status == ExtractionStatus.PENDING
                # Make the scheduled retry immediately due without waiting in the test.
                job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()

            second_claim = _claim_extraction_job(
                organization_id=identity.organization_id,
                user_id=identity.user_id,
            )
            assert second_claim.attempt_count == first_claim.attempt_count + 1

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == second_claim.id))
                assert job is not None
                # Simulate a process crash after claiming: the next claimant must
                # recover the expired lease rather than leave the job stranded.
                job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()

            recovered_claim = _claim_extraction_job(
                organization_id=identity.organization_id,
                user_id=identity.user_id,
            )
            assert recovered_claim.attempt_count == second_claim.attempt_count + 1

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                actions = set(
                    db.scalars(
                        select(AuditEvent.action).where(
                            AuditEvent.organization_id == identity.organization_id,
                            AuditEvent.entity_type == "document_extraction_job",
                        )
                    ).all()
                )
                assert "document.extraction_retry_scheduled" in actions
                assert "document.extraction_lease_requeued" in actions
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_durable_extraction_never_publishes_after_losing_its_lease(monkeypatch):
    app, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Lease perdu pendant OCR")
            instruction = _request_upload(
                client,
                str(document["id"]),
                filename="lease-lost.txt",
                payload=TEXT_BYTES,
            )
            storage.upload_latest(payload=TEXT_BYTES)
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201, completed.text
            version_id = UUID(completed.json()["version"]["id"])
            claim = _claim_extraction_job(organization_id=identity.organization_id, user_id=identity.user_id)

            original_extract_result = DocumentTextExtractor.extract_result
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)

                def expire_lease_after_extraction(extractor, filename, content_type, payload):
                    result = original_extract_result(extractor, filename, content_type, payload)
                    job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == claim.id))
                    assert job is not None
                    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    db.flush()
                    return result

                monkeypatch.setattr(DocumentTextExtractor, "extract_result", expire_lease_after_extraction)
                try:
                    process_claimed_document_extraction(
                        db,
                        settings=settings,
                        storage=storage,
                        claim=claim,
                    )
                except DocumentExtractionConflictError:
                    db.rollback()
                else:
                    raise AssertionError("a worker that lost its lease must not publish an extraction")

            assert storage.put_requests == []
            with SessionLocal() as db:
                version = db.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id))
                assert version is not None
                assert version.extraction_status == ExtractionStatus.RUNNING
                assert db.scalar(
                    select(func.count()).select_from(DocumentSegment).where(DocumentSegment.document_version_id == version_id)
                ) == 0
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_durable_extraction_refuses_changed_clean_object_then_allows_audited_manual_retry():
    app, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Intégrité de l’original propre")
            instruction = _request_upload(client, str(document["id"]), filename="preuve.txt", payload=TEXT_BYTES)
            storage.upload_latest(payload=TEXT_BYTES)
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201, completed.text
            version_id = UUID(completed.json()["version"]["id"])

            with SessionLocal() as db:
                version = db.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id))
                assert version is not None
                storage.objects[("vericlaim-documents", version.storage_key)] = StoredObject(
                    payload="Texte propre mais remplacé.".encode(),
                    content_type="text/plain",
                    etag="tampered-clean-etag",
                )

            claim = _claim_extraction_job(organization_id=identity.organization_id, user_id=identity.user_id)
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                try:
                    process_claimed_document_extraction(
                        db,
                        settings=settings,
                        storage=storage,
                        claim=claim,
                    )
                except DocumentExtractionTerminalError as exc:
                    assert exc.code == "source_checksum_mismatch"
                    db.rollback()
                else:
                    raise AssertionError("a changed clean object must never reach the parser")

                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                retried_automatically = record_document_extraction_failure(
                    db,
                    settings=settings,
                    claim=claim,
                    error_code="source_checksum_mismatch",
                    retryable=False,
                )
                assert retried_automatically is False
                db.commit()

            failed = client.get(f"/api/v1/documents/{document['id']}")
            assert failed.status_code == 200
            version_body = failed.json()["versions"][0]
            assert version_body["extraction_status"] == "failed"
            assert version_body["extraction_error_code"] == "source_checksum_mismatch"
            assert failed.json()["document"]["status"] == "failed"
            assert client.get(f"/api/v1/document-versions/{version_id}/segments").json()["segments"] == []

            retry = client.post(f"/api/v1/document-versions/{version_id}/extraction/retry")
            assert retry.status_code == 200, retry.text
            assert retry.json()["version"]["extraction_status"] == "pending"
            assert retry.json()["extraction_job"]["status"] == "queued"
            assert retry.json()["extraction_job"]["max_attempts"] > retry.json()["extraction_job"]["attempt_count"]
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_extractor_produces_stable_page_offsets_without_ocr_for_text_files():
    result = DocumentTextExtractor(max_bytes=1024, max_chars=1024, segment_max_chars=64).extract_result(
        "preuve.txt",
        "text/plain",
        TEXT_BYTES,
    )
    assert result.method == "TEXT_FILE"
    assert result.page_count == 1
    assert result.requires_human_review is False
    assert result.text == "Première allégation environnementale.\n\nDeuxième passage vérifiable."
    assert [(segment.start_offset, segment.end_offset, segment.text) for segment in result.segments] == [
        (0, 37, "Première allégation environnementale."),
        (39, 67, "Deuxième passage vérifiable."),
    ]


def test_extractor_preserves_native_pdf_page_provenance():
    import fitz

    pdf = fitz.open()
    try:
        pdf.new_page().insert_text((72, 72), "Native page one")
        pdf.new_page().insert_text((72, 72), "Native page two")
        payload = pdf.tobytes()
    finally:
        pdf.close()

    result = DocumentTextExtractor(max_bytes=1024 * 1024, max_pages=3, max_chars=1024).extract_result(
        "preuve.pdf",
        "application/pdf",
        payload,
    )
    assert result.method == "PDF_TEXT"
    assert result.page_count == 2
    assert result.requires_human_review is False
    assert [(segment.page_number, segment.text) for segment in result.segments] == [
        (1, "Native page one"),
        (2, "Native page two"),
    ]
    assert result.text[result.segments[1].start_offset : result.segments[1].end_offset] == "Native page two"


def test_extractor_marks_image_ocr_for_human_review(monkeypatch):
    from io import BytesIO

    from PIL import Image

    image_buffer = BytesIO()
    Image.new("RGB", (40, 20), color="white").save(image_buffer, format="PNG")
    extractor = DocumentTextExtractor(max_bytes=1024 * 1024, max_chars=1024)
    monkeypatch.setattr(extractor, "_ocr_image", lambda _image: "Texte OCR à vérifier")

    result = extractor.extract_result("scan.png", "image/png", image_buffer.getvalue())

    assert result.method == "OCR_IMAGE"
    assert result.requires_human_review is True
    assert [(segment.page_number, segment.segment_type, segment.text) for segment in result.segments] == [
        (1, "image_ocr", "Texte OCR à vérifier")
    ]


def test_legacy_multipart_upload_is_scanned_before_document_extraction():
    from app.main import app

    original_scanner = app.state.document_scanner
    scanner = FakeScanner(clean=False)
    app.state.document_scanner = scanner
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            response = client.post(
                "/api/v1/engine/evaluate",
                files={"document": ("malware.txt", b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE", "text/plain")},
                data={"context_json": json.dumps(CONTEXT), "evidence_json": json.dumps({"items": [], "legal_person": True})},
            )
            assert response.status_code == 422, response.text
            assert "rejeté" in response.json()["detail"]
            assert scanner.scanned_payloads == [b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"]
    finally:
        app.state.document_scanner = original_scanner
