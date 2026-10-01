"""C6 acceptance: a report must describe the persisted analysis, not a new one.

The previous implementation reconstructed a source text and re-ran the rule
engine on it. On an analysis where the engine had detected three claims and one
prohibition, the resulting PDF stated "CONFORME AU PÉRIMÈTRE AUDITÉ · Allégations :
0 | Violations : 0" with a genuine SHA-256 of the fabricated text. These tests
exist so that failure mode cannot come back.

They are fidelity tests: every value asserted in the PDF is read back from the
database and compared, so a report that diverges from its analysis fails.
"""
from __future__ import annotations

import io
import re
import zipfile
from uuid import UUID, uuid4

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analyses.service import (
    claim_next_analysis_detection_job,
    process_claimed_analysis_detection,
)
from app.core.config import settings
from app.core.database import Base, SessionLocal, create_tables
from app.identity.service import set_db_request_context
from app.main import app
from tests.report_support import issue_report, report_storage, request_report
from app.models.domain import (
    AnalysisVerdict,
    Document,
    DocumentSegment,
    DocumentVersion,
    ExtractionStatus,
    SegmentType,
)
from tests.auth_support import authenticate_client

DOCUMENT_TEXT = (
    "Cet emballage est biodégradable. "
    "Notre produit est neutre en carbone. "
    "Le flacon est recyclable."
)


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def _hash(character: str) -> str:
    return character * 64


def _seed_analysable_document(organization_id: UUID) -> UUID:
    """One extracted document whose text reaches three different rules."""
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        document = Document(
            organization_id=organization_id,
            document_key=f"C6-DOC-{suffix}",
            title="Déclaration fournisseur",
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
            sha256=_hash("a"),
            size_bytes=len(DOCUMENT_TEXT),
            source_language="fr",
            extraction_status=ExtractionStatus.COMPLETED,
            extraction_engine_version="vericlaim-document-extractor-v1",
            extracted_text_sha256=_hash("b"),
        )
        db.add(version)
        db.flush()
        db.add(
            DocumentSegment(
                organization_id=organization_id,
                document_version_id=version.id,
                sequence_number=0,
                page_number=1,
                segment_type=SegmentType.PARAGRAPH,
                text=DOCUMENT_TEXT,
                start_offset=0,
                end_offset=len(DOCUMENT_TEXT),
                source_sha256=version.sha256,
            )
        )
        db.commit()
        return version.id


def _run_worker_once(organization_id: UUID, user_id: UUID) -> None:
    with SessionLocal() as db:
        set_db_request_context(db, user_id=user_id, organization_id=organization_id)
        claim = claim_next_analysis_detection_job(
            db,
            settings=settings,
            organization_id=organization_id,
            worker_id="pytest-c6-worker",
        )
        assert claim is not None, "aucun job à traiter"
        process_claimed_analysis_detection(db, settings=settings, claim=claim)
        db.commit()


def _persisted_analysis(client: TestClient, identity) -> tuple[str, UUID]:
    """Create a real analysis through the API and process it, as production does."""
    version_id = _seed_analysable_document(identity.organization_id)
    created = client.post(
        "/api/v1/analyses",
        json={"document_version_ids": [str(version_id)]},
        headers={"Idempotency-Key": f"c6-{uuid4().hex[:12]}"},
    )
    assert created.status_code == 202, created.text
    analysis_id = created.json()["analysis"]["id"]
    _run_worker_once(identity.organization_id, identity.user_id)
    return analysis_id, version_id


# Right edge of the printable area. ``insert_text`` does not wrap and PyMuPDF
# clips at the page edge, but ``get_text`` still returns the clipped characters.
# A fidelity test must therefore reason about what a reader can actually see:
# words whose bounding box extends past this limit are dropped.
VISIBLE_RIGHT_EDGE = 555.32


def _pdf_text(content: bytes) -> str:
    """Extract only the text a human reader can see on the rendered page."""
    lines: list[str] = []
    with fitz.open(stream=content, filetype="pdf") as document:
        for page in document:
            grouped: dict[tuple[int, int], list[tuple[int, str]]] = {}
            for x0, _y0, x1, _y1, word, block, line, number in page.get_text("words"):
                if x1 > VISIBLE_RIGHT_EDGE + 1:
                    continue
                grouped.setdefault((block, line), []).append((number, word))
            for key in sorted(grouped):
                words = [word for _n, word in sorted(grouped[key])]
                lines.append(" ".join(words))
    return "\n".join(lines)


def _overflowing_words(content: bytes) -> list[str]:
    """Words the reader cannot see because they run past the printable area."""
    offenders: list[str] = []
    with fitz.open(stream=content, filetype="pdf") as document:
        for page in document:
            for x0, _y0, x1, _y1, word, _b, _l, _n in page.get_text("words"):
                if x1 > VISIBLE_RIGHT_EDGE + 1:
                    offenders.append(word)
    return offenders


def test_no_text_is_silently_clipped_by_the_page_edge():
    """A reader must not lose information to a layout that overflows.

    ``insert_text`` neither wraps nor warns, and ``get_text`` still returns the
    clipped characters, so without this guard a report can look complete to an
    automated check while a human reads a truncated line.
    """
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        content = issue_report(client, analysis_id=analysis_id, storage=storage).content
        offenders = _overflowing_words(content)
        assert offenders == [], f"texte coupé hors de la zone imprimable : {offenders[:10]}"


def _stored_verdicts(analysis_id: str) -> list[AnalysisVerdict]:
    with SessionLocal() as db:
        from app.models.domain import AnalysisVersion

        version = db.scalar(
            select(AnalysisVersion).where(AnalysisVersion.analysis_id == UUID(analysis_id))
        )
        assert version is not None
        return list(
            db.scalars(
                select(AnalysisVerdict)
                .where(AnalysisVerdict.analysis_version_id == version.id)
                .order_by(AnalysisVerdict.sequence_number.asc())
            ).all()
        )


# --------------------------------------------------------------------------- #
# C6.1 — the headline case: three claims, one prohibition, never "CONFORME"
# --------------------------------------------------------------------------- #


def test_report_shows_every_persisted_claim_and_never_claims_compliance():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")

        analysis_id, _ = _persisted_analysis(client, identity)
        verdicts = _stored_verdicts(analysis_id)
        assert verdicts, "aucun verdict persisté: le test ne prouve rien"

        response = issue_report(client, analysis_id=analysis_id, storage=storage)
        assert response.status_code == 200, response.text
        text = _pdf_text(response.content)

        # Every persisted rule and claim appears in the document.
        for verdict in verdicts:
            assert verdict.rule_id in text, f"règle absente du PDF: {verdict.rule_id}"
            assert verdict.claim_text[:40] in text, (
                f"allégation absente du PDF: {verdict.claim_text[:40]!r}"
            )

        assert "RULE_AGEC_BIODEGRADABLE" in text
        assert "STRICTLY_PROHIBITED" in text or "STRICTEMENT INTERDIT" in text.upper()

        # The report must not assert a clean perimeter while violations exist.
        count = re.search(r"Violations\s*:\s*(\d+)", text)
        assert count is not None, "le PDF n'annonce aucun compte de violations"
        assert int(count.group(1)) == sum(1 for v in verdicts if v.is_legal_violation) >= 1
        assert "CONFORME AU PÉRIMÈTRE AUDITÉ" not in text


def test_report_carries_the_real_fingerprints():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        with SessionLocal() as db:
            from app.models.domain import AnalysisVersion

            version = db.scalar(
                select(AnalysisVersion).where(AnalysisVersion.analysis_id == UUID(analysis_id))
            )
            result_sha256 = version.result_sha256
            rulebook_version = version.rulebook_version
            engine_version = version.engine_version

        response = issue_report(client, analysis_id=analysis_id, storage=storage)
        assert response.status_code == 200, response.text
        text = _pdf_text(response.content)

        assert result_sha256 in text, "l'empreinte persistée du résultat est absente du PDF"
        assert rulebook_version in text, "l'empreinte du Rule Book est absente du PDF"
        assert engine_version in text, "la version du moteur est absente du PDF"

        # No fabricated identifiers from the previous implementation.
        assert "Rule Engine v1.0.0" not in text
        assert "2026.09" not in text
        assert "aud-persisted-export" not in text
        assert "0" * 64 not in text


def test_report_renders_the_stored_date_not_the_download_date():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        with SessionLocal() as db:
            from app.models.domain import AnalysisVersion

            version = db.scalar(
                select(AnalysisVersion).where(AnalysisVersion.analysis_id == UUID(analysis_id))
            )
            as_of = version.evaluation_context_json["as_of_date"]

        text = _pdf_text(
            issue_report(client, analysis_id=analysis_id, storage=storage).content
        )
        # The ISO reference date of the verdict is printed, so a report cannot be
        # presented as a fresh audit of a later date.
        assert as_of in text


def test_cited_text_comes_from_persisted_segments():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        text = _pdf_text(
            issue_report(client, analysis_id=analysis_id, storage=storage).content
        )

        assert "biodégradable" in text
        assert "neutre en carbone" in text
        # The old default placeholder that produced the false-clean certificate.
        assert "Texte extrait de la pièce justificative." not in text


# --------------------------------------------------------------------------- #
# C6.2 — a client can no longer supply a verdict
# --------------------------------------------------------------------------- #


def test_client_supplied_verdicts_are_rejected():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        forged = {
            "analysis_id": str(uuid4()),
            "evaluation_response": {
                "extracted_source_text": "Allégation totalement fantaisiste.",
                "overall_compliance": "COMPLIANT",
                "risk_score": 0,
                "legal_exposure_estimate": "Aucun risque.",
                "violations_count": 0,
                "conditional_findings_count": 0,
                "detected_claims_count": 0,
                "evaluations": [],
                "exposure_matrix": {"items": []},
                "audit_trail": {
                    "audit_id": "forged",
                    "engine_version": "1.0.0",
                    "rulebook_version": "2026.09",
                    "evaluated_at_utc": "2026-09-29T18:00:00Z",
                    "as_of_date": "2026-09-29",
                    "source_sha256": "0" * 64,
                    "evidence_manifest_sha256": "0" * 64,
                    "report_sha256": "0" * 64,
                    "record_hash": "0" * 64,
                },
            },
        }
        for path in ("/api/v1/reports/pdf", "/api/v1/reports/dossier"):
            response = client.post(path, json=forged)
            assert response.status_code == 422, (
                f"{path} accepte encore un verdict fourni par le client: {response.status_code}"
            )
            assert response.content[:5] != b"%PDF-"


def test_analysis_id_is_mandatory():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        response = client.post("/api/v1/reports/pdf", json={"document_title": "sans analyse"})
        assert response.status_code == 422, response.text


def test_a_version_without_verdicts_is_refused_not_rendered():
    """A pre-v2 version must not be rendered as a compliance report."""
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        from app.models.domain import Analysis, AnalysisStatus, AnalysisVersion

        with SessionLocal() as db:
            analysis = Analysis(
                organization_id=identity.organization_id,
                analysis_key=f"legacy-{uuid4().hex[:8]}",
                status=AnalysisStatus.COMPLETED,
            )
            db.add(analysis)
            db.flush()
            db.add(
                AnalysisVersion(
                    organization_id=identity.organization_id,
                    analysis_id=analysis.id,
                    version_number=1,
                    status=AnalysisStatus.COMPLETED,
                    engine_version="vericlaim-fact-extractor-v1",
                    rulebook_version="not-applicable-deterministic-claims-v1",
                    input_manifest_sha256=_hash("0"),
                    result_sha256=None,
                    overall_compliance=None,
                    result_json={"pipeline": "deterministic_claim_detection"},
                )
            )
            db.commit()
            analysis_id = str(analysis.id)

        # C22 : le refus qui ne dépend pas du rendu est prononcé à l'entrée dans la
        # file, donc immédiatement — un client n'attend pas un worker pour apprendre
        # qu'il n'y a rien à rendre.
        response = client.post("/api/v1/reports/pdf", json={"analysis_id": analysis_id})
        assert response.status_code == 409, response.text
        assert "aucun verdict" in response.json()["detail"]["message"].lower()
        assert response.content[:5] != b"%PDF-"


# --------------------------------------------------------------------------- #
# C6.3 — the ZIP dossier carries the same corrected data
# --------------------------------------------------------------------------- #


def test_dossier_zip_is_rendered_from_the_persisted_analysis():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        with SessionLocal() as db:
            from app.models.domain import AnalysisVersion

            version = db.scalar(
                select(AnalysisVersion).where(AnalysisVersion.analysis_id == UUID(analysis_id))
            )
            result_sha256 = version.result_sha256

        response = issue_report(
            client, analysis_id=analysis_id, report_format="dossier_zip", storage=storage
        )
        assert response.status_code == 200, response.text
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        assert "rapport_pre_audit.pdf" in names
        assert "matrice_probatoire.csv" in names
        assert "README_DOSSIER_AUDIT.txt" in names

        readme = archive.read("README_DOSSIER_AUDIT.txt").decode("utf-8")
        assert result_sha256 in readme, "le README n'annonce pas la vraie empreinte"
        assert "Rule Engine v1.0.0" not in readme
        assert "2026.09" not in readme

        import json

        manifest = json.loads(archive.read("manifeste_audit_scelle.json").decode("utf-8"))
        assert manifest["audit_trail"]["report_sha256"] == result_sha256


# --------------------------------------------------------------------------- #
# C6.4 — tenant isolation is preserved on the new path
# --------------------------------------------------------------------------- #


def test_tenant_isolation_on_report_download():
    with TestClient(app) as client_a, TestClient(app) as client_b, report_storage() as storage:
        identity_a = authenticate_client(client_a, role_code="analyst")
        authenticate_client(client_b, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client_a, identity_a)

        issue_report(client_a, analysis_id=analysis_id, storage=storage)
        assert client_a.get(f"/api/v1/reports/analyses/{analysis_id}/pdf").status_code == 200
        # B ne voit ni le travail, ni le rapport, ni le fichier.
        assert client_b.get(f"/api/v1/reports/analyses/{analysis_id}/pdf").status_code == 404
        assert client_b.get(f"/api/v1/reports/analyses/{analysis_id}/dossier").status_code == 404
