"""Tests for Chantier 8 — B2B Pilot Pack (Overview KPIs, Pre-Audit Reports, Catalog Batch Import, Dossier Export, Retention Policy)."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisVersion,
    Claim,
    ClaimStatus,
    Product,
    Supplier,
)
from tests.auth_support import authenticate_client


def _hash(char: str = "c") -> str:
    return char * 64


def test_pilot_overview_and_kpis():
    with TestClient(app) as client, TestClient(app) as foreign_client:
        identity = authenticate_client(client, role_code="analyst")
        foreign_identity = authenticate_client(foreign_client, role_code="analyst")

        with SessionLocal() as db:
            # Seed suppliers, products, and claims
            supp = Supplier(
                id=uuid4(),
                organization_id=identity.organization_id,
                legal_name="Fournisseur Emballages Eco",
                country_code="FR",
            )
            prod = Product(
                id=uuid4(),
                organization_id=identity.organization_id,
                supplier_id=supp.id,
                reference="SKU-PILOT-01",
                name="Sachet Kraft",
            )
            anl = Analysis(
                id=uuid4(),
                organization_id=identity.organization_id,
                analysis_key="ANL-PILOT-01",
                status=AnalysisStatus.COMPLETED,
            )
            ver = AnalysisVersion(
                id=uuid4(),
                organization_id=identity.organization_id,
                analysis_id=anl.id,
                version_number=1,
                engine_version="0.1.0",
                rulebook_version="rulebook-v1",
                input_manifest_sha256=_hash("p"),
                status=AnalysisStatus.COMPLETED,
            )
            claim = Claim(
                id=uuid4(),
                organization_id=identity.organization_id,
                analysis_version_id=ver.id,
                claim_type="biodegradable",
                category="waste",
                claim_text="Sachet 100% biodégradable",
                status=ClaimStatus.DETECTED,
            )
            db.add_all([supp, prod, anl, ver, claim])
            db.commit()

        # 1. Fetch Pilot Overview
        res = client.get("/api/v1/pilot/overview")
        assert res.status_code == 200, res.text
        data = res.json()

        assert data["organization_id"] == str(identity.organization_id)
        assert data["kpis"]["total_suppliers"] >= 1
        assert data["kpis"]["total_products"] >= 1
        assert data["kpis"]["total_claims_detected"] >= 1
        assert data["kpis"]["critical_risk_claims_count"] >= 1
        assert "aide à la décision" in data["disclaimer"]

        # 2. Foreign tenant has empty KPIs
        foreign_res = foreign_client.get("/api/v1/pilot/overview")
        assert foreign_res.status_code == 200
        foreign_data = foreign_res.json()
        assert foreign_data["kpis"]["total_suppliers"] == 0
        assert foreign_data["kpis"]["total_claims_detected"] == 0


def test_pre_audit_report_generation():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")

        with SessionLocal() as db:
            anl = Analysis(
                id=uuid4(),
                organization_id=identity.organization_id,
                analysis_key="ANL-REP-01",
                status=AnalysisStatus.COMPLETED,
            )
            ver = AnalysisVersion(
                id=uuid4(),
                organization_id=identity.organization_id,
                analysis_id=anl.id,
                version_number=1,
                engine_version="0.1.0",
                rulebook_version="rulebook-v1",
                input_manifest_sha256=_hash("r"),
                status=AnalysisStatus.COMPLETED,
            )
            claim = Claim(
                id=uuid4(),
                organization_id=identity.organization_id,
                analysis_version_id=ver.id,
                claim_type="carbon_neutrality",
                category="carbon",
                claim_text="Livraison neutre en carbone par compensation",
                status=ClaimStatus.DETECTED,
            )
            db.add_all([anl, ver, claim])
            db.commit()

        res = client.get("/api/v1/pilot/pre-audit-report")
        assert res.status_code == 200, res.text
        report = res.json()

        assert report["report_id"].startswith("PREAUDIT-")
        assert len(report["findings"]) >= 1
        finding = [f for f in report["findings"] if f["claim_text"] == "Livraison neutre en carbone par compensation"][0]
        assert finding["severity"] == "HIGH"
        assert "Directive 2024/825" in finding["legal_basis"]
        assert len(report["audit_trail_signature"]) == 64
        assert "PRÉ-AUDIT CONFORMITÉ" in report["legal_disclaimer"]


def test_catalog_batch_import():
    with TestClient(app) as client:
        authenticate_client(client, role_code="admin")

        items = [
            {
                "supplier_legal_name": "Fournisseur A Carton SAS",
                "supplier_country": "FR",
                "supplier_email": "contact@fournisseur-a.fr",
                "product_reference": "BOX-001",
                "product_name": "Boîte Carton Double Cannelure",
                "product_category": "packaging",
            },
            {
                "supplier_legal_name": "Fournisseur A Carton SAS",  # Should reuse supplier
                "supplier_country": "FR",
                "product_reference": "BOX-002",
                "product_name": "Boîte Carton Simple Cannelure",
                "product_category": "packaging",
            },
            {
                "supplier_legal_name": "Fournisseur B Flacons SARL",
                "supplier_country": "DE",
                "product_reference": "FLAC-001",
                "product_name": "Flacon Verre 250ml",
            },
        ]

        res = client.post("/api/v1/pilot/import-catalog", json={"items": items})
        assert res.status_code == 201, res.text
        result = res.json()

        assert result["suppliers_created"] == 2
        assert result["suppliers_reused"] == 1
        assert result["products_created"] == 3
        assert len(result["errors"]) == 0

        # Re-importing same items should reuse existing
        res2 = client.post("/api/v1/pilot/import-catalog", json={"items": items})
        assert res2.status_code == 201
        result2 = res2.json()
        assert result2["suppliers_created"] == 0
        assert result2["suppliers_reused"] == 3
        assert result2["products_created"] == 0
        assert result2["products_reused"] == 3


def test_dossier_export_and_retention_policy():
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")

        # 1. Dossier Export
        exp_res = client.get("/api/v1/pilot/export-dossier")
        assert exp_res.status_code == 200, exp_res.text
        export_data = exp_res.json()
        assert "export_metadata" in export_data
        assert "suppliers" in export_data
        assert "products" in export_data

        # 2. Retention Policy
        ret_res = client.get("/api/v1/pilot/retention-policy")
        assert ret_res.status_code == 200, ret_res.text
        ret_data = ret_res.json()
        # C8: these values are no longer fabricated. An organization that has not
        # declared a retention policy gets null and a non-contractual status, not
        # the publisher's invented defaults.
        assert ret_data["configured"] is False
        assert ret_data["documents_retention_years"] is None
        assert ret_data["audit_trail_retention_years"] is None
        assert "non contractuel" in ret_data["status"]
        assert ret_data["gdpr_contact_email"] is None
        assert ret_data["storage_region"] is None
        assert ret_data["automatic_deletion_implemented"] is False
        assert ret_data["export_formats_supported"] == ["JSON", "CSV", "AUDIT_ZIP"]
        assert ret_data["last_policy_review"] is None
