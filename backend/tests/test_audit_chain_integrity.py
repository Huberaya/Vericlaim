from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.audit.service import record_audit_event, verify_audit_chain
from app.core.database import SessionLocal, create_tables
from app.main import app
from app.models.domain import AuditEvent
from tests.auth_support import authenticate_client


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def test_record_audit_events_and_hash_chaining():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        org_id = identity.organization_id

        with SessionLocal() as db:
            ev1 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="analysis",
                action="created",
                payload={"sku": "SKU-TEST-001", "surface": "packaging"},
                actor_user_id=identity.user_id,
            )
            ev2 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="claim",
                action="detected",
                payload={"claim_text": "100% recyclable", "rule": "FR-AGEC-RECYC"},
                actor_user_id=identity.user_id,
            )
            ev3 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="report",
                action="generated",
                payload={"format": "pdf", "status": "COMPLIANT"},
                actor_user_id=identity.user_id,
            )

            # Vérification du chaînage des hash
            assert ev1.previous_event_hash is None
            assert len(ev1.event_hash) == 64
            assert ev2.previous_event_hash == ev1.event_hash
            assert len(ev2.event_hash) == 64
            assert ev3.previous_event_hash == ev2.event_hash
            assert len(ev3.event_hash) == 64

        # Vérification via l'API
        res_list = client.get("/api/v1/audit/events")
        assert res_list.status_code == 200
        events = res_list.json()
        assert len(events) == 3

        res_verify = client.get("/api/v1/audit/verify")
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["is_valid"] is True
        assert verify_data["total_events"] == 3
        assert verify_data["head_event_hash"] == ev3.event_hash


def test_tamper_detection_on_payload_modification():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        org_id = identity.organization_id

        with SessionLocal() as db:
            ev1 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="document",
                action="uploaded",
                payload={"file": "certificat.pdf"},
            )
            ev2 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="validation",
                action="approved",
                payload={"status": "APPROVED", "by": "analyst"},
            )
            tampered_id = ev2.id

            # Altération malveillante directe du payload en base
            ev2_db = db.get(AuditEvent, tampered_id)
            assert ev2_db is not None
            ev2_db.payload_json = {"status": "FORGED_APPROVAL", "by": "attacker"}
            db.commit()

        # La vérification de la chaîne doit détecter l'altération
        res_verify = client.get("/api/v1/audit/verify")
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["is_valid"] is False
        assert verify_data["tampered_event_id"] == str(tampered_id)
        assert "Altération détectée du payload JSON" in verify_data["error_detail"]


def test_tamper_detection_on_event_deletion():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        org_id = identity.organization_id

        with SessionLocal() as db:
            ev1 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="doc",
                action="step1",
                payload={"step": 1},
            )
            ev2 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="doc",
                action="step2",
                payload={"step": 2},
            )
            ev3 = record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="doc",
                action="step3",
                payload={"step": 3},
            )

            # Suppression frauduleuse de ev2
            ev2_db = db.get(AuditEvent, ev2.id)
            assert ev2_db is not None
            db.delete(ev2_db)
            db.commit()

        # La vérification doit constater la rupture de chaîne entre ev1 et ev3
        res_verify = client.get("/api/v1/audit/verify")
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["is_valid"] is False
        assert "Rupture de chaîne détectée" in verify_data["error_detail"]


def test_multi_tenant_chain_isolation():
    with TestClient(app) as client_a, TestClient(app) as client_b:
        identity_a = authenticate_client(client_a, role_code="analyst")
        identity_b = authenticate_client(client_b, role_code="analyst")

        with SessionLocal() as db:
            ev_a1 = record_audit_event(
                db=db,
                organization_id=identity_a.organization_id,
                entity_type="order",
                action="submit",
                payload={"org": "A"},
            )
            ev_b1 = record_audit_event(
                db=db,
                organization_id=identity_b.organization_id,
                entity_type="order",
                action="submit",
                payload={"org": "B"},
            )

        # Chaîne Org A
        res_a = client_a.get("/api/v1/audit/events")
        assert res_a.status_code == 200
        assert len(res_a.json()) == 1
        assert res_a.json()[0]["id"] == str(ev_a1.id)

        # Chaîne Org B
        res_b = client_b.get("/api/v1/audit/events")
        assert res_b.status_code == 200
        assert len(res_b.json()) == 1
        assert res_b.json()[0]["id"] == str(ev_b1.id)

        # Les deux chaînes sont valides et indépendantes
        assert client_a.get("/api/v1/audit/verify").json()["is_valid"] is True
        assert client_b.get("/api/v1/audit/verify").json()["is_valid"] is True


def test_cryptographic_integrity_certificate():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        org_id = identity.organization_id

        with SessionLocal() as db:
            record_audit_event(
                db=db,
                organization_id=org_id,
                entity_type="system",
                action="init",
                payload={"version": "1.0.0"},
            )

        res_cert = client.get("/api/v1/audit/integrity-certificate")
        assert res_cert.status_code == 200
        cert = res_cert.json()
        assert cert["certificate_id"].startswith("CERT-AUDIT-")
        assert cert["chain_length"] == 1
        assert cert["verification_status"] == "SEALED_AND_VERIFIED"
        assert len(cert["head_event_hash"]) == 64
        assert len(cert["merkle_digest"]) == 64
