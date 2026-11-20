"""C10 acceptance: deterministic confidence scoring and human review.

The audit measured two things in the delivered product: `confidence_score` was
`NULL` on every claim, and `review_required` was raised only for OCR segments. A
client therefore could not tell a solid detection from a fragile one — and an
OCR-free document never asked for a human at all.

The plan asks for a score that is **deterministic and justified, never
probabilistic**, and for four published levels. These tests run the real pipeline
(API enqueue → durable worker → persisted verdicts → API read) and check the
published contract, not the internals.

**What a green run does not mean.** The rubric measures how much of a detected
claim is actually readable by an auditor (anchored multi-word phrase, figures,
certificate number, polarity). It is **not** the probability that a claim is
misleading, nor a risk assessment. The published `confidence_basis` string says so
in every payload, and `test_the_published_basis_refuses_to_be_a_probability`
checks it. No model, no training, no randomness is involved — the score grid test
is there to make that falsifiable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analyses.service import (
    claim_next_analysis_detection_job,
    process_claimed_analysis_detection,
)
from app.core.config import settings
from app.core.database import SessionLocal, create_tables
from app.engine.detection_confidence import (
    CONFIDENCE_BASIS,
    CONFIDENCE_RUBRIC_VERSION,
    HIGH_THRESHOLD,
    MEDIUM_THRESHOLD,
    OCR_SCORE_CEILING,
    DetectionConfidenceLevel,
    score_claim,
)
from app.engine.fact_extractor import FactExtractor
from app.identity.service import set_db_request_context
from app.main import app
from app.models.domain import (
    Document,
    DocumentSegment,
    DocumentVersion,
    ExtractionStatus,
    SegmentType,
)
from tests.auth_support import authenticate_client

CERTIFICATION_TEXT = (
    "Notre flacon contient 100 % de matière recyclée certifiée FSC n° FSC-C012345 par Ecocert."
)
NEGATED_TEXT = "Cet emballage ne contient pas de matière recyclée."
MIXED_POLARITY_TEXT = (
    "Cet emballage est biodégradable. Attention : il n'est pas biodégradable sous 12 semaines."
)
LEVELS = {level.value for level in DetectionConfidenceLevel}


@pytest.fixture(scope="module")
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def identity(client):
    return authenticate_client(client, role_code="analyst")


def _seed_document(
    organization_id,
    *,
    text: str,
    ocr: bool = False,
    extraction_review: bool = False,
) -> object:
    """One extracted document version holding ``text`` in a single segment."""
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        document = Document(
            organization_id=organization_id,
            document_key=f"C10-DOC-{suffix}",
            title="Déclaration fournisseur (C10)",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=organization_id,
            document_id=document.id,
            version_number=1,
            source_filename=f"{suffix}.txt",
            content_type="text/plain",
            storage_key=f"organizations/{organization_id}/{suffix}.txt",
            sha256="a" * 64,
            size_bytes=len(text),
            source_language="fr",
            extraction_status=(
                ExtractionStatus.REVIEW_REQUIRED if extraction_review else ExtractionStatus.COMPLETED
            ),
            extraction_engine_version="vericlaim-document-extractor-v1",
            extracted_text_sha256="b" * 64,
        )
        db.add(version)
        db.flush()
        db.add(
            DocumentSegment(
                organization_id=organization_id,
                document_version_id=version.id,
                sequence_number=0,
                page_number=1,
                segment_type=SegmentType.IMAGE_OCR if ocr else SegmentType.PARAGRAPH,
                text=text,
                start_offset=0,
                end_offset=len(text),
                source_sha256=version.sha256,
            )
        )
        db.commit()
        return version.id


def _analyse(client, identity, *, text: str, ocr: bool = False, extraction_review: bool = False) -> dict:
    """Run the real worker once and return the published snapshot."""
    version_id = _seed_document(
        identity.organization_id, text=text, ocr=ocr, extraction_review=extraction_review
    )
    created = client.post(
        "/api/v1/analyses",
        json={"document_version_ids": [str(version_id)]},
        headers={"Idempotency-Key": f"c10-{uuid4().hex[:12]}"},
    )
    assert created.status_code == 202, created.text
    analysis_id = created.json()["analysis"]["id"]

    with SessionLocal() as db:
        set_db_request_context(
            db, user_id=identity.user_id, organization_id=identity.organization_id
        )
        claim = claim_next_analysis_detection_job(
            db,
            settings=settings,
            organization_id=identity.organization_id,
            worker_id="c10-test-worker",
        )
        assert claim is not None, "aucun job de détection à traiter"
        process_claimed_analysis_detection(db, settings=settings, claim=claim)
        db.commit()

    snapshot = client.get(f"/api/v1/analyses/{analysis_id}/versions/1")
    assert snapshot.status_code == 200, snapshot.text
    body = snapshot.json()
    version_id = body["version"]["id"]
    result = body["version"].get("result") or {}
    verdicts = [
        item for item in (result.get("verdicts") or [])
    ]
    claims = body["claims"]
    claim_ids = {claim["id"] for claim in claims}
    return {
        "analysis_id": analysis_id,
        "version_id": version_id,
        "claims": claims,
        "verdicts": [item for item in verdicts if item.get("claim_id") in claim_ids],
        "result": result,
        "confidence_summary": result.get("confidence_summary") or {},
    }


def _only_claim(snapshot: dict, claim_type: str | None = None) -> dict:
    claims = snapshot["claims"]
    if claim_type is not None:
        claims = [claim for claim in claims if claim["claim_type"] == claim_type]
    assert len(claims) == 1, claims
    return claims[0]


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


def test_every_detected_claim_publishes_a_score_level_and_factors(client, identity):
    snapshot = _analyse(client, identity, text=CERTIFICATION_TEXT)
    assert snapshot["claims"], "aucune allégation détectée : le test ne mesurerait rien"
    for claim in snapshot["claims"]:
        assert claim["confidence_score"] is not None, claim
        assert 0.0 < claim["confidence_score"] <= 1.0
        assert claim["confidence_level"] in LEVELS
        assert claim["confidence_factors"], "un score sans facteur publié n'est pas justifiable"
        assert claim["confidence_rubric_version"] == CONFIDENCE_RUBRIC_VERSION
        for factor in claim["confidence_factors"]:
            assert factor["code"] and factor["detail"]
            assert isinstance(factor["effect"], (int, float))


def test_the_published_basis_refuses_to_be_a_probability(client, identity):
    """The number must never be readable as a probability or a legal risk score."""
    snapshot = _analyse(client, identity, text=CERTIFICATION_TEXT)
    assert snapshot["result"]["confidence_summary"]["basis"] == CONFIDENCE_BASIS
    for claim in snapshot["claims"]:
        basis = claim["confidence_basis"]
        assert basis == CONFIDENCE_BASIS
        assert "probabilité" in basis
        assert "ni une appréciation du risque juridique" in basis


def test_the_result_json_publishes_the_same_confidence_as_the_api(client, identity):
    """Two published surfaces carry the claims; both must carry the same rubric.

    The persisted result JSON is the one a report and a dossier are rendered
    from, so a score that exists only on the claim endpoint would be a score the
    PDF never shows.
    """
    snapshot = _analyse(client, identity, text=CERTIFICATION_TEXT)
    # The persisted result JSON is read from the version snapshot. Note that
    # ``GET /api/v1/analyses/{id}`` strips ``result.claims`` (a measured
    # inconsistency between the two read surfaces, recorded in C10_evidence.md).
    result_claims = (snapshot["result"] or {}).get("claims") or []
    assert result_claims, "le résultat persisté ne publie aucune allégation"

    by_id = {claim["id"]: claim for claim in snapshot["claims"]}
    assert {claim["id"] for claim in result_claims} == set(by_id)
    for claim in result_claims:
        api_claim = by_id[claim["id"]]
        assert claim["confidence_score"] == api_claim["confidence_score"]
        assert claim["confidence_level"] == api_claim["confidence_level"]
        assert claim["confidence_factors"] == api_claim["confidence_factors"]
        assert claim["review_reasons"] == api_claim["review_reasons"]
        assert claim["confidence_basis"] == api_claim["confidence_basis"]
        assert claim["confidence_factors"], "le résultat persisté publie un score sans facteur"


def test_the_score_is_the_sum_of_its_published_factors(client, identity):
    """The justification must be arithmetic, not decorative.

    The OCR cap is the one documented exception: it is a ceiling, not a term, and
    the OCR claims are excluded here on purpose — they have their own test.
    """
    snapshot = _analyse(client, identity, text=CERTIFICATION_TEXT)
    checked = 0
    for claim in snapshot["claims"]:
        factors = claim["confidence_factors"]
        if any(factor["code"] == "ocr_segment" for factor in factors):
            continue
        expected = round(sum(factor["effect"] for factor in factors), 4)
        assert claim["confidence_score"] == pytest.approx(expected, abs=1e-4), claim
        checked += 1
    assert checked == len(snapshot["claims"]) > 0


def test_the_score_grid_proves_a_rubric_not_a_model():
    """Every corpus score is a multiple of 0.05: no model output can look like this."""
    from pathlib import Path
    import json

    corpus = json.loads(
        (Path(__file__).parent / "corpus" / "claim_detection_corpus.json").read_text(
            encoding="utf-8"
        )
    )
    extractor = FactExtractor()
    scored = 0
    for case in corpus["positive_cases"]:
        for fact in extractor.extract(case["text"]):
            score = score_claim(fact).score
            assert 0.0 < score <= 0.95, (case["id"], score)
            assert abs(score * 20 - round(score * 20)) < 1e-6, (case["id"], score)
            scored += 1
    assert scored > 60, f"seulement {scored} allégations évaluées"


def test_the_rubric_is_reproducible_across_runs(client, identity):
    """Two independent analyses of the same text publish identical scores."""
    first = _analyse(client, identity, text=CERTIFICATION_TEXT)
    second = _analyse(client, identity, text=CERTIFICATION_TEXT)
    assert first["version_id"] != second["version_id"]
    first_scores = sorted(
        (claim["claim_type"], claim["confidence_score"], claim["confidence_level"])
        for claim in first["claims"]
    )
    second_scores = sorted(
        (claim["claim_type"], claim["confidence_score"], claim["confidence_level"])
        for claim in second["claims"]
    )
    assert first_scores == second_scores


# --------------------------------------------------------------------------- #
# Levels and the human-review flag
# --------------------------------------------------------------------------- #


def test_a_negated_claim_is_never_presented_as_high_confidence(client, identity):
    """Polarity is the fragile reading: C9 found a negation that was not read."""
    snapshot = _analyse(client, identity, text=NEGATED_TEXT)
    claim = _only_claim(snapshot, "recyclable")
    assert "polarity_signal" in [factor["code"] for factor in claim["confidence_factors"]]
    assert claim["confidence_score"] < HIGH_THRESHOLD
    assert claim["confidence_level"] in {"medium", "low"}

    # The penalty must exist *because* of the negation: the same claim asserted
    # affirmatively scores strictly higher. Without this comparison, a rubric that
    # simply scored everything low would pass the test above.
    affirmative = _analyse(client, identity, text="Cet emballage contient de la matière recyclée.")
    affirmative_claim = _only_claim(affirmative, "recyclable")
    assert affirmative_claim["claim_type"] == claim["claim_type"]
    assert affirmative_claim["confidence_score"] > claim["confidence_score"]
    # A negated claim is not a violation, so it must not be sent to human review
    # either: over-flagging empties the notion of "review required".
    assert claim["status"] == "detected"
    assert snapshot["result"]["review_required_claim_count"] == 0


def test_a_clean_and_quantified_claim_scores_above_a_bare_one():
    """Rubric ordering, stated as an expectation and then measured."""
    extractor = FactExtractor()
    rich_facts = [
        fact
        for fact in extractor.extract("Emballage composé de 100 % de matière recyclée.")
        if fact.claim_type.value == "recyclable"
    ]
    bare_facts = [
        fact
        for fact in extractor.extract("Produit écologique.")
        if fact.claim_type.value == "generic_environmental"
    ]
    assert rich_facts and bare_facts
    rich = score_claim(rich_facts[0]).score
    bare = score_claim(bare_facts[0]).score
    assert rich > bare, (rich, bare)


def test_an_ocr_segment_is_capped_and_always_requires_a_human(client, identity):
    snapshot = _analyse(client, identity, text=NEGATED_TEXT, ocr=True)
    claim = _only_claim(snapshot)
    assert claim["confidence_score"] <= OCR_SCORE_CEILING
    assert claim["confidence_level"] == "human_review_required"
    assert "segment_ocr" in claim["review_reasons"]
    assert claim["status"] == "review_required"
    assert snapshot["result"]["review_required_claim_count"] == 1


def test_a_document_flagged_for_extraction_review_lowers_the_score(client, identity):
    plain = _analyse(client, identity, text=NEGATED_TEXT)
    flagged = _analyse(client, identity, text=NEGATED_TEXT, extraction_review=True)
    plain_claim = _only_claim(plain)
    flagged_claim = _only_claim(flagged)
    assert "extraction_review_required" in [
        factor["code"] for factor in flagged_claim["confidence_factors"]
    ]
    assert "document_extraction_review_required" in flagged_claim["review_reasons"]
    assert flagged_claim["confidence_score"] < plain_claim["confidence_score"]
    assert flagged_claim["confidence_level"] == "human_review_required"
    assert plain_claim["confidence_level"] != "human_review_required"


def test_a_claim_whose_verdict_asks_for_review_is_marked_for_review(client, identity):
    """The certification rule cannot conclude without evidence, so the claim must
    not be published as a settled detection.

    Measured: the same claim was `detected` while its verdict said
    `REVIEW_REQUIRED` — two contradictory statements in one payload.
    """
    snapshot = _analyse(client, identity, text=CERTIFICATION_TEXT)
    claim = _only_claim(snapshot, "certification")
    verdicts = [item for item in snapshot["verdicts"] if item.get("claim_id") == claim["id"]]
    assert [item["verdict"] for item in verdicts] == ["REVIEW_REQUIRED"], verdicts
    assert claim["status"] == "review_required"
    assert "verdict_requires_review" in claim["review_reasons"]
    assert claim["confidence_level"] == "human_review_required"
    assert snapshot["result"]["review_required_claim_count"] == 1
    assert snapshot["confidence_summary"]["review_reasons"]["verdict_requires_review"] == 1


def test_a_claim_asserted_then_denied_in_the_same_segment_requires_review(client, identity):
    """A sentence that asserts a claim and then denies it must not be collapsed."""
    snapshot = _analyse(client, identity, text=MIXED_POLARITY_TEXT)
    assert len(snapshot["claims"]) == 2
    for claim in snapshot["claims"]:
        assert "polarity_conflict" in claim["review_reasons"]
        assert claim["status"] == "review_required"
        assert claim["confidence_level"] == "human_review_required"
    assert snapshot["confidence_summary"]["review_reasons"]["polarity_conflict"] == 2


def test_the_low_level_is_reachable_by_a_real_input(client, identity):
    """Every published level must be reachable, otherwise it is decoration.

    Measured while writing this file: with the first rubric the LOW rung could not
    be produced by *any* input, because every penalty below the medium threshold
    also imposed a human review and overwrote the level. The product advertised
    four levels and could only ever show three. The base score now depends on the
    trigger, which is what makes the rung real — this test fails if it stops being.
    """
    # Measured: "Produit écologique." is also LOW at detection time, but its
    # generic-claim verdict is REVIEW_REQUIRED, which correctly promotes it to
    # human review. "Flacon recyclable." keeps the LOW level end to end.
    snapshot = _analyse(client, identity, text="Flacon recyclable.")
    claim = _only_claim(snapshot, "recyclable")
    assert claim["confidence_level"] == "low"
    assert claim["confidence_score"] < MEDIUM_THRESHOLD
    assert claim["status"] == "detected"
    assert snapshot["confidence_summary"]["levels"]["low"] == 1
    # A weak detection is not a violation: no human review is imposed for that
    # reason alone, and the caller is told which level it sits on.
    assert snapshot["result"]["review_required_claim_count"] == 0


def test_the_confidence_summary_adds_up_to_the_claim_count(client, identity):
    snapshot = _analyse(client, identity, text=CERTIFICATION_TEXT)
    summary = snapshot["confidence_summary"]
    assert summary["rubric_version"] == CONFIDENCE_RUBRIC_VERSION
    assert sum(summary["levels"].values()) + summary["claims_without_rubric"] == len(
        snapshot["claims"]
    )
    assert summary["claims_without_rubric"] == 0
    assert sum(summary["review_reasons"].values()) > 0
