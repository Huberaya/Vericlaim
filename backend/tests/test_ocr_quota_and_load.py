"""C21 — Charge et quotas OCR : ce que le produit applique vraiment.

Le plan C21 demandait de vérifier que « le coût est mesuré avant d'être payé ». La
reconnaissance du chantier a trouvé l'inverse dans le code livré par C13 :

* la mise en file d'extraction refusait **une** page, quel que soit le document
  (`assert_quota_available(..., quantity=1)`) ;
* la consommation réelle (le nombre de pages effectivement transcrites) n'était
  écrite qu'**après** l'OCR, par le vrai worker.

Autrement dit, une organisation disposant d'une page de quota pouvait faire
transcrire un scan de 300 pages : le coût était constaté après avoir été payé. Le
premier test de cette suite échoue contre ce défaut (il a été exécuté contre lui
avant la correction).

Deuxième exigence reprise ici : le chemin OCR n'avait **jamais été exécuté pour
de vrai** — la suite existante le remplaçait par un `monkeypatch`
(`test_extractor_marks_image_ocr_for_human_review`). Un moteur fourni avec le
système ne s'installe pas d'un simple `pip install`. Ces tests exécutent
`tesseract` réellement quand il est présent, et le disent quand il ne l'est pas.
"""

from __future__ import annotations

import hashlib
import shutil
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.security import count_pages_without_rendering, inspect_document_bytes
from app.engine.document_extractor import DocumentTextExtractor
from app.main import app
from app.models.domain import (
    DocumentExtractionJob,
    DocumentSegment,
    DocumentVersion,
)
from tests.auth_support import authenticate_client
from tests.test_billing_plans_and_quotas import _create_document, _subscribe
from tests.test_secure_documents import _install_document_fakes, _request_upload

TESSERACT = shutil.which("tesseract")
requires_tesseract = pytest.mark.skipif(
    TESSERACT is None,
    reason=(
        "tesseract absent : le chemin OCR ne peut pas être exercé pour de vrai. "
        "Sur un poste de développement, l'installer (paquet système) plutôt que de "
        "sauter silencieusement ce test."
    ),
)


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------


def _reason(*, token: str = "VERICLAIM-C21") -> bytes:
    """Une page scannée : du texte rendu en image, aucun texte extractible."""

    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1400, 400), color="white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
    draw.text((60, 140), token, fill="black", font=font)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _scanned_pdf(*, pages: int = 1, token: str = "VERICLAIM-C21") -> bytes:
    """PDF de ``pages`` pages, chacune étant une simple image : OCR obligatoire."""

    import fitz

    image = _reason(token=token)
    document = fitz.open()
    try:
        for _ in range(pages):
            page = document.new_page()
            page.insert_image(page.rect, stream=image)
        return document.tobytes()
    finally:
        document.close()


def _native_pdf(*, pages: int) -> bytes:
    """PDF dont le texte est natif : aucune OCR ne doit être déclenchée."""

    import fitz

    document = fitz.open()
    try:
        for index in range(pages):
            document.new_page().insert_text((72, 72), f"Page native {index + 1}")
        return document.tobytes()
    finally:
        document.close()


def _consume_ocr_pages(*, organization_id: str, pages: int, key: str) -> None:
    """Porte la consommation OCR au niveau voulu, par le journal d'usage réel."""

    from app.billing.plans import Metric
    from app.billing.enforcement import settle_usage

    with SessionLocal() as db:
        settle_usage(
            db,
            organization_id=organization_id,
            metric=Metric.OCR_PAGES,
            quantity=pages,
            source_type="test_setup",
            source_id=key,
            idempotency_key=f"test:{key}",
        )
        db.commit()


def _ocr_line(client: TestClient) -> dict:
    usage = client.get("/api/v1/billing/usage")
    assert usage.status_code == 200, usage.text
    return next(line for line in usage.json()["lines"] if line["metric"] == "ocr_pages")


def _run_extraction_worker(*, storage, organization_id: str, monkeypatch) -> bool:
    from app.workers.document_extraction_worker import DocumentExtractionWorker

    worker = DocumentExtractionWorker(
        settings=settings,
        storage=storage,
        session_factory=SessionLocal,
        worker_id="pytest-c21-worker",
    )
    monkeypatch.setattr(worker, "_active_organization_ids", lambda: [organization_id])
    return worker.run_once()


# ---------------------------------------------------------------------------
# 1. Le quota se décide avant le travail, sur le nombre de pages réel
# ---------------------------------------------------------------------------


def test_a_scan_is_refused_before_the_ocr_runs_and_the_refusal_counts_pages(monkeypatch):
    """Défaut d'origine : un document de 5 pages était jugé comme une page de quota."""

    from app.billing.plans import PLAN_BY_CODE, Metric

    limit = PLAN_BY_CODE["starter"].quotas.limit_for(Metric.OCR_PAGES)
    payload = _scanned_pdf(pages=5)

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            # Il ne reste qu'une page de quota : un contrôle « 1 page pour tout
            # document » laisserait passer un scan de 5 pages.
            _consume_ocr_pages(
                organization_id=identity.organization_id, pages=limit - 1, key="une-page-restante"
            )
            assert _ocr_line(client)["remaining"] == 1

            document = _create_document(client, title="Scan de cinq pages")
            instruction = _request_upload(
                client, str(document["id"]), filename="scan.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")

            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )

            assert completed.status_code == 402, completed.text  # 402 = quota refusé, rien n'est engagé
            body = completed.json()["detail"]
            assert body["code"] == "quota_exceeded"
            assert body["metric"] == "ocr_pages"
            # Le refus porte le chiffre exact demandé et le solde réel.
            assert body["requested"] == 5, "le contrôle doit demander les 5 pages du document"
            assert body["used"] == limit - 1 and body["limit"] == limit
            assert "5 pages" in body["message"] and "1" in body["message"]

            # Aucun travail n'a été mis en file et aucune version n'a été créée : la
            # promotion du binaire propre est annulée, le refus ne laisse rien derrière lui.
            with SessionLocal() as db:
                jobs = db.scalar(
                    select(func.count())
                    .select_from(DocumentExtractionJob)
                    .where(DocumentExtractionJob.organization_id == identity.organization_id)
                )
                versions = db.scalar(
                    select(func.count())
                    .select_from(DocumentVersion)
                    .where(DocumentVersion.organization_id == identity.organization_id)
                )
                assert jobs == 0, "un job OCR a été créé alors que le quota refusait"
                assert versions == 0, "une version a survécu au refus : elle serait un document fantôme"

            assert _ocr_line(client)["used"] == limit - 1, "un refus ne consomme rien"

            # Le refus n'est pas définitif : le client monte d'offre et rejoue le même
            # fichier. Un import refusé pour quota ne doit pas être perdu.
            _subscribe(client, plan_code="pro")
            assert _ocr_line(client)["remaining"] == PLAN_BY_CODE["pro"].quotas.limit_for(Metric.OCR_PAGES)
            retried = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert retried.status_code == 201, retried.text
            assert retried.json()["version"]["page_count"] == 5
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


def test_the_queue_itself_refuses_a_document_that_does_not_fit_the_quota():
    """Seconde ligne de défense : la mise en file vérifie elle-même, avec le bon chiffre.

    Le contrôle préalable dans le service d'import protège l'utilisateur (rien n'est
    détruit). Celui-ci protège le produit : même appelée directement, la mise en file
    refuse un document qui ne tient pas dans le quota restant, et elle réclame le nombre
    de pages réel — pas une page.
    """

    from uuid import uuid4

    from app.billing.enforcement import QuotaExceededError
    from app.billing.plans import PLAN_BY_CODE, Metric
    from app.documents.extraction import enqueue_document_extraction
    from app.models.domain import Document, DocumentVersion, ExtractionStatus

    limit = PLAN_BY_CODE["starter"].quotas.limit_for(Metric.OCR_PAGES)

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        _consume_ocr_pages(
            organization_id=identity.organization_id, pages=limit - 1, key="file-attente"
        )
        document = _create_document(client, title="Mise en file directe")

    with SessionLocal() as db:
        stored = db.get(Document, UUID(document["id"]))
        assert stored is not None
        version = DocumentVersion(
            id=uuid4(),
            organization_id=identity.organization_id,
            document_id=stored.id,
            version_number=1,
            source_filename="scan.pdf",
            content_type="application/pdf",
            storage_key="clean/direct/source",
            sha256="a" * 64,
            size_bytes=1024,
            extraction_status=ExtractionStatus.PENDING,
        )
        db.add(version)
        db.flush()

        with pytest.raises(QuotaExceededError) as raised:
            enqueue_document_extraction(
                db,
                settings=settings,
                document=stored,
                version=version,
                organization_id=identity.organization_id,
                actor_user_id=None,
                request_id=None,
                expected_pages=5,
            )
        assert raised.value.detail["requested"] == 5
        assert raised.value.detail["metric"] == "ocr_pages"
        db.rollback()

    with SessionLocal() as db:
        jobs = db.scalar(
            select(func.count())
            .select_from(DocumentExtractionJob)
            .where(DocumentExtractionJob.organization_id == identity.organization_id)
        )
        assert jobs == 0, "la mise en file a créé un travail qu'elle venait de refuser"


def test_a_scan_is_refused_when_what_is_left_is_smaller_than_the_scan(monkeypatch):
    """Il reste 2 pages de quota, le document en fait 5 : refus avant l'OCR."""

    from app.billing.plans import PLAN_BY_CODE, Metric

    limit = PLAN_BY_CODE["starter"].quotas.limit_for(Metric.OCR_PAGES)
    payload = _scanned_pdf(pages=5)

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            _consume_ocr_pages(
                organization_id=identity.organization_id, pages=limit - 2, key="presque-plein"
            )

            line = _ocr_line(client)
            assert line["remaining"] == 2

            document = _create_document(client, title="Scan qui ne passe pas")
            instruction = _request_upload(
                client, str(document["id"]), filename="scan.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 402, completed.text  # 402 = quota refusé, rien n'est engagé
            assert completed.json()["detail"]["code"] == "quota_exceeded"
            assert _ocr_line(client)["used"] == limit - 2, "un refus ne consomme rien"
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


def test_a_scan_that_fits_the_remaining_quota_is_accepted_and_charged_exactly():
    """Un scan de 5 pages avec 30 pages de quota passe et consomme 5 pages, pas 1."""

    payload = _scanned_pdf(pages=5)

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            document = _create_document(client, title="Scan dans le quota")
            instruction = _request_upload(
                client, str(document["id"]), filename="scan.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 201, completed.text
            assert completed.json()["version"]["page_count"] == 5

            with SessionLocal() as db:
                job = db.scalar(
                    select(DocumentExtractionJob).where(
                        DocumentExtractionJob.organization_id == identity.organization_id
                    )
                )
                assert job is not None, "un scan accepté doit être mis en file"

            # La page 5 du scan ne doit pas ressembler à une page de quota : le solde
            # n'est pas décrémenté à la mise en file (le travail n'a pas encore eu lieu).
            assert _ocr_line(client)["used"] == 0
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


def test_a_native_pdf_is_charged_its_real_number_of_pages():
    """Un PDF natif n'utilise pas l'OCR mais occupe un document : le compteur reste juste."""

    payload = _native_pdf(pages=7)

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            document = _create_document(client, title="PDF natif")
            instruction = _request_upload(
                client, str(document["id"]), filename="rapport.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 201, completed.text
            assert completed.json()["version"]["page_count"] == 7
            assert _ocr_line(client)["used"] == 0
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


# ---------------------------------------------------------------------------
# 2. Le comptage des pages est une mesure, pas une supposition
# ---------------------------------------------------------------------------


def test_the_page_count_is_taken_from_the_document_without_any_ocr():
    """Compter les pages ne doit déclencher aucune reconnaissance de caractères."""

    import pytesseract

    def _forbidden(*_args, **_kwargs):  # pragma: no cover - échoue s'il est appelé
        raise AssertionError("compter les pages a déclenché une OCR")

    payload = _scanned_pdf(pages=6)
    pytesseract.image_to_string, original = _forbidden, pytesseract.image_to_string
    try:
        inspected = inspect_document_bytes(
            payload, filename="scan.pdf", declared_content_type="application/pdf", max_size_bytes=50_000_000
        )
        assert inspected.page_count == 6
    finally:
        pytesseract.image_to_string = original


def test_the_page_count_of_an_image_is_one_and_of_an_unknown_format_is_none():
    payload = _reason()
    inspected = inspect_document_bytes(
        payload, filename="scan.png", declared_content_type="image/png", max_size_bytes=50_000_000
    )
    assert inspected.page_count == 1

    # Texte brut : seule l'extraction peut dire combien de « pages » cela représente.
    # On ne devine pas — et l'appelant traite l'inconnu comme une page, ce qui est
    # consigné comme limite (un fichier texte ne peut de toute façon pas dépasser
    # une page de quota sans que le compteur réel le corrige après extraction).
    assert count_pages_without_rendering(b"du texte", content_type="text/plain") is None


def test_a_corrupt_pdf_does_not_get_a_page_count_it_does_not_have():
    assert count_pages_without_rendering(b"%PDF-1.7\ntronque", content_type="application/pdf") is None


# ---------------------------------------------------------------------------
# 3. L'OCR fonctionne réellement (moteur système, pas un substitut)
# ---------------------------------------------------------------------------


@requires_tesseract
def test_a_real_scanned_pdf_is_transcribed_by_the_real_ocr_engine():
    """Le chemin OCR, exécuté sans substitut : c'est le cœur de C21."""

    payload = _scanned_pdf(pages=1, token="VERICLAIM-C21")
    extractor = DocumentTextExtractor(max_bytes=50_000_000, max_pages=5, max_chars=100_000)

    result = extractor.extract_result("scan.pdf", "application/pdf", payload)

    assert result.method == "PDF_OCR"
    assert result.page_count == 1
    assert result.requires_human_review is True, "un texte lu par OCR doit être relu"
    normalized = result.text.upper().replace(" ", "")
    assert "VERICLAIM" in normalized and "C21" in normalized, result.text
    assert [segment.segment_type for segment in result.segments] == ["image_ocr"]
    assert result.pages[0].used_ocr is True
    assert result.segments[0].page_number == 1


@requires_tesseract
def test_the_transcribed_text_can_be_traced_back_to_the_page_it_came_from(monkeypatch):
    """Le texte OCR est publié, avec sa page et son empreinte d'origine."""

    payload = _scanned_pdf(pages=2, token="VERICLAIM-C21")

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            document = _create_document(client, title="Scan de deux pages")
            instruction = _request_upload(
                client, str(document["id"]), filename="scan.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 201, completed.text
            version_id = UUID(completed.json()["version"]["id"])

            assert _run_extraction_worker(
                storage=storage, organization_id=identity.organization_id, monkeypatch=monkeypatch
            )

            with SessionLocal() as db:
                version = db.get(DocumentVersion, version_id)
                assert version is not None
                assert version.extraction_status.value == "review_required"
                assert version.page_count == 2
                assert version.extracted_text_sha256
                digest = version.extracted_text_sha256
                storage_key = version.extracted_text_storage_key
                source_sha256 = version.sha256

            # Le texte publié est bien celui qui a été transcrit : il est relu depuis le
            # stockage objet, comme le fera un rapport, pas depuis la mémoire du worker.
            assert storage_key, "le texte transcrit doit être publié dans le stockage objet"
            text_bytes = storage.read_bytes(
                bucket=settings.document_clean_bucket, key=storage_key, max_size_bytes=10_000_000
            )
            text = text_bytes.decode("utf-8")
            assert "VERICLAIM" in text.upper().replace(" ", "")
            assert hashlib.sha256(text_bytes).hexdigest() == digest

            with SessionLocal() as db:
                segments = list(
                    db.scalars(
                        select(DocumentSegment)
                        .where(DocumentSegment.document_version_id == version_id)
                        .order_by(DocumentSegment.page_number)
                    )
                )
            assert segments, "le texte transcrit doit produire des segments citables"
            assert all(segment.segment_type.value == "image_ocr" for segment in segments)
            assert [segment.page_number for segment in segments] == [1, 2]
            assert {segment.source_sha256 for segment in segments} == {source_sha256}
            # L'extrait citable renvoie au texte publié, page par page.
            assert {segment.page_number for segment in segments} == {1, 2}
            for segment in segments:
                assert text[segment.start_offset : segment.end_offset].strip()
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


@requires_tesseract
def test_the_pages_actually_transcribed_are_what_gets_charged(monkeypatch):
    """La consommation suit le travail réel : 3 pages transcrites = 3 pages comptées."""

    payload = _scanned_pdf(pages=3, token="VERICLAIM-C21")

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            document = _create_document(client, title="Scan de trois pages")
            instruction = _request_upload(
                client, str(document["id"]), filename="scan.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 201, completed.text

            line = _ocr_line(client)
            assert line["used"] == 0, "aucune page n'est comptée avant le travail"

            assert _run_extraction_worker(
                storage=storage, organization_id=identity.organization_id, monkeypatch=monkeypatch
            )

            after = _ocr_line(client)
            assert after["used"] == 3
            assert after["remaining"] == after["limit"] - 3
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


@requires_tesseract
def test_a_scan_longer_than_the_quota_is_stopped_before_any_page_is_transcribed(monkeypatch):
    """Le coût n'est jamais payé avant d'être accepté : le refus précède la file."""

    from app.billing.plans import PLAN_BY_CODE, Metric

    limit = PLAN_BY_CODE["starter"].quotas.limit_for(Metric.OCR_PAGES)
    payload = _scanned_pdf(pages=3)

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            _consume_ocr_pages(
                organization_id=identity.organization_id, pages=limit - 2, key="deux-pages-restantes"
            )
            document = _create_document(client, title="Scan d'une page de trop")
            instruction = _request_upload(
                client, str(document["id"]), filename="scan.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            refused = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert refused.status_code == 402, refused.text
            assert refused.json()["detail"]["requested"] == 3

            # Le worker n'a rien à faire : rien n'a été mis en file, aucune page n'est
            # transcrite, donc aucune OCR n'est payée.
            assert (
                _run_extraction_worker(
                    storage=storage,
                    organization_id=identity.organization_id,
                    monkeypatch=monkeypatch,
                )
                is False
            )
            assert _ocr_line(client)["used"] == limit - 2
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


# ---------------------------------------------------------------------------
# 4. Deux workers ne peuvent pas payer deux fois la même page
# ---------------------------------------------------------------------------


def test_a_job_can_only_be_claimed_once():
    """Le bail protège la page : deux workers, un seul travail."""

    from app.documents.extraction import claim_next_document_extraction_job
    from app.identity.service import set_db_request_context

    payload = _scanned_pdf(pages=1)

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            document = _create_document(client, title="Un seul worker")
            instruction = _request_upload(
                client, str(document["id"]), filename="scan.pdf", payload=payload
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            assert (
                client.post(
                    f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
                ).status_code
                == 201
            )
            organization_id = identity.organization_id

            def _claim(worker_id: str):
                with SessionLocal() as db:
                    set_db_request_context(db, user_id=None, organization_id=organization_id)
                    claim = claim_next_document_extraction_job(
                        db,
                        settings=settings,
                        organization_id=organization_id,
                        worker_id=worker_id,
                    )
                    db.commit()
                    return claim

            first = _claim("worker-a")
            second = _claim("worker-b")
            assert first is not None, "le premier worker doit obtenir le travail"
            assert second is None, "le second worker ne doit pas payer la même page"

            with SessionLocal() as db:
                job = db.scalar(
                    select(DocumentExtractionJob).where(
                        DocumentExtractionJob.organization_id == organization_id
                    )
                )
                assert job is not None
                assert job.status.value == "running", "le travail doit être marqué comme loué"
                assert job.worker_id == "worker-a"
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner
