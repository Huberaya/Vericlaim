"""Parité d'intégrité entre SQLite (tests) et PostgreSQL (production).

SQLite n'applique **pas** les clés étrangères par défaut. Toute la suite de tests
tournait donc sans elles, et une contrainte violée passait inaperçue.

Ce défaut-là n'est pas théorique : il a été trouvé en produisant la preuve C22 sur
PostgreSQL, à la **première finalisation de téléversement** d'un vrai compte —
``POST /api/v1/document-uploads/{id}/complete`` répondait 500, clé étrangère violée
sur ``document_uploads.finalized_document_version_id``. Sur SQLite, le même appel
répondait 201 : le défaut existait depuis la migration ``c91d2e7f4a3`` (C4), et
aucun test ne pouvait le voir.

Ces tests activent donc les clés étrangères sur SQLite avant d'exécuter les
parcours d'écriture réels. Ils échouent si un code écrit une ligne qui référence
une ligne qui n'existe pas encore — ce que PostgreSQL refuse.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.core.database import SessionLocal, engine
from app.models.domain import DocumentUpload, DocumentVersion
from tests.auth_support import authenticate_client
from tests.document_support import FakeObjectStorage, FakeScanner
from tests.test_secure_documents import (
    PDF_BYTES,
    _create_document,
    _install_document_fakes,
    _request_upload,
)


@contextmanager
def foreign_keys_enforced():
    """Active ``PRAGMA foreign_keys`` sur toutes les connexions du moteur.

    Le réglage est par connexion : il faut donc vider le pool après avoir posé
    l'écouteur, sinon les connexions déjà ouvertes continuent sans contrainte.
    """

    def _enable(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", _enable)
    engine.dispose()
    try:
        with SessionLocal() as db:
            enforced = db.execute(text("PRAGMA foreign_keys")).scalar()
            assert enforced == 1, "les clés étrangères ne sont pas appliquées par SQLite"
        yield
    finally:
        event.remove(engine, "connect", _enable)
        engine.dispose()


def test_the_first_document_upload_survives_foreign_key_enforcement():
    """La finalisation d'un téléversement écrit la version **avant** de la référencer.

    Contre le défaut : SQLAlchemy ordonnait la mise à jour de ``document_uploads``
    avant l'insertion de ``document_versions``, parce qu'aucune relation ORM ne lie
    les deux lignes. Sur PostgreSQL, la contrainte refusait l'écriture.
    """

    app, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    assert isinstance(storage, FakeObjectStorage)
    assert isinstance(app.state.document_scanner, FakeScanner)
    try:
        with foreign_keys_enforced(), TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client)
            instruction = _request_upload(client, str(document["id"]))
            storage.upload_latest(payload=PDF_BYTES)

            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 201, completed.text
            version_id = completed.json()["version"]["id"]

            with SessionLocal() as db:
                version = db.get(DocumentVersion, UUID(version_id))
                upload = db.get(DocumentUpload, UUID(str(instruction["upload"]["id"])))
                assert version is not None
                assert version.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
                assert upload is not None
                assert str(upload.finalized_document_version_id) == version_id
                assert str(version.organization_id) == str(identity.organization_id)
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_the_extraction_enqueue_of_a_finalized_upload_respects_foreign_keys():
    """Le travail d'extraction créé à la finalisation ne référence rien d'inexistant."""

    app, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with foreign_keys_enforced(), TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            document = _create_document(client)
            instruction = _request_upload(client, str(document["id"]))
            storage.upload_latest(payload=PDF_BYTES)
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 201, completed.text

            with SessionLocal() as db:
                from app.models.domain import DocumentExtractionJob

                version_id = completed.json()["version"]["id"]
                jobs = db.scalars(
                    select(DocumentExtractionJob).where(
                        DocumentExtractionJob.document_version_id == UUID(version_id)
                    )
                ).all()
                assert len(jobs) == 1, "un travail d'extraction doit être créé pour la version"
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_the_analysis_pipeline_persists_verdicts_under_foreign_key_enforcement():
    """Le pipeline d'analyse écrit les allégations avant les verdicts qui les citent.

    Contre le défaut : ``analysis_verdicts.claim_id`` référence ``claims``. SQLAlchemy,
    qui ne connaît aucune relation entre ces tables, insérait les verdicts en premier.
    Sur PostgreSQL, le worker d'analyse échouait donc à chaque passage
    (``unexpected_worker_error``) : aucun verdict, aucun rapport, pour tous les clients.
    """

    from app.models.domain import AnalysisVerdict, Claim
    from app.main import app
    from tests.test_pdf_reporting import _persisted_analysis

    with foreign_keys_enforced(), TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _version_id = _persisted_analysis(client, identity)

        with SessionLocal() as db:
            verdicts = db.scalars(
                select(AnalysisVerdict).where(
                    AnalysisVerdict.organization_id == identity.organization_id
                )
            ).all()
            claims = db.scalars(
                select(Claim).where(Claim.organization_id == identity.organization_id)
            ).all()
            assert claims, "la détection doit persister ses allégations"
            assert verdicts, "la détection doit persister ses verdicts"
            assert {verdict.claim_id for verdict in verdicts} <= {claim.id for claim in claims}
            assert analysis_id
