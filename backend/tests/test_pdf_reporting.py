from __future__ import annotations

import io
import zipfile
from uuid import uuid4
import fitz
import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal, create_tables
from app.main import app
from app.models.domain import Analysis, AnalysisVersion
from tests.auth_support import authenticate_client


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def test_pdf_generation_from_evaluation_response():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")

        eval_payload = {
            "evaluation_response": {
                "extracted_source_text": "Produit 100% biodégradable et éco-responsable.",
                "overall_compliance": "NON_COMPLIANT",
                "risk_score": 85,
                "legal_exposure_estimate": "Risque d'amende administrative DGCCRF jusqu'à 100 000 €.",
                "violations_count": 1,
                "conditional_findings_count": 0,
                "detected_claims_count": 1,
                "evaluations": [
                    {
                        "claim_id": "claim-101",
                        "claim_text": "100% biodégradable",
                        "claim_type": "biodegradable",
                        "start_offset": 8,
                        "end_offset": 26,
                        "trigger_text": "biodégradable",
                        "rule_id": "FR-AGEC-R541-220-BIODEC",
                        "rule_title": "Interdiction absolue allégation biodégradable",
                        "law_reference": "Code de l'environnement art. L. 541-9-1",
                        "legal_force": "BINDING_FR",
                        "severity": "CRITICAL",
                        "verdict": "NON_COMPLIANT",
                        "is_legal_violation": True,
                        "remediation": {
                            "buyer_explanation": "Allégation formellement proscrite par la loi AGEC.",
                            "recommended_rewrite": "Supprimer la mention biodégradable.",
                            "supplier_contract_clause": "Clause d'interdiction stricte AGEC R. 541-220.",
                        },
                    }
                ],
                "exposure_matrix": {"items": []},
                "audit_trail": {
                    "audit_id": "aud-12345",
                    "engine_version": "1.0.0",
                    "rulebook_version": "2026.09",
                    "evaluated_at_utc": "2026-09-29T18:00:00Z",
                    "as_of_date": "2026-09-29",
                    "source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "evidence_manifest_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "report_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "record_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                },
            },
            "document_title": "Rapport Pré-Audit Test",
            "product_identifier": "SKU-PROD-TEST",
            "surface": "packaging",
        }

        response = client.post("/api/v1/reports/pdf", json=eval_payload)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment; filename=" in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-")

        # Vérification du contenu du PDF via PyMuPDF
        doc = fitz.open(stream=response.content, filetype="pdf")
        assert len(doc) >= 1
        page1_text = doc[0].get_text()
        assert "VERICLAIM AI" in page1_text
        assert "NON-CONFORMITÉ JURIDIQUE RETENUE" in page1_text
        assert "FR-AGEC-R541-220-BIODEC" in page1_text
        assert "DOCUMENT DE PRÉ-AUDIT ET D'AIDE À LA DÉCISION" in page1_text


def test_dossier_zip_pack_generation():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")

        eval_payload = {
            "evaluation_response": {
                "extracted_source_text": "Emballage issu de forêts gérées durablement.",
                "overall_compliance": "COMPLIANT",
                "risk_score": 10,
                "legal_exposure_estimate": "Risque faible sous réserve de certificat FSC/PEFC valide.",
                "violations_count": 0,
                "conditional_findings_count": 0,
                "detected_claims_count": 0,
                "evaluations": [],
                "exposure_matrix": {"items": []},
                "audit_trail": {
                    "audit_id": "aud-67890",
                    "engine_version": "1.0.0",
                    "rulebook_version": "2026.09",
                    "evaluated_at_utc": "2026-09-29T18:00:00Z",
                    "as_of_date": "2026-09-29",
                    "source_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                    "evidence_manifest_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                    "report_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                    "record_hash": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                },
            },
            "product_identifier": "SKU-GREEN-001",
        }

        response = client.post("/api/v1/reports/dossier", json=eval_payload)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"

        # Vérification de l'intégrité de l'archive ZIP
        zip_file = zipfile.ZipFile(io.BytesIO(response.content))
        file_list = zip_file.namelist()
        assert "rapport_pre_audit.pdf" in file_list
        assert "manifeste_audit_scelle.json" in file_list
        assert "matrice_probatoire.csv" in file_list
        assert "README_DOSSIER_AUDIT.txt" in file_list

        readme_content = zip_file.read("README_DOSSIER_AUDIT.txt").decode("utf-8")
        assert "DOSSIER DE PRÉ-AUDIT RÉGLEMENTAIRE" in readme_content
        assert "AVERTISSEMENT JURIDIQUE" in readme_content


def test_analysis_pdf_and_dossier_tenant_isolation():
    with TestClient(app) as client_a, TestClient(app) as client_b:
        identity_a = authenticate_client(client_a, role_code="analyst")
        identity_b = authenticate_client(client_b, role_code="analyst")

        with SessionLocal() as db:
            analysis_a = Analysis(
                organization_id=identity_a.organization_id,
                analysis_key="ana-alpha-001",
                status="completed",
            )
            db.add(analysis_a)
            db.commit()
            db.refresh(analysis_a)

            version_a = AnalysisVersion(
                organization_id=identity_a.organization_id,
                analysis_id=analysis_a.id,
                version_number=1,
                status="completed",
                engine_version="1.0.0",
                rulebook_version="2026.09",
                input_manifest_sha256="0" * 64,
                result_sha256="0" * 64,
                result_json={
                    "extracted_source_text": "Emballage carton 100% recyclable.",
                },
            )
            db.add(version_a)
            db.commit()

            analysis_id = str(analysis_a.id)

        # Org Alpha peut télécharger son PDF et son dossier
        res_pdf_a = client_a.get(f"/api/v1/reports/analyses/{analysis_id}/pdf")
        assert res_pdf_a.status_code == 200, res_pdf_a.text
        assert res_pdf_a.content.startswith(b"%PDF-")

        res_dossier_a = client_a.get(f"/api/v1/reports/analyses/{analysis_id}/dossier")
        assert res_dossier_a.status_code == 200, res_dossier_a.text
        assert res_dossier_a.headers["content-type"] == "application/zip"

        # Org Beta ne peut pas accéder à l'analyse d'Org Alpha (404 / isolation tenant)
        res_pdf_b = client_b.get(f"/api/v1/reports/analyses/{analysis_id}/pdf")
        assert res_pdf_b.status_code == 404

        res_dossier_b = client_b.get(f"/api/v1/reports/analyses/{analysis_id}/dossier")
        assert res_dossier_b.status_code == 404
