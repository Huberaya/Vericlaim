"""Audit chain integrity — exercised through the REAL writer path.

These tests deliberately never call a private helper to fabricate events.
They drive the chain through the application's own writer
(``append_audit_event``, used by every service) and verify through the public
API. The previous revision tested a dead function that no production code
called, which is why a structurally broken chain stayed green.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.service import verify_audit_chain
from app.core.database import SessionLocal, create_tables
from app.models.domain import AuditEvent
from tests.auth_support import authenticate_client


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def _create_events(client: TestClient, count: int) -> list[dict]:
    """Write ``count`` real chained events through the public API."""
    for index in range(count):
        response = client.post(
            "/api/v1/suppliers",
            json={"legal_name": f"Fournisseur {index}"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 201, response.text
    return client.get("/api/v1/audit/events").json()


def test_real_writer_produces_valid_chain():
    with TestClient(app) as client:  # noqa: F821
        identity = authenticate_client(client, role_code="analyst")
        events = _create_events(client, 3)

        assert len(events) >= 3

        # The chain exposes a genesis event and linked successors
        ordered = sorted(events, key=lambda item: item["occurred_at"])
        assert ordered[0]["previous_event_hash"] is None
        for previous, current in zip(ordered, ordered[1:]):
            assert current["previous_event_hash"] == previous["event_hash"]
        for event in ordered:
            assert len(event["event_hash"]) == 64

        verification = client.get("/api/v1/audit/verify").json()
        assert verification["is_valid"] is True, verification["error_detail"]
        assert verification["total_events"] == len(events)

        # Same tenant, direct service call agrees with the API
        with SessionLocal() as db:
            assert verify_audit_chain(db, organization_id=identity.organization_id).is_valid is True


def test_tamper_detection_on_payload_modification():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        _create_events(client, 2)

        with SessionLocal() as db:
            event = db.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == identity.organization_id)
                .order_by(AuditEvent.occurred_at)
            ).first()
            event.payload_json = {"legal_name": "FALSIFIE"}
            db.commit()

        verification = client.get("/api/v1/audit/verify").json()
        assert verification["is_valid"] is False
        assert "Altération" in verification["error_detail"]


def test_tamper_detection_on_event_deletion():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        _create_events(client, 3)

        with SessionLocal() as db:
            events = list(
                db.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.organization_id == identity.organization_id)
                    .order_by(AuditEvent.occurred_at)
                ).all()
            )
            db.delete(events[1])
            db.commit()

        verification = client.get("/api/v1/audit/verify").json()
        assert verification["is_valid"] is False
        assert "Rupture de chaîne" in verification["error_detail"]


def test_multi_tenant_chain_isolation():
    client_a = TestClient(app)
    client_a.__enter__()
    identity_a = authenticate_client(client_a, role_code="analyst", email=f"a.{uuid4().hex[:8]}@example.com")
    _create_events(client_a, 2)

    client_b = TestClient(app)
    client_b.__enter__()
    authenticate_client(client_b, role_code="analyst", email=f"b.{uuid4().hex[:8]}@example.com")
    _create_events(client_b, 1)

    events_a = client_a.get("/api/v1/audit/events").json()
    events_b = client_b.get("/api/v1/audit/events").json()
    assert len(events_a) == 2
    assert len(events_b) == 1
    assert {e["id"] for e in events_a}.isdisjoint({e["id"] for e in events_b})

    assert client_a.get("/api/v1/audit/verify").json()["is_valid"] is True
    assert client_b.get("/api/v1/audit/verify").json()["is_valid"] is True

    client_a.__exit__(None, None, None)
    client_b.__exit__(None, None, None)


def test_cryptographic_integrity_certificate():
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")
        _create_events(client, 1)

        response = client.get("/api/v1/audit/integrity-certificate")
        assert response.status_code == 200, response.text
        certificate = response.json()
        assert certificate["certificate_id"].startswith("CERT-AUDIT-")
        assert certificate["chain_length"] == 1
        assert certificate["verification_status"] == "SEALED_AND_VERIFIED"
        assert len(certificate["head_event_hash"]) == 64
        assert len(certificate["merkle_digest"]) == 64


from app.main import app  # noqa: E402  (imported after helpers for readability)
