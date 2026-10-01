"""C7 acceptance: a report must be provable, and its limits must be stated.

The pre-C7 behaviour was that a "report" was just bytes: nothing tied it to a
persisted analysis, nothing said who signed it or when, nothing let a third party
check it, and a human override of a verdict was invisible in the document. A
client could not hand a report to a regulator and have it hold up.

These tests are integrity tests, not rendering tests: they forge references, tamper
with files, mutate stored signatures and stale analyses, and each one asserts that
the verification endpoints say so. A verification endpoint that always answers
"valid" would pass a smoke test and fail here.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal, create_tables
from app.main import app
from app.models.domain import AnalysisVersion, AnalysisVerdict, Report, ReportJob
from app.reports.signing import SIGNATURE_SCHEMA_VERSION
from tests.auth_support import authenticate_client
from tests.report_support import issue_report, report_storage
from tests.test_pdf_reporting import (
    _overflowing_words,
    _pdf_text,
    _persisted_analysis,
)


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def _issue_report(client: TestClient, identity, *, storage=None):
    """Génère un vrai rapport et retourne (analysis_id, référence, réponse).

    C22 : la génération se fait désormais en trois temps — demande (202), worker,
    téléchargement — et c'est le fichier téléchargé qui est vérifié ici, comme le
    ferait un client.
    """

    analysis_id, _ = _persisted_analysis(client, identity)
    response = issue_report(client, analysis_id=analysis_id, storage=storage)
    reference = response.headers["x-verification-reference"]
    return analysis_id, reference, response


def _stored_report(reference: str) -> Report:
    with SessionLocal() as db:
        report = db.scalar(
            select(Report).where(Report.verification_reference == reference)
        )
        assert report is not None, "le rapport signé n'a pas été persisté"
        return report


# --------------------------------------------------------------------------- #
# C7.1 — an authentic report verifies on all four questions
# --------------------------------------------------------------------------- #


def test_an_authentic_report_verifies_against_its_own_file():
    """The nominal path: the issued fingerprint must verify.

    This test caught a real defect. The signature covered a timezone-aware
    timestamp, but the column comes back naive from the driver, so the recomputed
    string differed from the signed one while the instant was identical: every
    honest report failed verification. It only appeared because the genuine report
    was actually verified, instead of trusting that signing and verifying agreed.
    """
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        _analysis_id, reference, response = _issue_report(client, identity, storage=storage)
        file_hash = response.headers["x-report-file-sha256"]

        verdict = client.get(
            f"/api/v1/reports/verify/{reference}", params={"sha256": file_hash}
        )
        assert verdict.status_code == 200, verdict.text
        payload = verdict.json()

        assert payload["known"] is True
        assert payload["signature_valid"] is True
        assert payload["analysis_unchanged"] is True
        assert payload["file_matches"] is True
        assert payload["rulebook_version"] == response.headers["x-rulebook-version"]
        assert payload["result_sha256"] == response.headers["x-result-sha256"]
        assert payload["signature_key_id"] == response.headers["x-report-signature-key-id"], (
            "le vérificateur doit connaître la clé employée, sans quoi la vérification n'est pas datable"
        )
        # A public verification must be datable by the caller: a naive timestamp is
        # not, and is silently wrong by the reader's offset.
        issued = datetime.fromisoformat(payload["generated_at"])
        assert issued.tzinfo is not None and issued.utcoffset() == timedelta(0), (
            f"horodatage non explicite en UTC : {payload['generated_at']}"
        )


def test_verification_is_reachable_without_an_account():
    """A third party checking a report has no VeriClaim session."""
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        _analysis_id, reference, _response = _issue_report(client, identity, storage=storage)

    # A brand new client, no cookies, no CSRF token, no organization.
    with TestClient(app) as anonymous:
        verdict = anonymous.get(f"/api/v1/reports/verify/{reference}")
        assert verdict.status_code == 200, verdict.text
        assert verdict.json()["signature_valid"] is True


# --------------------------------------------------------------------------- #
# C7.2 — the verification must refuse, not reassure
# --------------------------------------------------------------------------- #


def test_a_modified_file_does_not_match_the_issued_fingerprint():
    """A re-edited PDF must fail, while the signature itself stays honest.

    The two questions are deliberately separate, and this test pins the
    separation: the signature attests the analysis binding, the file hash attests
    the document. Merging them into one boolean would let a modified file be
    reported as "signature valid" with no visible warning.
    """
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        _analysis_id, reference, _response = _issue_report(client, identity, storage=storage)

        verdict = client.get(
            f"/api/v1/reports/verify/{reference}", params={"sha256": "f" * 64}
        )
        payload = verdict.json()
        assert payload["file_matches"] is False
        assert payload["signature_valid"] is True
        assert any("empreinte" in reason for reason in payload["reasons"])


def test_an_unknown_reference_is_never_reported_as_valid():
    """An invented handle must not be mistaken for a genuine one."""
    with TestClient(app) as client:
        verdict = client.get("/api/v1/reports/verify/vc-invented-by-the-attacker")
        assert verdict.status_code == 200
        payload = verdict.json()
        assert payload["known"] is False
        assert payload["signature_valid"] is False
        assert payload["analysis_unchanged"] is False
        assert payload["file_matches"] is None, "non vérifié n'est pas 'conforme'"


def test_a_forged_stored_signature_is_detected():
    """If the row itself is altered, verification must fail loudly."""
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        _analysis_id, reference, _response = _issue_report(client, identity, storage=storage)

        with SessionLocal() as db:
            stored = db.scalar(
                select(Report).where(Report.verification_reference == reference)
            )
            stored.signature = "0" * 64
            db.commit()

        payload = client.get(f"/api/v1/reports/verify/{reference}").json()
        assert payload["signature_valid"] is False
        assert any("signature" in reason.lower() for reason in payload["reasons"])


def test_a_stale_analysis_is_reported_as_such():
    """A report outlives the analysis it describes; the reader must know."""
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, reference, _response = _issue_report(client, identity, storage=storage)

        with SessionLocal() as db:
            version = db.scalar(
                select(AnalysisVersion).where(
                    AnalysisVersion.analysis_id == UUID(analysis_id)
                )
            )
            version.engine_version = "vericlaim-analysis-engine-v3"
            db.commit()

        payload = client.get(f"/api/v1/reports/verify/{reference}").json()
        assert payload["signature_valid"] is True, (
            "le rapport reste une pièce valable : il décrit bien la version au moment de l'émission"
        )
        assert payload["analysis_unchanged"] is False
        assert any("changé" in reason for reason in payload["reasons"])


def test_the_public_handle_does_not_expose_internal_identifiers():
    """The handle is what a client publishes; it must not leak the tenant graph."""
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, reference, _response = _issue_report(client, identity, storage=storage)

        stored = _stored_report(reference)
        assert reference.startswith("vc-")
        assert len(reference) > 24
        assert str(stored.analysis_version_id) not in reference
        assert str(stored.organization_id) not in reference
        assert analysis_id not in reference


# --------------------------------------------------------------------------- #
# C7.3 — what the document itself carries
# --------------------------------------------------------------------------- #


def test_the_pdf_prints_the_reference_that_was_signed():
    """The printed handle must be the one that verifies, or the document lies."""
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        _analysis_id, reference, response = _issue_report(client, identity, storage=storage)
        text = _pdf_text(response.content)

        assert reference in text, "la référence de vérification doit figurer sur le rapport"
        assert response.headers["x-report-signature"] in text
        assert "Vérification" in text
        # A reader must be told what the signature proves and what it does not.
        assert "ne certifie pas" in text or "pas la véracité" in text


def test_each_issued_report_gets_its_own_reference():
    """Two downloads of the same analysis are two distinct probative artefacts."""
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, first_reference, _response = _issue_report(client, identity, storage=storage)
        second = issue_report(client, analysis_id=analysis_id, storage=storage)
        second_reference = second.headers["x-verification-reference"]

        assert second_reference != first_reference
        for reference in (first_reference, second_reference):
            payload = client.get(f"/api/v1/reports/verify/{reference}").json()
            assert payload["known"] is True
            assert payload["signature_valid"] is True


def test_the_stored_row_binds_the_signature_to_the_analysis_versions():
    """The persisted binding is what makes the report replayable later."""
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, reference, response = _issue_report(client, identity, storage=storage)
        stored = _stored_report(reference)

        with SessionLocal() as db:
            version = db.scalar(
                select(AnalysisVersion).where(
                    AnalysisVersion.analysis_id == UUID(analysis_id)
                )
            )
            assert stored.signed_result_sha256 == version.result_sha256
            assert stored.signed_rulebook_version == version.rulebook_version
            assert stored.signed_engine_version == version.engine_version
            assert stored.analysis_version_id == version.id

        assert stored.signature_schema_version == SIGNATURE_SCHEMA_VERSION
        assert stored.sha256 == response.headers["x-report-file-sha256"]
        assert stored.signature_key_id == response.headers["x-report-signature-key-id"]
        assert stored.version_number >= 1


def test_a_deprecated_pipeline_is_announced_in_the_document():
    """Silently re-serving an old Rule Book's verdict is the original C6 failure."""
    from app.engine.rule_book import RULEBOOK_VERSION

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _version_id = _persisted_analysis(client, identity)
        with SessionLocal() as db:
            version = db.scalar(
                select(AnalysisVersion).where(
                    AnalysisVersion.analysis_id == UUID(analysis_id)
                )
            )
            version.rulebook_version = "2025-01-01+deadbeefcafe"
            db.commit()

        response = issue_report(client, analysis_id=analysis_id, storage=storage)
        assert response.status_code == 200
        text = _pdf_text(response.content)

        assert "DÉPRÉCIÉ" in text
        assert "2025-01-01+deadbeefcafe" in text, "l'empreinte exacte doit être citée"
        assert RULEBOOK_VERSION in text, "la version courante doit être citée"
        assert _overflowing_words(response.content) == []


def test_a_human_override_is_visible_in_the_report():
    """A verdict contested or overridden must be readable, not hidden.

    The report is the artefact a client shows a regulator. If a jurist overrode
    an automatic verdict, the document that still prints the automatic verdict
    without saying so is misleading by omission.
    """
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _version_id = _persisted_analysis(client, identity)

        with SessionLocal() as db:
            verdict_row = db.scalar(
                select(AnalysisVerdict).where(
                    AnalysisVerdict.organization_id == identity.organization_id
                ).limit(1)
            )
            assert verdict_row is not None
            version_id = str(verdict_row.analysis_version_id)
            claim_id = str(verdict_row.claim_id)

        rationale = "Biodégradabilité confirmée par essai ISO 14855 fourni au dossier."
        recorded = client.post(
            "/api/v1/validations",
            json={
                "analysis_version_id": version_id,
                "claim_id": claim_id,
                "decision": "overridden",
                "rationale": rationale,
            },
        )
        assert recorded.status_code == 201, recorded.text

        response = issue_report(client, analysis_id=analysis_id, storage=storage)
        text = _pdf_text(response.content)

        assert "REVUE HUMAINE" in text
        assert "OVERRIDDEN" in text
        assert "Biodégradabilité confirmée" in text
        assert _overflowing_words(response.content) == []


def test_the_evidence_pack_carries_the_same_verifiable_reference():
    """The ZIP is the artefact sent to a buyer; its notice must be verifiable too."""
    import zipfile

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        response = issue_report(
            client, analysis_id=analysis_id, report_format="dossier_zip", storage=storage
        )
        assert response.status_code == 200, response.text

        reference = response.headers["x-verification-reference"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            readme = next(
                (
                    archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                    if name.lower().endswith(".md") or name.lower().endswith(".txt")
                ),
                "",
            )
        assert reference in readme, "la notice du pack doit citer la référence signée"
        assert response.headers["x-report-signature"] in readme

        payload = client.get(
            f"/api/v1/reports/verify/{reference}",
            params={"sha256": response.headers["x-report-file-sha256"]},
        ).json()
        assert payload["signature_valid"] is True
        assert payload["file_matches"] is True


def test_the_report_is_still_refused_when_the_version_has_no_verdicts():
    """Signing must not become a way to make an empty analysis look provable."""
    from tests.test_pdf_reporting import _seed_analysable_document

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        version_id = _seed_analysable_document(identity.organization_id)
        created = client.post(
            "/api/v1/analyses",
            json={"document_version_ids": [str(version_id)]},
            headers={"Idempotency-Key": f"c7-{uuid4().hex[:12]}"},
        )
        assert created.status_code == 202, created.text
        analysis_id = created.json()["analysis"]["id"]

        # Aucun worker ne tourne : la version existe mais ne porte aucun verdict.
        # C22 — le refus qui ne dépend pas du rendu est prononcé à l'entrée dans la
        # file, donc sans attendre un worker, et aucune ligne de rapport n'est créée.
        response = client.post("/api/v1/reports/pdf", json={"analysis_id": analysis_id})
        assert response.status_code == 409, response.text
        assert "aucun verdict" in response.json()["detail"]["message"].lower()
        assert "x-verification-reference" not in response.headers
        with SessionLocal() as db:
            pending = db.scalars(
                select(ReportJob).where(
                    ReportJob.organization_id == identity.organization_id
                )
            ).all()
        assert pending == [], (
            "un rapport non rendable ne doit pas laisser de travail fantôme dans la file"
        )
