"""A worker name is not a unique lease: fence every attempt, including retries."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.extraction import (
    DocumentExtractionConflictError,
    _publish_extraction_result,
    process_claimed_document_extraction,
    record_document_extraction_failure,
)
from app.engine.document_extractor import DocumentTextExtractor
from app.identity.service import set_db_request_context
from app.models.domain import DocumentExtractionJob, DocumentExtractionJobStatus
from tests.auth_support import authenticate_client
from tests.test_secure_documents import (
    TEXT_BYTES, _install_document_fakes, _create_document, _request_upload, _claim_extraction_job,
)


@pytest.mark.parametrize("operation", ["process", "publish", "failure"])
def test_reclaimed_attempt_with_same_worker_name_rejects_old_claim(operation):
    app, storage, _, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client)
            instruction = _request_upload(client, document["id"], filename="lease.txt", payload=TEXT_BYTES)
            storage.upload_latest(payload=TEXT_BYTES)
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201, completed.text
            old_claim = _claim_extraction_job(organization_id=identity.organization_id, user_id=identity.user_id)
            with SessionLocal() as db:
                job = db.get(DocumentExtractionJob, old_claim.id)
                job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
            new_claim = _claim_extraction_job(organization_id=identity.organization_id, user_id=identity.user_id)
            assert new_claim.worker_id == old_claim.worker_id
            assert new_claim.attempt_count == old_claim.attempt_count + 1

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                args = dict(db=db, settings=settings, claim=old_claim)
                if operation == "failure":
                    assert record_document_extraction_failure(**args, error_code="stale_failure", retryable=False) is False
                else:
                    with pytest.raises(DocumentExtractionConflictError):
                        if operation == "process":
                            process_claimed_document_extraction(**args, storage=storage)
                        else:
                            result = DocumentTextExtractor().extract_result("lease.txt", "text/plain", TEXT_BYTES)
                            _publish_extraction_result(**args, storage=storage, result=result)
                db.commit()
            with SessionLocal() as db:
                job = db.get(DocumentExtractionJob, new_claim.id)
                assert job.status == DocumentExtractionJobStatus.RUNNING
                assert job.attempt_count == new_claim.attempt_count
                assert job.error_code is None
            assert not storage.put_requests
            # The current attempt still completes; replay cannot publish twice.
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                process_claimed_document_extraction(db, settings=settings, storage=storage, claim=new_claim)
                db.commit()
            assert len(storage.put_requests) == 1
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                with pytest.raises(DocumentExtractionConflictError):
                    process_claimed_document_extraction(db, settings=settings, storage=storage, claim=new_claim)
            assert len(storage.put_requests) == 1
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner
