from fastapi.testclient import TestClient
from tests.auth_support import authenticate_client
from tests.test_secure_documents import _install_document_fakes, _create_document

def test_document_events_verify_with_legacy_audit_api():
    from app.core.database import SessionLocal
    from app.audit.service import record_audit_event, verify_audit_chain
    app, _, _, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _create_document(client)
            with SessionLocal() as db:
                record_audit_event(db, organization_id=identity.organization_id, entity_type="test", action="legacy.writer", payload={})
                verification = verify_audit_chain(db, identity.organization_id)
                assert verification.is_valid, verification.error_detail
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_historical_legacy_hashes_are_readable_without_rewriting():
    from datetime import datetime, timezone
    from uuid import uuid4
    from app.core.database import SessionLocal
    from app.audit.service import compute_payload_sha256, compute_event_hash, normalize_iso_datetime, verify_audit_chain
    from app.identity.service import append_audit_event
    from app.models.domain import AuditEvent
    app, _, _, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            with SessionLocal() as db:
                timestamp = datetime.now(timezone.utc)
                payload_hash = compute_payload_sha256({})
                legacy_hash = compute_event_hash(identity.organization_id, "historical", None, "old", normalize_iso_datetime(timestamp), payload_hash, None)
                db.add(AuditEvent(organization_id=identity.organization_id, entity_type="historical", action="old",
                    occurred_at=timestamp, payload_json={}, payload_sha256=payload_hash, event_hash=legacy_hash))
                db.commit()
                event = append_audit_event(db, organization_id=identity.organization_id, actor_user_id=identity.user_id,
                    entity_type="new", entity_id=None, action="new", payload={}, occurred_at=timestamp)
                db.commit()
                assert event.previous_event_hash == legacy_hash
                assert verify_audit_chain(db, identity.organization_id).is_valid
                # New canonical events bind actor/request metadata too.
                event.request_id = "tampered"
                db.flush()
                assert not verify_audit_chain(db, identity.organization_id).is_valid
                db.rollback()
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner
