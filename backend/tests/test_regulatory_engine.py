from __future__ import annotations

from datetime import date
import json

from fastapi.testclient import TestClient

from app.engine.fact_extractor import FactExtractor
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.proof_validator import ProofValidator, RegistryRecord
from app.models.legal_types import (
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
