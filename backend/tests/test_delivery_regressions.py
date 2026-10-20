"""Operational regressions: real API contract and deterministic lease interleaving."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.analyses.service import (
    AnalysisConflictError, claim_next_analysis_detection_job,
    process_claimed_analysis_detection, record_analysis_detection_failure,
)
from app.core.config import settings
from app.models.domain import AnalysisDetectionJob, AnalysisDetectionJobStatus, AnalysisVersion, AuditEvent
from app.identity.service import set_db_request_context
from tests.auth_support import authenticate_client
from tests.test_secure_documents import _install_document_fakes, _create_document, _request_upload
from tests.test_persistent_analyses import _engine, _seed_extracted_document, _enqueue


def test_upload_instruction_advertises_server_limit_not_declared_size():
    app, storage, _, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            doc = _create_document(client)
            instruction = _request_upload(client, doc["id"], filename="limit.txt", payload=b"text")
            assert instruction["max_size_bytes"] == settings.max_upload_bytes
            # The capability remains restricted to the declared length; it must
            # not permit a large replay just because the API advertises a ceiling.
            assert storage.upload_requests[-1][3] == 4
            response = client.post(f"/api/v1/documents/{doc['id']}/versions", json={
                "source_filename": "huge.txt", "content_type": "text/plain", "size_bytes": settings.max_upload_bytes + 1,
            })
            assert response.status_code == 422
            assert len(storage.upload_requests) == 1
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def claim_job(engine, seed):
    with Session(engine) as db:
        set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
        claim = claim_next_analysis_detection_job(db, settings=settings, organization_id=seed.organization_id, worker_id="same-process-name")
        assert claim is not None
        db.commit()
        return claim


@pytest.mark.parametrize("operation", ["process", "failure", "publish_after_detection"])
def test_analysis_old_attempt_cannot_modify_reclaimed_job(operation, monkeypatch):
    engine = _engine()
    try:
        seed = _seed_extracted_document(engine)
        _, version_id, job_id = _enqueue(engine, seed)
        old = claim_job(engine, seed)

        def reclaim():
            with Session(engine) as other:
                job = other.get(AnalysisDetectionJob, job_id)
                job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                other.commit()
            fresh = claim_job(engine, seed)
            assert fresh.worker_id == old.worker_id and fresh.attempt_count == old.attempt_count + 1
            return fresh

        fresh = None
        if operation == "publish_after_detection":
            from app.analyses import service
            detect = service._detect_claim_records

            def interleaved_detect(*args, **kwargs):
                nonlocal fresh
                result = detect(*args, **kwargs)
                fresh = reclaim()  # old Session still holds its pre-reclaim ORM identity
                return result
            monkeypatch.setattr(service, "_detect_claim_records", interleaved_detect)
        else:
            fresh = reclaim()

        with Session(engine) as db:
            set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
            if operation == "failure":
                assert record_analysis_detection_failure(db, settings=settings, claim=old, error_code="stale", retryable=False) is False
            else:
                with pytest.raises(AnalysisConflictError):
                    process_claimed_analysis_detection(db, claim=old)
            db.commit()
        assert fresh is not None
        with Session(engine) as db:
            job = db.get(AnalysisDetectionJob, job_id)
            version = db.get(AnalysisVersion, version_id)
            assert job.status == AnalysisDetectionJobStatus.RUNNING
            assert job.attempt_count == fresh.attempt_count
            assert job.error_code is None
            assert version.result_sha256 is None
        monkeypatch.undo()
        with Session(engine) as db:
            set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
            result = process_claimed_analysis_detection(db, claim=fresh)
            assert result.claim_count > 0
            db.commit()
        with Session(engine) as db:
            set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
            with pytest.raises(AnalysisConflictError):
                process_claimed_analysis_detection(db, claim=fresh)
    finally:
        engine.dispose()
