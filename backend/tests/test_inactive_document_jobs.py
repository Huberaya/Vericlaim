from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.extraction import claim_next_document_extraction_job
from app.identity.service import set_db_request_context
from app.models.domain import Document, DocumentStatus, DocumentExtractionJob, DocumentExtractionJobStatus
from tests.auth_support import authenticate_client
from tests.test_secure_documents import _install_document_fakes, _create_document, _request_upload, _claim_extraction_job


@pytest.mark.parametrize("inactive", ["deleted", "archived"])
@pytest.mark.parametrize("expired", [False, True])
def test_inactive_document_does_not_poison_queued_or_expired_jobs(inactive, expired):
    app, storage, _, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            doc = _create_document(client)
            instruction = _request_upload(client, doc["id"], filename="inactive.txt", payload=b"text")
            storage.upload_latest(payload=b"text")
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201
            version_id = UUID(completed.json()["version"]["id"])
            if expired:
                _claim_extraction_job(organization_id=identity.organization_id, user_id=identity.user_id)
            with SessionLocal() as db:
                document = db.get(Document, UUID(doc["id"]))
                document.status = DocumentStatus.DELETED if inactive == "deleted" else DocumentStatus.ARCHIVED
                if inactive == "deleted":
                    document.deleted_at = datetime.now(timezone.utc)
                job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.document_version_id == version_id))
                if expired:
                    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                assert claim_next_document_extraction_job(db, settings=settings, organization_id=identity.organization_id, worker_id="new") is None
                db.commit()
            with SessionLocal() as db:
                job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.document_version_id == version_id))
                assert job.status == DocumentExtractionJobStatus.FAILED
                assert job.error_code == "document_inactive"
                assert job.lease_expires_at is None
            assert not storage.put_requests
            # A failed inactive job must not permanently block this tenant.
            next_doc = _create_document(client)
            next_upload = _request_upload(client, next_doc["id"], filename="next.txt", payload=b"next")
            storage.upload_latest(payload=b"next")
            assert client.post(f"/api/v1/document-uploads/{next_upload['upload']['id']}/complete").status_code == 201
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                assert claim_next_document_extraction_job(db, settings=settings, organization_id=identity.organization_id, worker_id="healthy") is not None
                db.commit()
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


@pytest.mark.parametrize("inactive", ["deleted", "archived"])
def test_document_deactivated_during_extraction_cannot_publish(inactive, monkeypatch):
    from app.documents.extraction import DocumentExtractionTerminalError, process_claimed_document_extraction, record_document_extraction_failure
    from app.engine.document_extractor import DocumentTextExtractor
    app, storage, _, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            doc = _create_document(client)
            instruction = _request_upload(client, doc["id"], filename="race.txt", payload=b"text")
            storage.upload_latest(payload=b"text")
            assert client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete").status_code == 201
            claim = _claim_extraction_job(organization_id=identity.organization_id, user_id=identity.user_id)
            original = DocumentTextExtractor.extract_result

            def deactivate(extractor, *args, **kwargs):
                result = original(extractor, *args, **kwargs)
                with SessionLocal() as other:
                    document = other.get(Document, UUID(doc["id"]))
                    document.status = DocumentStatus.DELETED if inactive == "deleted" else DocumentStatus.ARCHIVED
                    if inactive == "deleted":
                        document.deleted_at = datetime.now(timezone.utc)
                    other.commit()
                return result

            monkeypatch.setattr(DocumentTextExtractor, "extract_result", deactivate)
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                with pytest.raises(DocumentExtractionTerminalError) as error:
                    process_claimed_document_extraction(db, settings=settings, storage=storage, claim=claim)
                assert error.value.code == "document_inactive"
                db.rollback()
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                record_document_extraction_failure(db, settings=settings, claim=claim, error_code="document_inactive", retryable=False)
                db.commit()
                assert db.get(DocumentExtractionJob, claim.id).status == DocumentExtractionJobStatus.FAILED
            assert not storage.put_requests
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner
