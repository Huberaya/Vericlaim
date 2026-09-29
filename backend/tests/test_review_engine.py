"""Tests for Chantier 6.3 — Human Review Validations and Supplier Evidence Requests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisVersion,
    AuditEvent,
    Claim,
    ClaimSource,
    ClaimStatus,
    Document,
    DocumentSegment,
    DocumentVersion,
    EvidenceRequest,
    EvidenceRequestStatus,
    Product,
    SegmentType,
    Supplier,
    Validation,
    ValidationDecision,
)
from tests.auth_support import authenticate_client


def _hash(char: str = "b") -> str:
    return char * 64


def test_human_validations_and_tenant_isolation():
    from app.main import app

    with TestClient(app) as client, TestClient(app) as foreign_client, TestClient(app) as viewer_client:
        identity = authenticate_client(client, role_code="analyst")
        foreign_identity = authenticate_client(foreign_client, role_code="analyst")
        authenticate_client(viewer_client, role_code="viewer")

        analysis_id = uuid4()
        version_id = uuid4()
        claim_id = uuid4()

        with SessionLocal() as db:
            analysis = Analysis(
                id=analysis_id,
                organization_id=identity.organization_id,
                analysis_key="ANL-REV-01",
                status=AnalysisStatus.COMPLETED,
            )
            version = AnalysisVersion(
                id=version_id,
                organization_id=identity.organization_id,
                analysis_id=analysis_id,
                version_number=1,
                engine_version="0.1.0",
                rulebook_version="rulebook-v1",
                input_manifest_sha256=_hash("m"),
                status=AnalysisStatus.COMPLETED,
            )
            claim = Claim(
                id=claim_id,
                organization_id=identity.organization_id,
                analysis_version_id=version_id,
                claim_type="carbon_neutrality",
                category="carbon",
                claim_text="Produit neutre en carbone",
                status=ClaimStatus.DETECTED,
            )
            db.add_all([analysis, version, claim])
            db.commit()

        # 1. Record Validation (contested with comment)
        val_res = client.post(
            "/api/v1/validations",
            json={
                "analysis_version_id": str(version_id),
                "claim_id": str(claim_id),
                "decision": "contested",
                "comment": "Allégation non étayée par un bilan Scope 3 complet",
                "rationale": "Non conformité avec décret 2022-748 et directive 2024/825",
            },
        )
        assert val_res.status_code == 201, val_res.text
        val_data = val_res.json()
        assert val_data["decision"] == "contested"
        assert val_data["comment"] == "Allégation non étayée par un bilan Scope 3 complet"

        # 2. Foreign tenant cannot list or create validation on foreign analysis
        foreign_list = foreign_client.get(f"/api/v1/analyses/{analysis_id}/versions/1/validations")
        assert foreign_list.status_code == 404

        # 3. Viewer in another tenant gets 404 on foreign analysis
        viewer_foreign = viewer_client.get(f"/api/v1/analyses/{analysis_id}/versions/1/validations")
        assert viewer_foreign.status_code == 404

        # 4. List validations for this analysis version
        list_res = client.get(f"/api/v1/analyses/{analysis_id}/versions/1/validations")
        assert list_res.status_code == 200, list_res.text
        assert len(list_res.json()) == 1
        assert list_res.json()[0]["claim_id"] == str(claim_id)

        # 5. Update Validation (overriding to validated after human proof review)
        override_res = client.post(
            "/api/v1/validations",
            json={
                "analysis_version_id": str(version_id),
                "claim_id": str(claim_id),
                "decision": "validated",
                "comment": "Preuve complémentaire fournie et vérifiée",
                "rationale": "Rapport ACV reçu et conforme",
            },
        )
        assert override_res.status_code == 201
        assert override_res.json()["decision"] == "validated"

        # Check Audit Trail
        with SessionLocal() as db:
            events = db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.organization_id == identity.organization_id,
                    AuditEvent.entity_type == "validation",
                )
            ).all()
            assert "validation.created" in events
            assert "validation.updated" in events


def test_supplier_evidence_requests_lifecycle():
    from app.main import app

    with TestClient(app) as client, TestClient(app) as foreign_client:
        identity = authenticate_client(client, role_code="analyst")
        authenticate_client(foreign_client, role_code="analyst")

        # Create Supplier & Product
        supp = client.post(
            "/api/v1/suppliers",
            json={"legal_name": "Fournisseur Plastiques SA", "country_code": "FR"},
            headers={"Idempotency-Key": "supp-rev-01"},
        ).json()["supplier"]

        prod = client.post(
            "/api/v1/products",
            json={"supplier_id": supp["id"], "reference": "PLAST-001", "name": "Bocal PET"},
            headers={"Idempotency-Key": "prod-rev-01"},
        ).json()["product"]

        # 1. Create Evidence Request in draft
        due_date = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        req_res = client.post(
            "/api/v1/evidence-requests",
            json={
                "supplier_id": supp["id"],
                "product_id": prod["id"],
                "subject": "Demande d'attestation filière de recyclage",
                "message": "Merci de nous fournir l'attestation de recyclabilité pour le bocal PET.",
                "requested_items": [{"type": "recycling_route", "name": "Attestation Citeo"}],
                "due_at": due_date,
            },
        )
        assert req_res.status_code == 201, req_res.text
        req_data = req_res.json()
        req_id = req_data["id"]
        assert req_data["status"] == "draft"
        assert req_data["supplier_name"] == "Fournisseur Plastiques SA"
        assert req_data["product_name"] == "Bocal PET"

        # 2. Foreign tenant cannot read
        assert foreign_client.get(f"/api/v1/evidence-requests/{req_id}").status_code == 404

        # 3. Send request
        send_res = client.post(f"/api/v1/evidence-requests/{req_id}/send")
        assert send_res.status_code == 200, send_res.text
        assert send_res.json()["status"] == "sent"
        assert send_res.json()["sent_at"] is not None

        # 4. Remind request
        remind_res = client.post(f"/api/v1/evidence-requests/{req_id}/remind")
        assert remind_res.status_code == 200, remind_res.text
        assert remind_res.json()["last_reminded_at"] is not None

        # 5. List evidence requests
        list_res = client.get(f"/api/v1/evidence-requests?supplier_id={supp['id']}")
        assert list_res.status_code == 200
        assert len(list_res.json()["items"]) == 1

        # 6. Update request to fulfilled
        patch_res = client.patch(
            f"/api/v1/evidence-requests/{req_id}",
            json={"status": "fulfilled"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["status"] == "fulfilled"

        # Check Audit Trail
        with SessionLocal() as db:
            events = db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.organization_id == identity.organization_id,
                    AuditEvent.entity_type == "evidence_request",
                )
            ).all()
            assert "evidence_request.created" in events
            assert "evidence_request.sent" in events
            assert "evidence_request.reminded" in events
            assert "evidence_request.updated" in events


def test_supplier_request_template_generation():
    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")

        analysis_id = uuid4()
        version_id = uuid4()
        claim_id = uuid4()

        with SessionLocal() as db:
            analysis = Analysis(
                id=analysis_id,
                organization_id=identity.organization_id,
                analysis_key="ANL-TMPL-01",
                status=AnalysisStatus.COMPLETED,
            )
            version = AnalysisVersion(
                id=version_id,
                organization_id=identity.organization_id,
                analysis_id=analysis_id,
                version_number=1,
                engine_version="0.1.0",
                rulebook_version="rulebook-v1",
                input_manifest_sha256=_hash("m"),
                status=AnalysisStatus.COMPLETED,
            )
            claim = Claim(
                id=claim_id,
                organization_id=identity.organization_id,
                analysis_version_id=version_id,
                claim_type="recyclable",
                category="recycling",
                claim_text="Emballage 100% recyclable",
                status=ClaimStatus.DETECTED,
            )
            db.add_all([analysis, version, claim])
            db.commit()

        # Generate tailored template
        tmpl_res = client.post(
            "/api/v1/evidence-requests/generate-template",
            json={"claim_id": str(claim_id)},
        )
        assert tmpl_res.status_code == 200, tmpl_res.text
        tmpl = tmpl_res.json()
        assert "100% recyclable" in tmpl["subject"]
        assert "Directive européenne 2024/825" in tmpl["message"]
        assert "Attestation de filière" in tmpl["requested_items"][0]["name"]
        assert tmpl["suggested_due_days"] == 15
