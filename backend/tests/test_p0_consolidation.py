"""Regression proofs for the P0 commercialisation audit.

These tests exercise real application services and opaque sessions. They do not
claim to validate a deployed OIDC provider, S3, ClamAV or OCR installation.
"""
from __future__ import annotations

import io

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import SessionLocal, create_tables
from app.enterprise.service import create_organization_api_key, create_organization_legal_hold
from app.identity.oidc import OidcClient, OidcProtocolError
from app.main import app
from app.models.domain import Analysis, AnalysisStatus, AnalysisVersion, ApiKey, LegalHold
from tests.auth_support import authenticate_client


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def _persisted_analysis(organization_id, *, status=AnalysisStatus.COMPLETED):
    with SessionLocal() as db:
        analysis = Analysis(organization_id=organization_id, analysis_key="p0-report", status=status)
        db.add(analysis)
        db.flush()
        version = AnalysisVersion(
            organization_id=organization_id,
            analysis_id=analysis.id,
            version_number=1,
            status=status,
            engine_version="test-engine",
            rulebook_version="test-rulebook",
            input_manifest_sha256="a" * 64,
            result_json={},
        )
        db.add(version)
        db.commit()
        return analysis.id


@pytest.mark.parametrize("suffix", ["pdf", "dossier"])
@pytest.mark.parametrize("status", [AnalysisStatus.QUEUED, AnalysisStatus.COMPLETED])
def test_persisted_analysis_export_never_fabricates_a_regulatory_report(suffix, status):
    with TestClient(app) as client:
        identity = authenticate_client(client)
        analysis_id = _persisted_analysis(identity.organization_id, status=status)
        response = client.get(f"/api/v1/reports/analyses/{analysis_id}/{suffix}")
        assert response.status_code == 409, response.text
        assert "persist" in response.json()["detail"].lower()
        assert not response.content.startswith(b"%PDF-")


def test_legacy_result_text_cannot_be_rendered_as_a_compliant_persisted_report():
    with TestClient(app) as client:
        identity = authenticate_client(client)
        with SessionLocal() as db:
            analysis = Analysis(organization_id=identity.organization_id, analysis_key="legacy-text", status="completed")
            db.add(analysis)
            db.flush()
            db.add(
                AnalysisVersion(
                    organization_id=identity.organization_id,
                    analysis_id=analysis.id,
                    version_number=1,
                    status="completed",
                    engine_version="legacy",
                    rulebook_version="legacy",
                    input_manifest_sha256="b" * 64,
                    result_json={"extracted_source_text": "Texte sans allégation explicite."},
                )
            )
            db.commit()
            analysis_id = analysis.id
        response = client.get(f"/api/v1/reports/analyses/{analysis_id}/pdf")
        assert response.status_code == 409
        assert not response.content.startswith(b"%PDF-")


def test_enterprise_metrics_do_not_invent_observability_or_alerts():
    with TestClient(app) as client:
        identity = authenticate_client(client)
        _persisted_analysis(identity.organization_id)
        metrics = client.get("/api/v1/enterprise/metrics")
        assert metrics.status_code == 200, metrics.text
        data = metrics.json()
        assert data["service_status"] == "responding"
        assert data["database_status"] == "connected"
        assert data["storage_status"] == "not_probed"
        assert data["workers_status"] == "not_probed"
        assert data["active_tenants_count"] == 1
        assert data["total_analyses_completed"] == 1
        for field in (
            "average_analysis_latency_ms",
            "total_api_requests",
            "error_rate_percent",
            "open_alerts_count",
            "memory_usage_mb",
            "cpu_utilization_percent",
        ):
            assert data[field] is None, field
        alerts = client.get("/api/v1/enterprise/alerts")
        assert alerts.status_code == 200
        assert alerts.json() == []


def test_rule_reviews_are_not_globally_approved_without_a_persisted_legal_workflow():
    with TestClient(app) as admin, TestClient(app) as other:
        authenticate_client(admin, role_code="admin")
        authenticate_client(other, role_code="viewer")
        before = other.get("/api/v1/regulatory/rules/RULE_AGEC_BIODEGRADABLE").json()
        assert before["governance_review"]["review_status"] == "under_review"
        assert before["governance_review"]["reviewed_by"] is None
        response = admin.post(
            "/api/v1/regulatory/rules/RULE_AGEC_BIODEGRADABLE/review",
            json={"review_status": "approved_legal", "confidence_level": "high", "legal_notes": "non vérifié"},
        )
        assert response.status_code == 409
        after = other.get("/api/v1/regulatory/rules/RULE_AGEC_BIODEGRADABLE").json()
        assert after["governance_review"] == before["governance_review"]
        overview = other.get("/api/v1/regulatory/rulebook").json()
        assert "non vérifi" in overview["governance_statement"].lower()


def test_enterprise_writes_remain_atomic_with_the_request_transaction():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        with SessionLocal() as db:
            key = create_organization_api_key(
                db,
                organization_id=identity.organization_id,
                actor_user_id=identity.user_id,
                name="Rollback probe",
                scopes=["catalog:read"],
            )
            hold = create_organization_legal_hold(
                db,
                organization_id=identity.organization_id,
                actor_user_id=identity.user_id,
                case_reference="ROLLBACK",
                reason="Transactional regression probe",
            )
            key_id, hold_id = key.id, hold.id
            db.rollback()
        with SessionLocal() as db:
            assert db.scalar(select(ApiKey).where(ApiKey.id == key_id)) is None
            assert db.scalar(select(LegalHold).where(LegalHold.id == hold_id)) is None


@pytest.mark.parametrize("field", ["authorization_endpoint", "token_endpoint", "jwks_uri"])
def test_production_oidc_discovery_refuses_cleartext_endpoints(field):
    settings = Settings(
        environment="production",
        oidc_issuer="https://idp.example.test",
        oidc_client_id="client",
        oidc_redirect_uri="https://app.example.test/api/v1/auth/callback",
        oidc_discovery_url="https://idp.example.test/.well-known/openid-configuration",
    )
    metadata = {
        "issuer": "https://idp.example.test",
        "authorization_endpoint": "https://idp.example.test/authorize",
        "token_endpoint": "https://idp.example.test/token",
        "jwks_uri": "https://idp.example.test/jwks",
    }
    metadata[field] = metadata[field].replace("https://", "http://", 1)

    def handler(request):
        return httpx.Response(200, json=metadata)

    client = OidcClient(settings, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(OidcProtocolError):
        client.metadata()


@pytest.mark.parametrize("image_format", ["BMP", "GIF"])
def test_image_decoder_rejects_codecs_outside_the_ingestion_allowlist(image_format, monkeypatch):
    """Exercise the decoder seam directly; ingestion normally rejects these signatures first."""
    from PIL import Image
    from app.engine.document_extractor import DocumentExtractionError, DocumentTextExtractor
    import pytesseract

    payload = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(payload, format=image_format)
    calls = []
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *args, **kwargs: calls.append(args) or "OCR")
    with pytest.raises(DocumentExtractionError):
        DocumentTextExtractor()._open_checked_image(payload.getvalue())
    assert calls == []


@pytest.mark.parametrize("image_format", ["PNG", "JPEG", "TIFF", "WEBP"])
def test_image_decoder_keeps_supported_raster_formats(image_format):
    from PIL import Image
    from app.engine.document_extractor import DocumentTextExtractor

    payload = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(payload, format=image_format)
    image = DocumentTextExtractor()._open_checked_image(payload.getvalue())
    try:
        assert image.format == image_format
        assert image.size == (8, 8)
    finally:
        image.close()


@pytest.mark.parametrize("component", ["rule", "governance", "citation"])
def test_rulebook_fingerprint_covers_all_published_content(component, monkeypatch):
    from dataclasses import replace
    from app.engine import governance

    before = governance.get_rulebook_summary().sha256_fingerprint
    if component == "rule":
        changed = replace(governance.RULES[0], priority=governance.RULES[0].priority + 1)
        monkeypatch.setattr(governance, "RULES", (changed, *governance.RULES[1:]))
    elif component == "governance":
        metadata = dict(governance.RULE_METADATA_EXT[governance.RULES[0].rule_id])
        metadata["legal_notes"] = "Changed governance note for fingerprint regression."
        monkeypatch.setitem(governance.RULE_METADATA_EXT, governance.RULES[0].rule_id, metadata)
    else:
        citation = governance.OFFICIAL_CITATIONS[governance.RULES[0].rule_id][0].model_copy(
            update={"text_excerpt": "Changed citation for fingerprint regression."}
        )
        monkeypatch.setitem(governance.OFFICIAL_CITATIONS, governance.RULES[0].rule_id, [citation])
    assert governance.get_rulebook_summary().sha256_fingerprint != before


def test_rulebook_does_not_claim_an_unverified_release_or_homologation():
    from app.engine.governance import get_rulebook_summary

    assert get_rulebook_summary().release_date is None


def _seed_detected_claim(organization_id):
    from app.models.domain import Claim
    with SessionLocal() as db:
        version = db.scalar(select(AnalysisVersion).where(AnalysisVersion.organization_id == organization_id))
        claim = Claim(
            organization_id=organization_id,
            analysis_version_id=version.id,
            claim_type="biodegradable",
            category="environment",
            claim_text="Emballage biodégradable",
        )
        db.add(claim)
        db.commit()
        return claim.id


def test_pilot_synthesis_is_an_inventory_not_an_invented_legal_report():
    import hashlib
    import json
    with TestClient(app) as client:
        identity = authenticate_client(client)
        _persisted_analysis(identity.organization_id)
        _seed_detected_claim(identity.organization_id)
        response = client.get("/api/v1/pilot/pre-audit-report")
        assert response.status_code == 200, response.text
        report = response.json()
        finding = report["findings"][0]
        assert finding["severity"] == "not_evaluated"
        assert finding["coverage_status"] == "missing"
        assert "signature" not in report
        assert "audit_trail_signature" not in report
        material = {key: value for key, value in report.items() if key not in {"report_id", "content_sha256"}}
        expected = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        assert report["content_sha256"] == expected


def test_pilot_empty_inventory_never_claims_compliance_or_absence_of_findings():
    with TestClient(app) as client:
        authenticate_client(client)
        overview = client.get("/api/v1/pilot/overview").json()
        assert overview["kpis"]["human_validation_rate_percent"] is None
        assert overview["kpis"]["claims_expired_evidence"] is None
        assert overview["kpis"]["critical_risk_claims_count"] is None
        assert "global_compliance_rate_percent" not in overview["kpis"]
        report = client.get("/api/v1/pilot/pre-audit-report").json()
        assert report["findings"] == []
        assert not any("aucun écart" in item.lower() for item in report["remediation_summary"])


def test_retention_endpoint_does_not_invent_a_deployed_privacy_policy():
    with TestClient(app) as client:
        identity = authenticate_client(client)
        response = client.get("/api/v1/pilot/retention-policy")
        assert response.status_code == 200, response.text
        policy = response.json()
        assert policy["organization_id"] == str(identity.organization_id)
        assert policy["policy_status"] == "not_configured"
        for field in ("documents_retention_years", "audit_trail_retention_years", "evidence_archive_retention_years", "gdpr_contact_email", "encryption_standard", "storage_region", "last_policy_review"):
            assert policy[field] is None, field


def test_evidence_csv_neutralizes_spreadsheet_formula_cells():
    import csv
    from types import SimpleNamespace
    from app.reports.dossier_exporter import build_evidence_csv

    item = SimpleNamespace(
        claim_id="=1+1", claim_text='=HYPERLINK("https://example.invalid")', rule_id="@rule",
        rule_title="+title", law_reference="-reference", verdict="REVIEW_REQUIRED",
        is_legal_violation=False, required_evidence=["=evidence"],
        remediation=SimpleNamespace(recommended_rewrite="=rewrite"),
    )
    rows = list(csv.reader(io.StringIO(build_evidence_csv(SimpleNamespace(evaluations=[item]))), delimiter=";"))
    for cell in rows[1]:
        assert not cell.lstrip().startswith(("=", "+", "-", "@")), cell


def test_pilot_supplier_priority_uses_persisted_analysis_ownership():
    from datetime import datetime, timezone
    from app.models.domain import Claim, Product, Supplier, Validation, ValidationDecision
    with TestClient(app) as client:
        identity = authenticate_client(client)
        with SessionLocal() as db:
            supplier = Supplier(organization_id=identity.organization_id, legal_name="Fournisseur P0", country_code="FR")
            db.add(supplier)
            db.flush()
            product = Product(organization_id=identity.organization_id, supplier_id=supplier.id, reference="P0-SKU", name="Produit P0")
            analysis = Analysis(organization_id=identity.organization_id, analysis_key="p0-owner", supplier_id=supplier.id, product_id=None)
            db.add_all([product, analysis])
            db.flush()
            product.supplier_id = supplier.id
            analysis.product_id = product.id
            version = AnalysisVersion(
                organization_id=identity.organization_id, analysis_id=analysis.id, version_number=1,
                engine_version="test", rulebook_version="test", input_manifest_sha256="c" * 64,
            )
            db.add(version)
            db.flush()
            claim = Claim(organization_id=identity.organization_id, analysis_version_id=version.id,
                          claim_type="recyclable", category="environment", claim_text="Recyclable")
            db.add(claim)
            db.flush()
            for year, decision in [(2024, ValidationDecision.VALIDATED), (2025, ValidationDecision.CONTESTED)]:
                db.add(Validation(organization_id=identity.organization_id, analysis_version_id=version.id,
                                  claim_id=claim.id, decision=decision,
                                  created_at=datetime(year, 1, 1, tzinfo=timezone.utc)))
            db.commit()
            supplier_id = supplier.id
        overview = client.get("/api/v1/pilot/overview").json()
        row = next(item for item in overview["top_risk_suppliers"] if item["supplier_id"] == str(supplier_id))
        assert row["claims_count"] == 1
        assert row["missing_evidence_count"] == 1
        assert row["review_priority"] == "medium"
        assert overview["kpis"]["claims_validated"] == 0
        assert overview["kpis"]["claims_contested"] == 1
        assert overview["kpis"]["claims_pending_review"] == 0
