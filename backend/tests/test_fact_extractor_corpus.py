"""C9 acceptance: the measured detection lexicon.

The audit measured two blocking gaps: "100 % de matière recyclée" was not detected
at all, and certification claims (ECOLABEL, FSC, ISO 14001) were not a concept in
the product, so the claim → evidence chain stayed empty for the claims a buyer is
most likely to rely on. Audit case A ("allégation correctement documentée")
produced zero detections.

This module turns that measurement into a regression indicator. The reference
corpus lives in `tests/corpus/claim_detection_corpus.json` and is versioned: the
detection rate and the false-positive rate are recomputed on every run, and both
must respect the thresholds asserted here.

**What a green run does not mean.** The corpus was written by the same author as
the lexicon, so a high rate measures *non-regression on known cases*, not market
coverage. The published false-positive rate is measured on 25 curated negative
sentences, not on real client documents. Both limits are restated in
`audit/C9_evidence.md` and in the API documentation path for the UI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine.fact_extractor import FactExtractor
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.lexicon import LEXICON_VERSION
from app.engine.rule_book import RULES
from app.models.legal_types import ClaimType, EvidenceDossier
from app.models.schemas import AuditContext

# Acceptance thresholds from the plan.
MIN_DETECTION_RATE = 0.90
MAX_FALSE_POSITIVE_RATE = 0.10

CORPUS_PATH = Path(__file__).parent / "corpus" / "claim_detection_corpus.json"


@pytest.fixture(scope="module")
def corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def extractor() -> FactExtractor:
    return FactExtractor()


def _detection_results(corpus: dict, extractor: FactExtractor):
    detected, missed = [], []
    for case in corpus["positive_cases"]:
        claims = extractor.extract(case["text"])
        found = {claim.claim_type.value for claim in claims}
        affirmative = {claim.claim_type.value for claim in claims if claim.affirmative}
        expected = set(case["expected"])
        expected_affirmative = set(case.get("expect_affirmative", []))
        if expected <= found and expected_affirmative <= affirmative:
            detected.append(case["id"])
        else:
            missed.append(
                {
                    "id": case["id"],
                    "manquant": sorted(expected - found),
                    "trouvé": sorted(found),
                }
            )
    return detected, missed


def _false_positives(corpus: dict, extractor: FactExtractor):
    flagged = []
    for case in corpus["negative_cases"]:
        claims = [claim for claim in extractor.extract(case["text"]) if claim.affirmative]
        if claims:
            flagged.append(
                {
                    "id": case["id"],
                    "types": sorted({claim.claim_type.value for claim in claims}),
                    "texte": case["text"][:70],
                    "raison": case["reason"],
                }
            )
    return flagged


def test_detection_rate_on_the_reference_corpus(corpus, extractor):
    """The plan's floor (90 %) *and* the ratchet against the measured baseline.

    With a 100 % baseline, a bare 90 % threshold would absorb a six-point
    regression — a real mutation proved it: removing the recycled-content motifs
    still left 95 %. The corpus therefore stores what was measured, and the test
    requires both the plan's acceptance floor and no loss against that measurement.
    """
    detected, missed = _detection_results(corpus, extractor)
    total = len(corpus["positive_cases"])
    rate = len(detected) / total
    baseline = corpus["measurement_baseline"]
    assert rate >= MIN_DETECTION_RATE, (
        f"taux de détection {rate:.1%} ({len(detected)}/{total}) sous le seuil du plan "
        f"{MIN_DETECTION_RATE:.0%}; manques: {missed}"
    )
    assert len(missed) <= baseline["positive_total"] - baseline["detected"], (
        f"régression de détection: {len(missed)} cas manqués contre "
        f"{baseline['positive_total'] - baseline['detected']} à la mesure du "
        f"{baseline['measured_on']}; manques: {missed}"
    )


def test_false_positive_rate_on_legitimate_copy(corpus, extractor):
    """Adding patterns is easy; not firing on legitimate text is the hard part.

    The audit's own warning: every added motif is a false-positive source. This
    test is the counterweight to the detection test above.
    """
    flagged = _false_positives(corpus, extractor)
    total = len(corpus["negative_cases"])
    rate = len(flagged) / total
    baseline = corpus["measurement_baseline"]
    assert rate <= MAX_FALSE_POSITIVE_RATE, (
        f"taux de faux positifs {rate:.1%} ({len(flagged)}/{total}) au-dessus du seuil "
        f"{MAX_FALSE_POSITIVE_RATE:.0%}; cas: {flagged}"
    )
    assert len(flagged) <= baseline["false_positives"], (
        f"régression de faux positifs: {len(flagged)} contre {baseline['false_positives']} "
        f"à la mesure du {baseline['measured_on']}; cas: {flagged}"
    )


def test_the_documented_ambiguity_cases_never_fire(corpus, extractor):
    """Zero tolerance on the cases that define the design: colour, food descriptor,
    durability, conformity mark, company name, literal "empreinte".

    The published rate is capped at 10%, which would absorb a single regression. The
    ambiguity cases are therefore asserted separately: they are the reason the
    lexicon is anchored, and one of them firing is a product defect, not a statistic.
    """
    flagged = []
    for case in corpus["negative_cases"]:
        if not case.get("critical"):
            continue
        claims = [claim for claim in extractor.extract(case["text"]) if claim.affirmative]
        if claims:
            flagged.append(
                (case["id"], sorted({claim.claim_type.value for claim in claims}), case["text"][:60])
            )
    assert flagged == [], f"cas d'ambiguïté documentés déclenchés à tort: {flagged}"


def test_the_audit_case_that_detected_nothing_is_now_covered(corpus, extractor):
    """Case A is the headline failure: "100 % de matière recyclée certifiée + ACV ISO 14044"."""
    case = next(item for item in corpus["positive_cases"] if item["id"] == "A")
    found = {claim.claim_type.value for claim in extractor.extract(case["text"])}
    assert {"recyclable", "certification"} <= found, (
        "le cas A de l'audit doit produire au moins une allégation de recyclage "
        f"et une allégation de certification; trouvé: {sorted(found)}"
    )


def test_certification_claims_carry_the_three_facts_a_verifier_needs(extractor):
    """Scheme, certificate reference and body are extracted verbatim, never invented."""
    claims = extractor.extract(
        "Certification environnementale obtenue en 2025 (n° FR/012/345) délivrée par "
        "AFNOR Certification."
    )
    certification = [claim for claim in claims if claim.claim_type is ClaimType.CERTIFICATION]
    assert len(certification) == 1, f"une seule allégation de certification attendue: {claims}"
    claim = certification[0]
    assert claim.certification_reference == "FR/012/345"
    assert claim.certification_body == "AFNOR Certification"

    named = extractor.extract("Emballage certifié FSC n° FSC-C012345 par Ecocert.")
    certification = [claim for claim in named if claim.claim_type is ClaimType.CERTIFICATION]
    assert certification and certification[0].certification_scheme == "FSC"
    assert certification[0].certification_reference == "FSC-C012345"
    assert certification[0].certification_body == "Ecocert"


def test_a_conformity_mark_is_not_an_environmental_certification(extractor):
    """"certifié conforme à la norme NF EN 71" is a safety mark, not a green claim."""
    for text in (
        "Produit certifié conforme à la norme NF EN 71 sur la sécurité des jouets.",
        "Le transport est assuré par un prestataire certifié ISO 9001.",
    ):
        types = {claim.claim_type.value for claim in extractor.extract(text)}
        assert "certification" not in types, f"faux positif de certification: {text!r}"


def test_negations_are_still_read_as_negations(corpus, extractor):
    """Audit case K: correctness here was already established and must not regress."""
    case = next(item for item in corpus["positive_cases"] if item["id"] == "K")
    claims = extractor.extract(case["text"])
    assert claims, "le texte négatif doit produire des faits, pas rien du tout"
    assert all(not claim.affirmative for claim in claims), (
        "cas K: aucune occurrence ne doit être lue comme affirmative; "
        f"obtenu: {[(c.claim_type.value, c.affirmative) for c in claims]}"
    )
    assert {claim.claim_type.value for claim in claims} == {"biodegradable", "recyclable"}

    # "ne contient pas de matière recyclée": the determiner between the cue and the
    # trigger is what made the negation invisible before this chantier.
    assert any(
        claim.negation_cue and "pas" in claim.negation_cue for claim in claims
    ), "la marque de négation doit être conservée"


def test_a_mixed_text_keeps_both_polarities(corpus, extractor):
    """Audit case J: one affirmative occurrence and one negated one, same text."""
    case = next(item for item in corpus["positive_cases"] if item["id"] == "J")
    polarities = [
        claim.affirmative
        for claim in extractor.extract(case["text"])
        if claim.claim_type.value == "biodegradable"
    ]
    assert polarities == [True, False], f"polarités obtenues: {polarities}"


def test_offsets_still_point_at_the_real_trigger(corpus, extractor):
    """A cited excerpt that does not match its offsets would be a false citation."""
    for case in corpus["positive_cases"]:
        for claim in extractor.extract(case["text"]):
            assert case["text"][claim.trigger_start_offset : claim.trigger_end_offset] == claim.trigger_text, (
                f"{case['id']}: offsets incohérents pour {claim.trigger_text!r}"
            )
            assert 0 <= claim.start_offset <= claim.trigger_start_offset < claim.end_offset <= len(case["text"])


def test_detection_is_deterministic(corpus, extractor):
    """No randomness, no ordering drift: the product claims reproducibility."""
    for case in corpus["positive_cases"][:10]:
        first = [claim.model_dump() for claim in extractor.extract(case["text"])]
        second = [claim.model_dump() for claim in extractor.extract(case["text"])]
        assert first == second, f"{case['id']}: deux exécutions divergent"


def test_every_detectable_claim_type_has_a_rule():
    """The pipeline refuses a whole analysis when a claim type has no rule.

    Adding a claim type to the extractor without a Rule Book entry would not
    degrade quietly: it would fail the analysis with
    `no_rule_for_claim_type`. This test states that coupling.
    """
    covered = {claim_type for rule in RULES for claim_type in rule.claim_types}
    missing = sorted(claim_type.value for claim_type in ClaimType if claim_type not in covered)
    assert missing == [], f"types d'allégation sans règle: {missing}"


def test_the_corpus_and_the_lexicon_are_versioned(corpus):
    """A rate is only meaningful for the revision it was measured with."""
    assert corpus["version"], "le corpus doit porter une version"
    assert LEXICON_VERSION.startswith("lexicon-")
    assert len(corpus["positive_cases"]) >= 50, "le corpus doit rester significatif"
    assert len(corpus["negative_cases"]) >= 20, "le corpus négatif doit rester significatif"


def test_the_widened_lexicon_reaches_the_agec_prohibition_only_on_its_scope(extractor):
    """A consequence of C9 that must be stated, not discovered in production.

    Detecting more formulations also means more formulations reach the existing
    hard prohibitions. Measured: "Nous agissons pour le climat." — a corporate
    commitment — now triggers `RULE_AGEC_NATURE_FRIENDLY`, which is a
    STRICTLY_PROHIBITED legal violation *on a product or packaging surface*. The
    audit already found that only two AGEC rules can produce a firm violation; this
    chantier increases the number of inputs that reach them.

    The surface is the lever: the same sentence audited as an advertisement is out
    of that rule's scope. Both behaviours are pinned here so neither changes by
    accident. Treating a first-person corporate sentence as a product claim is an
    open decision, recorded in audit/C9_evidence.md.
    """
    sentence = "Nous agissons pour le climat."
    claim = extractor.extract(sentence)[0]
    evaluator = InferenceEvaluator(fr_2024_825_transposition_status="unknown")

    on_packaging = evaluator.evaluate_claim_all(
        claim, EvidenceDossier(items=[]), AuditContext(surface="packaging")
    )
    agec = [finding for finding in on_packaging if finding.rule_id == "RULE_AGEC_NATURE_FRIENDLY"]
    assert agec, "la règle AGEC doit être évaluée sur un support produit/emballage"
    assert agec[0].verdict.value == "STRICTLY_PROHIBITED"
    assert agec[0].is_legal_violation is True

    on_advertisement = evaluator.evaluate_claim_all(
        claim, EvidenceDossier(items=[]), AuditContext(surface="advertisement")
    )
    assert all(
        finding.rule_id != "RULE_AGEC_NATURE_FRIENDLY" or finding.is_legal_violation is False
        for finding in on_advertisement
    ), "hors du champ produit/emballage, aucune violation AGEC ne doit être prononcée"


def test_the_lexical_limitation_is_published_to_the_caller():
    """The plan asks for the lexicon limits to be visible in the product.

    There is no analysis UI yet (the frontend is a single page), so the published
    surface is the API itself: a client must be told that detection is lexical, that
    only listed formulations are covered, and which lexicon revision produced the
    result. An undetected claim must never read as compliance.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.auth_support import authenticate_client
    from tests.test_pdf_reporting import _persisted_analysis

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _version_id = _persisted_analysis(client, identity)
        payload = client.get(f"/api/v1/analyses/{analysis_id}")
        assert payload.status_code == 200, payload.text
        # The envelope lives on the analysed version, not on the analysis header.
        result = (payload.json().get("latest_version") or {}).get("result") or {}
        limitations = result.get("limitations") or []

    joined = " ".join(limitations)
    assert LEXICON_VERSION in joined, f"la révision du lexique doit être publiée: {limitations}"
    assert "ne couvre que les formulations listées" in joined
    assert "ne signifient pas que le support est conforme" in joined or "ne vaut pas conformité" in joined
