from __future__ import annotations

from datetime import date
from decimal import Decimal
import json

from fastapi.testclient import TestClient

from app.engine.fact_extractor import FactExtractor
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.proof_validator import ProofValidator, RegistryRecord
from app.models.legal_types import (
    ClaimType,
    EcolabelEvidence,
    EvidenceDossier,
    LcaEvidence,
    OverallCompliance,
    Surface,
    Verdict,
)
from app.models.schemas import AuditContext


class InMemoryCertificateRegistry:
    def __init__(self, records):
        self.records = {record.license_number: record for record in records}

    def find(self, license_number):
        return self.records.get(license_number)


def packaging_context(**overrides):
    values = {
        "as_of_date": date(2026, 10, 1),
        "jurisdiction": "FR",
        "surface": Surface.PACKAGING,
        "consumer_facing": True,
        "product_identifier": "SKU-1",
        "product_category": "packaging",
    }
    values.update(overrides)
    return AuditContext(**values)


def test_biodegradable_is_strictly_prohibited_even_with_verified_ecolabel():
    record = RegistryRecord(
        scheme="EU_ECOLABEL",
        license_number="EU/001/123",
        valid_from=date(2025, 1, 1),
        valid_until=None,
        product_identifiers=("SKU-1",),
        product_categories=("packaging",),
        relevant_claim_types=("generic_environmental", "biodegradable"),
        issuer="Commission registry snapshot",
        registry_name="test registry",
    )
    validator = ProofValidator(InMemoryCertificateRegistry([record]))
    engine = InferenceEvaluator(validator, fr_2024_825_transposition_status="implemented")
    claim = FactExtractor().extract("Bouteille 100% biodégradable.")[0]
    dossier = EvidenceDossier(
        items=[
            EcolabelEvidence(
                kind="ecolabel_certificate",
                scheme="EU_ECOLABEL",
                license_number="EU/001/123",
                product_identifier="SKU-1",
                product_category="packaging",
            )
        ]
    )

    findings = engine.evaluate_claim_all(claim, dossier, packaging_context())
    agec = next(item for item in findings if item.rule_id == "RULE_AGEC_BIODEGRADABLE")

    assert agec.verdict == Verdict.STRICTLY_PROHIBITED
    assert agec.is_legal_violation is True
    assert agec.safe_harbor_applicable is False
    assert agec.sanction is not None
    assert agec.sanction.max_legal_person_eur == 15000
    # "Biodégradable" is a specific attribute, not automatically treated as
    # a generic environmental claim under point 4a. Its French AGEC ban remains.
    assert all(item.rule_id != "RULE_EU_GENERIC_CLAIM" for item in findings)


def test_current_agec_rule_uses_current_r541_230_and_15k_company_cap():
    engine = InferenceEvaluator()
    claim = FactExtractor().extract("Emballage biodégradable.")[0]
    result = engine.evaluate_claim(claim, EvidenceDossier(), packaging_context())

    assert result.rule_id == "RULE_AGEC_BIODEGRADABLE"
    assert "R. 541-230" in result.law_reference
    assert result.sanction is not None
    assert result.sanction.max_legal_person_eur == 15000
    assert "Article L. 541-9-4-1" in result.sanction.legal_basis


def test_missing_lca_for_quantified_co2_claim_is_conditional_reject():
    engine = InferenceEvaluator()
    claims = FactExtractor().extract("Émissions CO₂ réduites de 40%.")
    quantitative = next(item for item in claims if item.claim_type.value == "quantified_climate")
    finding = next(
        item
        for item in engine.evaluate_claim_all(quantitative, EvidenceDossier(), packaging_context())
        if item.rule_id == "RULE_EVIDENCE_QUANTIFIED_CLAIM"
    )

    assert finding.verdict == Verdict.CONDITIONAL_REJECT
    assert finding.is_legal_violation is False
    assert any(check.status.value == "NOT_PROVIDED" for check in finding.evidence_checks)
    assert "ISO 14044" in " ".join(finding.required_evidence)


def test_generic_claim_requires_registry_verified_relevant_ecolabel():
    record = RegistryRecord(
        scheme="EU_ECOLABEL",
        license_number="EU/FR/42",
        valid_from=date(2024, 1, 1),
        valid_until=date(2027, 1, 1),
        product_identifiers=("SKU-1",),
        product_categories=("packaging",),
        relevant_claim_types=("generic_environmental",),
        issuer="EU Ecolabel competent body",
        registry_name="official snapshot",
    )
    engine = InferenceEvaluator(
        ProofValidator(InMemoryCertificateRegistry([record])),
        fr_2024_825_transposition_status="implemented",
    )
    claim = FactExtractor().extract("Produit éco-conçu.")[0]
    dossier = EvidenceDossier(
        items=[
            EcolabelEvidence(
                kind="ecolabel_certificate",
                scheme="EU_ECOLABEL",
                license_number="EU/FR/42",
                product_identifier="SKU-1",
                product_category="packaging",
            )
        ]
    )
    result = engine.evaluate_claim(claim, dossier, packaging_context())

    assert result.rule_id == "RULE_EU_GENERIC_CLAIM"
    assert result.safe_harbor_applicable is True
    assert result.verdict == Verdict.COMPLIANT


def test_specific_detail_does_not_automatically_exempt_generic_claim():
    engine = InferenceEvaluator(fr_2024_825_transposition_status="implemented")
    claim = next(
        item
        for item in FactExtractor().extract("Produit écologique, baisse de 30% de CO2 sur son cycle de vie.")
        if item.claim_type.value == "generic_environmental"
    )
    result = engine.evaluate_claim(claim, EvidenceDossier(), packaging_context())

    assert result.verdict == Verdict.REVIEW_REQUIRED
    assert result.is_legal_violation is False
    assert "même support" in (result.legal_caveat or "")


def test_client_supplied_certificate_number_is_not_trusted_by_default():
    engine = InferenceEvaluator(fr_2024_825_transposition_status="implemented")
    claim = FactExtractor().extract("Produit écologique.")[0]
    dossier = EvidenceDossier(
        items=[
            EcolabelEvidence(
                kind="ecolabel_certificate",
                scheme="EU_ECOLABEL",
                license_number="USER-SUPPLIED-123",
                product_identifier="SKU-1",
                product_category="packaging",
            )
        ]
    )
    result = engine.evaluate_claim(claim, dossier, packaging_context())

    assert result.safe_harbor_applicable is False
    assert result.verdict == Verdict.REVIEW_REQUIRED
    assert result.evidence_checks[0].status.value == "NOT_INDEPENDENTLY_VERIFIED"


def test_carbon_offset_claim_is_date_gated_and_fr_regime_is_separately_checked():
    engine = InferenceEvaluator(fr_2024_825_transposition_status="implemented")
    text = "Emballage neutre en carbone grâce à nos crédits carbone."
    claim = next(item for item in FactExtractor().extract(text) if item.claim_type.value == "carbon_neutrality")

    before = engine.evaluate_claim_all(
        claim,
        EvidenceDossier(),
        packaging_context(as_of_date=date(2026, 9, 24)),
    )
    eu_before = next(item for item in before if item.rule_id == "RULE_EU_CARBON_NEUTRAL_COMPENSATION")
    france_before = next(item for item in before if item.rule_id == "RULE_FR_CARBON_NEUTRAL_DISCLOSURE")
    assert eu_before.verdict == Verdict.UPCOMING
    assert france_before.verdict == Verdict.CONDITIONAL_REJECT
    assert france_before.sanction is not None
    assert france_before.sanction.max_legal_person_eur == 100000

    after = engine.evaluate_claim_all(
        claim,
        EvidenceDossier(),
        packaging_context(as_of_date=date(2026, 9, 28)),
    )
    eu_after = next(item for item in after if item.rule_id == "RULE_EU_CARBON_NEUTRAL_COMPENSATION")
    assert eu_after.verdict == Verdict.STRICTLY_PROHIBITED
    assert eu_after.is_legal_violation is True


def test_unconfirmed_french_transposition_prevents_automatic_eu_violation_finding():
    engine = InferenceEvaluator(fr_2024_825_transposition_status="unknown")
    claim = FactExtractor().extract("Produit écologique.")[0]
    result = engine.evaluate_claim(claim, EvidenceDossier(), packaging_context())

    assert result.verdict == Verdict.REVIEW_REQUIRED
    assert result.is_legal_violation is False
    assert any(step.code == "NATIONAL_TRANSPOSITION_CHECK" for step in result.reasoning_steps)


def test_comparative_lca_is_an_advisory_gate_not_an_enacted_law_violation():
    engine = InferenceEvaluator()
    claim = next(
        item
        for item in FactExtractor().extract("2 fois moins polluant que le modèle précédent.")
        if item.claim_type.value == "comparative"
    )
    result = engine.evaluate_claim(claim, EvidenceDossier(), packaging_context())

    assert result.rule_id == "RULE_EU_COMPARATIVE_LCA"
    assert result.verdict == Verdict.CONDITIONAL_REJECT
    assert result.is_legal_violation is False
    assert result.legal_force.value == "PROPOSAL_ONLY"
    assert result.sanction is None


def test_recyclable_without_real_local_route_is_held_for_proof():
    engine = InferenceEvaluator()
    claim = FactExtractor().extract("Emballage recyclable partout.")[0]
    result = engine.evaluate_claim(claim, EvidenceDossier(), packaging_context())

    assert result.rule_id == "RULE_ISO_RECYCLABLE_PERCENTAGE"
    assert result.verdict == Verdict.CONDITIONAL_REJECT
    assert result.is_legal_violation is False
    assert "collecte" in " ".join(result.required_evidence)


def test_negative_claim_is_not_treated_as_an_affirmative_violation():
    engine = InferenceEvaluator()
    claim = FactExtractor().extract("Bouteille non biodégradable.")[0]
    result = engine.evaluate_claim(claim, EvidenceDossier(), packaging_context())

    assert claim.affirmative is False
    assert result.verdict == Verdict.NOT_APPLICABLE
    assert result.is_legal_violation is False


def test_api_returns_auditable_structured_report():
    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/engine/evaluate",
            json={
                "source_text": "Bouteille 100% biodégradable.",
                "context": {
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "surface": "packaging",
                    "consumer_facing": True,
                    "product_identifier": "SKU-1",
                },
                "evidence": {"items": [], "legal_person": True},
            },
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["overall_compliance"] == OverallCompliance.NON_COMPLIANT.value
    assert data["violations_count"] >= 1
    agec = next(item for item in data["evaluations"] if item["rule_id"] == "RULE_AGEC_BIODEGRADABLE")
    assert agec["verdict"] == Verdict.STRICTLY_PROHIBITED.value
    assert data["audit_trail"]["source_sha256"]
    assert data["audit_trail"]["record_hash"]


def test_api_extracts_uploaded_text_and_returns_it_for_highlighting():
    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/engine/evaluate",
            data={
                "context_json": json.dumps({
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "surface": "packaging",
                    "consumer_facing": True,
                    "product_identifier": "SKU-1",
                }),
                "evidence_json": json.dumps({"items": [], "legal_person": True}),
            },
            files={"document": ("etiquette.txt", "Bouteille biodégradable.".encode("utf-8"), "text/plain")},
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["extracted_source_text"] == "Bouteille biodégradable."
    assert data["audit_trail"]["extraction_method"] == "TEXT_FILE"
    assert data["audit_trail"]["document_sha256"]


def test_export_audit_pdf_returns_valid_pdf():
    from app.main import app

    with TestClient(app) as client:
        eval_resp = client.post(
            "/api/v1/engine/evaluate",
            json={
                "source_text": "Packaging 100% biodégradable et neutre en carbone.",
                "context": {
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "surface": "packaging",
                    "consumer_facing": True,
                    "product_identifier": "SKU-999",
                },
                "evidence": {"items": [], "legal_person": True},
            },
        )
        assert eval_resp.status_code == 200, eval_resp.text
        report_data = eval_resp.json()

        pdf_resp = client.post("/api/v1/engine/export/pdf", json=report_data)
        assert pdf_resp.status_code == 200, pdf_resp.text
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert "attachment" in pdf_resp.headers["content-disposition"]
        assert pdf_resp.content.startswith(b"%PDF")
        assert len(pdf_resp.content) > 3000


def test_chemical_free_claim_is_strictly_prohibited_as_deceptive_practice():
    engine = InferenceEvaluator()
    claims, findings = engine.evaluate_text(
        "Nettoyant ménager 100% sans produits chimiques.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    assert len(claims) == 1
    assert claims[0].claim_type == ClaimType.CHEMICAL_FREE
    chem_findings = [f for f in findings if f.rule_id == "RULE_CONSUMER_CHEMICAL_FREE"]
    assert len(chem_findings) == 1
    assert chem_findings[0].verdict == Verdict.STRICTLY_PROHIBITED
    assert chem_findings[0].is_legal_violation is True
    assert chem_findings[0].sanction is not None
    assert chem_findings[0].sanction.max_legal_person_eur == Decimal("1500000")


def test_oxodegradable_is_strictly_prohibited():
    engine = InferenceEvaluator()
    claims, findings = engine.evaluate_text(
        "Emballage en plastique oxo-dégradable.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    assert len(claims) == 1
    assert claims[0].claim_type == ClaimType.BIODEGRADABLE
    agec_finding = [f for f in findings if f.rule_id == "RULE_AGEC_BIODEGRADABLE"][0]
    assert agec_finding.verdict == Verdict.STRICTLY_PROHIBITED
    assert agec_finding.is_legal_violation is True


def test_unquantified_recycled_content_claim_is_violation_under_agec():
    engine = InferenceEvaluator()
    # Unquantified claim: "en plastique recyclé" -> NON_COMPLIANT under R. 541-227
    _, unquantified_findings = engine.evaluate_text(
        "Flacon fabriqué en plastique recyclé.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    recycled_finding = [f for f in unquantified_findings if f.rule_id == "RULE_AGEC_RECYCLED_UNQUANTIFIED"][0]
    assert recycled_finding.verdict == Verdict.NON_COMPLIANT
    assert recycled_finding.is_legal_violation is True

    # Quantified claim: "comporte au moins 50% de plastique recyclé" -> REVIEW_REQUIRED
    _, quantified_findings = engine.evaluate_text(
        "Flacon comporte au moins 50% de plastique recyclé.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    quant_finding = [f for f in quantified_findings if f.rule_id == "RULE_AGEC_RECYCLED_UNQUANTIFIED"][0]
    assert quant_finding.verdict == Verdict.REVIEW_REQUIRED


def test_compostable_claim_requires_home_compost_specification():
    engine = InferenceEvaluator()
    # Isolated "compostable" without specification
    _, isolated_findings = engine.evaluate_text(
        "Barquette 100% compostable.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    compost_finding = [f for f in isolated_findings if f.rule_id == "RULE_AGEC_COMPOSTABLE"][0]
    assert compost_finding.verdict == Verdict.CONDITIONAL_REJECT

    # Specified "compostable à domicile"
    _, home_findings = engine.evaluate_text(
        "Barquette compostable à domicile certifiée.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    home_finding = [f for f in home_findings if f.rule_id == "RULE_AGEC_COMPOSTABLE"][0]
    assert home_finding.verdict == Verdict.REVIEW_REQUIRED


def test_zero_waste_zero_pollution_claim_is_conditional_reject():
    engine = InferenceEvaluator()
    claims, findings = engine.evaluate_text(
        "Gamme textile zéro déchet et non-polluant.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    assert any(c.claim_type == ClaimType.ZERO_POLLUTION for c in claims)
    zero_finding = [f for f in findings if f.rule_id == "RULE_CONSUMER_ZERO_POLLUTION"][0]
    assert zero_finding.verdict == Verdict.CONDITIONAL_REJECT


def test_extended_nature_friendly_and_carbon_synonyms():
    engine = InferenceEvaluator()
    claims, _ = engine.evaluate_text(
        "Formule qui préserve la planète, favorable à la biodiversité, et 100% compensée.",
        EvidenceDossier(items=[]),
        AuditContext(jurisdiction="FR", surface=Surface.PACKAGING),
    )
    types = {c.claim_type for c in claims}
    assert ClaimType.NATURE_FRIENDLY in types
    assert ClaimType.CARBON_NEUTRALITY in types


def test_audit_history_and_retrieval():
    from app.main import app

    with TestClient(app) as client:
        # Submit an evaluation with supplier metadata
        eval_resp = client.post(
            "/api/v1/engine/evaluate",
            json={
                "source_text": "Bouteille réutilisable zéro déchet.",
                "context": {
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "surface": "packaging",
                    "consumer_facing": True,
                    "supplier_name": "EcoSupply SAS",
                    "product_identifier": "SKU-ECO-88",
                },
                "evidence": {"items": [], "legal_person": True},
            },
        )
        assert eval_resp.status_code == 200
        audit_id = eval_resp.json()["audit_trail"]["audit_id"]

        # List audits and find our audit
        history_resp = client.get("/api/v1/engine/audits", params={"supplier": "EcoSupply"})
        assert history_resp.status_code == 200
        history_data = history_resp.json()
        assert history_data["total"] >= 1
        found = [item for item in history_data["items"] if item["audit_id"] == audit_id]
        assert len(found) == 1
        assert found[0]["supplier_name"] == "EcoSupply SAS"
        assert found[0]["product_identifier"] == "SKU-ECO-88"

        # Fetch single audit
        single_resp = client.get(f"/api/v1/engine/audits/{audit_id}")
        assert single_resp.status_code == 200
        single_data = single_resp.json()
        assert single_data["audit_trail"]["audit_id"] == audit_id
        assert single_data["extracted_source_text"] == "Bouteille réutilisable zéro déchet."


def test_supplier_comparison_benchmark():
    from app.main import app

    with TestClient(app) as client:
        comp_resp = client.post(
            "/api/v1/engine/suppliers/compare",
            json={
                "submissions": [
                    {
                        "supplier_name": "Fournisseur Vertueux (BioPack)",
                        "product_identifier": "PACK-VERT-01",
                        "source_text": "Emballage en carton issu de forêts gérées durablement.",
                        "context": {
                            "as_of_date": "2026-09-24",
                            "jurisdiction": "FR",
                            "surface": "packaging",
                            "consumer_facing": True,
                        },
                        "evidence": {"items": [], "legal_person": True},
                    },
                    {
                        "supplier_name": "Fournisseur À Risque (Toxipack)",
                        "product_identifier": "PACK-RISK-02",
                        "source_text": "Emballage 100% biodégradable et sans produits chimiques.",
                        "context": {
                            "as_of_date": "2026-09-24",
                            "jurisdiction": "FR",
                            "surface": "packaging",
                            "consumer_facing": True,
                        },
                        "evidence": {"items": [], "legal_person": True},
                    },
                ]
            },
        )
        assert comp_resp.status_code == 200, comp_resp.text
        data = comp_resp.json()
        assert data["suppliers_count"] == 2
        ranked = data["ranked_suppliers"]
        # BioPack should rank #1
        assert ranked[0]["supplier_name"] == "Fournisseur Vertueux (BioPack)"
        assert ranked[0]["rank"] == 1
        # Toxipack should rank #2 with critical non-compliance
        assert ranked[1]["supplier_name"] == "Fournisseur À Risque (Toxipack)"
        assert ranked[1]["rank"] == 2
        assert ranked[1]["overall_compliance"] == "NON_COMPLIANT"
        assert ranked[1]["recommendation_color"] == "red"
        assert ranked[1]["violations_count"] >= 2
        assert data["best_supplier"] == "Fournisseur Vertueux (BioPack)"


def test_live_ecolabel_connector_recognizes_official_licenses():
    from app.engine.ecolabel_connector import LiveEcolabelConnector

    connector = LiveEcolabelConnector()
    # Check preloaded official licenses
    eu_rec = connector.find("FR/012/345")
    assert eu_rec is not None
    assert eu_rec.scheme == "EU_ECOLABEL"
    assert eu_rec.officially_recognised is True

    nfe_rec = connector.find("NFE/75/001")
    assert nfe_rec is not None
    assert nfe_rec.scheme == "EN_ISO_14024_TYPE_I"
    assert "AFNOR" in nfe_rec.issuer

    # Live verification method
    verified = connector.verify_live("FR/012/345", scheme="EU_ECOLABEL")
    assert verified["verified"] is True
    assert verified["status"] == "OFFICIALLY_VERIFIED"
    assert verified["safe_harbor_eligible"] is True

    # Unknown fake license
    fake = connector.verify_live("FAKE-ECOLABEL-99999")
    assert fake["verified"] is False
    assert fake["status"] == "NOT_FOUND"


def test_api_ecolabel_endpoints():
    from app.main import app

    with TestClient(app) as client:
        # 1. Registries list
        reg_resp = client.get("/api/v1/engine/ecolabels/registries")
        assert reg_resp.status_code == 200
        regs = reg_resp.json()
        assert len(regs) >= 4
        reg_ids = {r["registry_id"] for r in regs}
        assert "EU_ECOLABEL_ECAT" in reg_ids
        assert "AFNOR_NF_ENVIRONNEMENT" in reg_ids

        # 2. Live verification of official license
        verif_resp = client.get("/api/v1/engine/ecolabels/verify", params={"license_number": "FR/012/345"})
        assert verif_resp.status_code == 200
        verif_data = verif_resp.json()
        assert verif_data["verified"] is True
        assert verif_data["safe_harbor_eligible"] is True
        assert "ECAT" in verif_data["registry_name"]

        # 3. Non-existent license
        unkn_resp = client.get("/api/v1/engine/ecolabels/verify", params={"license_number": "NON-EXISTENT-XYZ-999"})
        assert unkn_resp.status_code == 200
        assert unkn_resp.json()["verified"] is False

        # 4. Sync endpoint
        sync_resp = client.post("/api/v1/engine/ecolabels/sync")
        assert sync_resp.status_code == 200
        assert sync_resp.json()["status"] == "SYNCHRONIZED"


def test_live_ecolabel_grants_safe_harbor_in_end_to_end_audit():
    from app.main import app

    with TestClient(app) as client:
        eval_resp = client.post(
            "/api/v1/engine/evaluate",
            json={
                "source_text": "Produit écologique pour la maison.",
                "context": {
                    "as_of_date": "2026-10-01",
                    "jurisdiction": "FR",
                    "surface": "advertisement",
                    "consumer_facing": True,
                    "product_category": "packaging",
                },
                "evidence": {
                    "items": [
                        {
                            "kind": "ecolabel_certificate",
                            "scheme": "EU_ECOLABEL",
                            "license_number": "FR/012/345",
                            "product_category": "packaging",
                        }
                    ],
                    "legal_person": True,
                },
            },
        )
        assert eval_resp.status_code == 200, eval_resp.text
        data = eval_resp.json()
        # Find EU generic claim evaluation
        eu_finding = [ev for ev in data["evaluations"] if ev["rule_id"] == "RULE_EU_GENERIC_CLAIM"][0]
        # Must grant Safe Harbor because FR/012/345 is verified in live connector!
        assert eu_finding["safe_harbor_applicable"] is True
        assert eu_finding["safe_harbor_reason"] is not None
        assert "ECAT" in eu_finding["safe_harbor_reason"]


def test_url_scraper_extracts_html_and_blocks_ssrf():
    import pytest
    from app.engine.url_scraper import EcommerceUrlScraper, UrlScraperError, is_safe_url

    # SSRF verification
    assert is_safe_url("http://localhost:8000") is False
    assert is_safe_url("http://127.0.0.1/admin") is False
    assert is_safe_url("http://169.254.169.254/latest/meta-data") is False
    assert is_safe_url("https://boutique-bio.fr/produit-123") is True

    # Offline mock e-commerce scraping
    scraper = EcommerceUrlScraper()
    import asyncio
    text, doc_hash, meta = asyncio.run(scraper.scrape("https://demo-shop.vericlaim.ai/produit/gourde-verte"))
    assert "Gourde Isotherme" in text
    assert "biodégradable" in text
    assert len(doc_hash) == 64
    assert meta["page_title"]

    # Blocked URL error
    with pytest.raises(UrlScraperError):
        asyncio.run(scraper.scrape("http://127.0.0.1:9000/internal"))


def test_api_evaluate_url_endpoint():
    from app.main import app

    with TestClient(app) as client:
        # 1. Successful audit of e-commerce product URL
        resp = client.post(
            "/api/v1/engine/evaluate/url",
            json={
                "url": "https://demo-shop.vericlaim.ai/produit/gourde-verte",
                "context": {
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "consumer_facing": True,
                },
                "evidence": {"items": [], "legal_person": True},
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["audit_trail"]["extraction_method"] == "URL_SCRAPER"
        assert data["overall_compliance"] == "NON_COMPLIANT"
        assert data["violations_count"] >= 1
        assert "Gourde Isotherme" in data["extracted_source_text"]

        # 2. Rejection of forbidden / loopback URL
        bad_resp = client.post(
            "/api/v1/engine/evaluate/url",
            json={
                "url": "http://127.0.0.1:8000/etc/passwd",
            },
        )
        assert bad_resp.status_code == 422
        assert "pas autorisée" in bad_resp.json()["detail"]


def test_catalog_batch_evaluation_and_csv_import_export():
    from app.main import app
    import io

    with TestClient(app) as client:
        # 1. Batch JSON evaluation
        batch_payload = {
            "items": [
                {
                    "sku": "SKU-GREENWASH-01",
                    "title": "Gourde Nomade Verte",
                    "text": "Bouteille 100% biodégradable et sans déchet pour la planète.",
                    "surface": "packaging",
                    "supplier_name": "EcoPlast Inc",
                },
                {
                    "sku": "SKU-COMPLIANT-02",
                    "title": "Bocal Verre Consigné",
                    "text": "Réduction de 25% de CO2 certifiée par ACV ISO 14044 et licence FR/012/345.",
                    "surface": "packaging",
                    "supplier_name": "VerreDurable SAS",
                    "has_lca": True,
                    "ecolabel_license": "FR/012/345",
                },
            ],
            "jurisdiction": "FR",
            "consumer_facing": True,
        }
        resp = client.post("/api/v1/engine/evaluate/batch", json=batch_payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_items"] == 2
        assert data["non_compliant_items"] >= 1
        assert data["total_fines_ceiling_eur"] > 0
        assert len(data["results"]) == 2

        # 2. Batch CSV Upload
        csv_content = (
            "sku;title;text;surface;supplier\n"
            "SKU-CSV-1;Boîte Cartonnée;Emballage 100% biodégradable;packaging;Fournisseur A\n"
            "SKU-CSV-2;Flacon Savon;Formule certifiée par licence FR/012/345;online_store;Fournisseur B\n"
        )
        files = {"file": ("catalog.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
        csv_resp = client.post("/api/v1/engine/evaluate/batch-csv", files=files)
        assert csv_resp.status_code == 200, csv_resp.text
        csv_data = csv_resp.json()
        assert csv_data["total_items"] == 2
        assert csv_data["results"][0]["sku"] == "SKU-CSV-1"

        # 3. Export Batch CSV
        export_resp = client.post("/api/v1/engine/export/batch-csv", json=csv_data)
        assert export_resp.status_code == 200
        assert "text/csv" in export_resp.headers["content-type"]
        exported_text = export_resp.content.decode("utf-8-sig")
        assert "SKU-CSV-1" in exported_text
        assert "Statut Conformité" in exported_text


def test_rate_limiting_and_anti_abuse_protection():
    from app.main import app
    from app.core.limiter import get_client_identifier
    from fastapi import Request

    # 1. Identifier logic supports API keys and IP fallback
    req_with_key = Request(scope={"type": "http", "headers": [(b"x-api-key", b"test-client-123")]})
    assert get_client_identifier(req_with_key) == "apikey:test-client-123"

    # 2. Rate limit rejection returning 429 Too Many Requests
    with TestClient(app) as client:
        headers = {"X-API-Key": "burst-test-unique-ip-1"}
        r1 = client.get("/api/v1/engine/rate-limit-check", headers=headers)
        assert r1.status_code == 200, r1.text
        assert r1.json()["ratelimited"] is True

        r2 = client.get("/api/v1/engine/rate-limit-check", headers=headers)
        assert r2.status_code == 200

        r3 = client.get("/api/v1/engine/rate-limit-check", headers=headers)
        assert r3.status_code == 429
        assert "rate limit exceeded" in r3.text.lower() or "too many requests" in r3.text.lower()


def test_observability_metrics_and_health_probes():
    from app.main import app

    with TestClient(app) as client:
        # 1. Detailed Healthcheck
        h_resp = client.get("/healthz")
        assert h_resp.status_code == 200
        h_data = h_resp.json()
        assert h_data["status"] == "healthy"
        assert h_data["database"]["status"] == "ok"
        assert h_data["engine"]["status"] == "ready"
        assert h_data["engine"]["loaded_rules_count"] >= 10
        assert h_data["ecolabel_connector"]["registries_count"] >= 4

        # 2. Kubernetes Liveness & Readiness probes
        live_resp = client.get("/livez")
        assert live_resp.status_code == 200
        assert live_resp.json()["status"] == "alive"

        ready_resp = client.get("/readyz")
        assert ready_resp.status_code == 200
        assert ready_resp.json()["status"] == "ready"

        # 3. Prometheus Metrics Endpoint
        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200
        assert "text/plain" in metrics_resp.headers["content-type"]
        metrics_body = metrics_resp.text
        assert "vericlaim_evaluations_total" in metrics_body
        assert "vericlaim_evaluation_duration_seconds" in metrics_body

        # 4. Correlation ID header injection
        cid_resp = client.get("/healthz", headers={"X-Request-ID": "corr-uuid-test-999"})
        assert cid_resp.headers.get("X-Request-ID") == "corr-uuid-test-999"


def test_dynamic_javascript_rendering_and_spa_extraction():
    import asyncio
    from app.engine.url_scraper import EcommerceUrlScraper
    from app.main import app

    scraper = EcommerceUrlScraper()
    spa_url = "https://demo-shop.vericlaim.ai/produit/spa-react-eco-creme"

    # 1. Direct scraper extraction with JS rendering enabled
    text, doc_hash, meta = asyncio.run(scraper.scrape(spa_url, render_js=True))
    assert meta["dynamic_js_rendering"] is True
    assert "JSON_LD_PRODUCT" in meta["dynamic_extraction_methods"]
    assert "CLIENT_HYDRATION_STORE" in meta["dynamic_extraction_methods"]
    assert "Crème Solaire Minérale Bio" in text
    assert "sans produits chimiques" in text.lower()
    assert "biodégradable" in text.lower()

    # 2. End-to-end API audit of dynamic headless SPA
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/engine/evaluate/url",
            json={
                "url": spa_url,
                "render_js": True,
                "context": {
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "consumer_facing": True,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["overall_compliance"] == "NON_COMPLIANT"
        assert data["violations_count"] >= 1
        assert any(
            "sans produits chimiques" in ev["claim_text"].lower()
            or "biodégradable" in ev["claim_text"].lower()
            for ev in data["evaluations"]
        )


def test_multilingual_claims_english_and_german():
    from app.engine.fact_extractor import FactExtractor
    from app.models.legal_types import ClaimType
    from app.main import app

    extractor = FactExtractor()

    # 1. English Lexicon Extraction
    en_text = (
        "Packaging 100% biodegradable and chemical-free. "
        "Carbon neutral product certified by verified carbon offset credits."
    )
    en_claims = extractor.extract(en_text)
    en_types = {c.claim_type for c in en_claims}
    assert ClaimType.BIODEGRADABLE in en_types
    assert ClaimType.CHEMICAL_FREE in en_types
    assert ClaimType.CARBON_NEUTRALITY in en_types

    carbon_claim = next(c for c in en_claims if c.claim_type == ClaimType.CARBON_NEUTRALITY)
    assert carbon_claim.has_offsetting_signal is True

    # 2. German Lexicon Extraction
    de_text = (
        "Verpackung 100% biologisch abbaubar und absolut chemiefrei. "
        "Klimaneutral durch Klimakompensation und aus recyceltem Plastik hergestellt."
    )
    de_claims = extractor.extract(de_text)
    de_types = {c.claim_type for c in de_claims}
    assert ClaimType.BIODEGRADABLE in de_types
    assert ClaimType.CHEMICAL_FREE in de_types
    assert ClaimType.CARBON_NEUTRALITY in de_types
    assert ClaimType.RECYCLED_CONTENT in de_types

    de_carbon = next(c for c in de_claims if c.claim_type == ClaimType.CARBON_NEUTRALITY)
    assert de_carbon.has_offsetting_signal is True

    # 3. End-to-end API audit on English copy
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/engine/evaluate",
            json={
                "source_text": "Bottle 100% biodegradable and zero waste.",
                "context": {
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "surface": "packaging",
                    "consumer_facing": True,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_compliance"] == "NON_COMPLIANT"
        assert data["violations_count"] >= 1
