"""Lot P1 : le registre de libération des objets d'import.

Garanties épinglées ici, toutes observables dans le stockage de test et en base :

1. la transition d'état et son marqueur de purge sont écrits dans la MÊME transaction ;
2. aucun octet n'est détruit avant le commit qui autorise la suppression ;
3. un échec de suppression laisse un état durable qui permet de reprendre ;
4. une ligne en échec n'arrête pas le traitement des suivantes ;
5. la reprise est idempotente et résistante à la concurrence ;
6. aucun succès n'est annoncé quand la suppression a échoué ou reste indéterminée ;
7. `quarantine_deleted` a disparu des événements d'état: seul l'événement de fermeture
   du registre affirme une libération, et il l'affirme après relecture du stockage ;
8. un préfixe, un chemin ou une organisation d'autrui ne peut pas être passé à la
   libération ;
9. les événements d'audit suivent les transitions réellement commitées.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.service import (
    PURGE_MAX_ATTEMPTS,
    _upload_object_prefix,
    retry_retained_document_purges,
    sweep_expired_document_uploads,
)
from app.documents.storage import ObjectStorageError, validate_tenant_scoped_prefix
from app.identity.service import set_db_request_context
from app.models.domain import AuditEvent, Document, DocumentStatus, DocumentUpload, DocumentUploadStatus
from tests.auth_support import authenticate_client
from tests.document_lifecycle_support import PDF_BYTES, create_document_and_upload_intent, install_document_fakes
from tests.document_support import StoredObject


@pytest.fixture(autouse=True)
def _isolated_ledger() -> Iterator[None]:
    """Ferme les registres ouverts par les tests precedents.

    La suite partage une base SQLite par execution: sans remise a zero, une
    passe de reprise verrait les lignes d'autres tests et rendrait les
    compteurs inintelligibles.
    """

    from sqlalchemy import inspect

    from app.core.database import engine

    if not inspect(engine).has_table("document_uploads"):
        # Première exécution de la suite: le schéma n'est posé que par le lifespan de
        # l'application, et il n'y a encore aucun registre à fermer.
        yield
        return
    with SessionLocal() as db:
        db.execute(
            update(DocumentUpload).values(
                purge_required=False,
                purge_kind=None,
                purge_reason=None,
                purge_attempts=0,
                purge_last_error=None,
                purge_requested_at=None,
            )
        )
        db.commit()
    yield


def _upload_row(upload_id: str) -> DocumentUpload:
    with SessionLocal() as db:
        return db.get(DocumentUpload, uuid.UUID(upload_id))


def _audit_payloads(db: Session, *, action: str, entity_type: str, entity_id: uuid.UUID) -> list[dict]:
    rows = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == entity_type,
                AuditEvent.entity_id == entity_id,
                AuditEvent.action == action,
            )
        ).all()
    )
    return [row.payload_json for row in rows]


def _upload_audit(db: Session, *, action: str, upload_id: uuid.UUID) -> list[dict]:
    return _audit_payloads(db, action=action, entity_type="document_upload", entity_id=upload_id)


def _promote(client: TestClient, storage, instruction: dict) -> dict:
    storage.upload_latest(payload=PDF_BYTES)
    completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
    assert completion.status_code == 201, completion.text
    return completion.json()


def test_marker_is_committed_with_the_transition_and_release_runs_after() -> None:
    """Un objet n'est jamais détruit avant le commit qui autorise la suppression."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    observed: list[str] = []
    original_delete_prefix = storage.delete_prefix

    def spy_delete_prefix(*, bucket: str, prefix: str):
        # A ce moment précis, la transition doit déjà être lisible par AUTRE
        # connexion: c'est la définition opérationnelle d'un geste post-commit.
        with SessionLocal() as probe:
            row = probe.get(DocumentUpload, uuid.UUID(instruction["upload"]["id"]))
            observed.append("version_promue" if row.finalized_document_version_id is not None else "transition_absente")
        return original_delete_prefix(bucket=bucket, prefix=prefix)

    storage.delete_prefix = spy_delete_prefix  # type: ignore[method-assign]
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Registre ouvert")
            assert storage.prefix_deletions == [], "aucun geste de stockage avant la complétion"
            body = _promote(client, storage, instruction)
            assert observed == ["version_promue"], observed
            assert storage.prefix_deletions, "la libération a bien été tentée, après commit"
            upload_id = uuid.UUID(instruction["upload"]["id"])
            with SessionLocal() as db:
                set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=identity.organization_id)
                cleaned = _audit_payloads(
                    db,
                    action="document.upload_clean",
                    entity_type="document_version",
                    entity_id=uuid.UUID(body["version"]["id"]),
                )
            assert len(cleaned) == 1
            assert cleaned[0]["quarantine_purge"] == "scheduled"
            assert "quarantine_deleted" not in cleaned[0], (
                "l'événement de transition ne peut pas affirmer une suppression à venir"
            )
            row = _upload_row(instruction["upload"]["id"])
            assert row.purge_required is True and row.purge_kind == "quarantine_prefix"
            assert row.purge_reason == "promoted" and row.purge_attempts == 0
            assert row.purge_requested_at is not None
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_closed_ledger_only_after_a_reread_of_the_storage() -> None:
    """La reprise relit le stockage et ne ferme le registre que sur une preuve."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Registre à fermer")
            _promote(client, storage, instruction)
            upload_id = instruction["upload"]["id"]
            # L'objet de quarantaine est parti, le registre est encore ouvert: c'est
            # l'état exact après un krach entre le commit et le geste de stockage.
            row = _upload_row(upload_id)
            assert row.purge_required is True
            key = row.quarantine_storage_key
            bucket = settings.document_quarantine_bucket
            assert (bucket, key) not in storage.objects

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                summary = retry_retained_document_purges(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert summary.released == 1 and summary.retained == 0 and summary.failed_rows == 0
                row = db.get(DocumentUpload, uuid.UUID(upload_id))
                assert row.purge_required is False and row.purge_attempts == 1
                assert row.purge_kind is None and row.purge_reason is None and row.purge_last_error is None
                closed = _upload_audit(db, action="document.upload_purge_released", upload_id=uuid.UUID(upload_id))
                assert len(closed) == 1, closed
                assert closed[0]["quarantine_purge"] == "already_absent", closed
                assert closed[0]["released_count"] == 0 and closed[0]["purge_attempts"] == 1
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_storage_failure_keeps_the_ledger_open_then_recovers() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Stockage en panne")
            storage.fail_operations = {"delete_prefix"}
            body = _promote(client, storage, instruction)
            upload_id = uuid.UUID(instruction["upload"]["id"])
            row = _upload_row(str(upload_id))
            assert row.purge_required is True, "un échec de stockage doit rester rejouable"
            assert row.purge_attempts == 0
            document_id = uuid.UUID(body["document"]["id"])
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                assert _upload_audit(db, action="document.upload_purge_released", upload_id=upload_id) == []
            # Le client a obtenu sa version; le geste de stockage, lui, attend la reprise.
            storage.fail_operations = set()
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                summary = retry_retained_document_purges(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert summary.released == 1
                assert db.get(DocumentUpload, upload_id).purge_required is False
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_partially_retained_prefix_is_reported_as_retained_not_deleted() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document, instruction = create_document_and_upload_intent(client, title="Fragments restants")
            body = _promote(client, storage, instruction)
            upload_id = uuid.UUID(instruction["upload"]["id"])
            row = _upload_row(str(upload_id))
            prefix = _upload_object_prefix(row)
            del body
            # Un POST multipart abandonné laisse des pièces à côté de la clé source.
            stray = f"{prefix}parts/fragment-1"
            storage.objects[(settings.document_quarantine_bucket, stray)] = StoredObject(
                payload=b"fragment", content_type="application/octet-stream", etag="deadbeef"
            )
            # Le geste repond « partiellement retenu » sans lever: c'est le cas ou un
            # resultat optimiste deviendrait un faux succes.
            storage.fail_prefix_deletions = {stray}
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                summary = retry_retained_document_purges(db, settings=settings, storage=storage)
                db.commit()
                assert summary.released == 0 and summary.retained == 1
                fresh = db.get(DocumentUpload, upload_id)
                assert fresh.purge_required is True and fresh.purge_attempts == 1
                assert fresh.purge_last_error == "retained"
                assert (settings.document_quarantine_bucket, stray) in storage.objects
                events = _upload_audit(db, action="upload_object_purge_retained", upload_id=upload_id)
            assert len(events) == 1
            payload = events[0]
            assert payload["retained_count"] == 1 and payload["listing_complete"] is True
            assert payload["retries_scheduled"] is True and payload["purge_error"] is None
            assert payload["retained_keys_sample"] == [stray]
            # Deuxième passe, stockage rétabli: le registre se ferme.
            storage.fail_prefix_deletions = set()
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                summary = retry_retained_document_purges(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert summary.released == 1
                assert (settings.document_quarantine_bucket, stray) not in storage.objects
                assert db.get(DocumentUpload, upload_id).purge_required is False
            assert prefix.startswith(f"quarantine/{identity.organization_id}/{document['id']}/")
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_one_failing_row_does_not_stop_the_batch() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            upload_ids = []
            for index in range(3):
                _document, instruction = create_document_and_upload_intent(client, title=f"Pièce {index}")
                _promote(client, storage, instruction)
                upload_ids.append(uuid.UUID(instruction["upload"]["id"]))
            middle = _upload_row(str(upload_ids[1]))
            # Une seule ligne doit échouer: le cas exact où une batch entière ne doit
            # pas être perdue à cause d'un préfixe injoignable.
            original = storage.delete_prefix

            def flaky(*, bucket: str, prefix: str):
                if prefix == _upload_object_prefix(middle):
                    raise ObjectStorageError("stockage indisponible sur ce préfixe")
                return original(bucket=bucket, prefix=prefix)

            storage.delete_prefix = flaky  # type: ignore[method-assign]
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                summary = retry_retained_document_purges(db, settings=settings, storage=storage, limit=10)
                db.commit()
                assert summary.examined == 3 and summary.released == 2 and summary.failed_rows == 1
                rows = {row.id: row for row in db.scalars(select(DocumentUpload).where(DocumentUpload.id.in_(upload_ids)))}
                assert rows[upload_ids[0]].purge_required is False
                assert rows[upload_ids[2]].purge_required is False
                broken = rows[upload_ids[1]]
                assert broken.purge_required is True, "la ligne en échec garde son marqueur"
                assert broken.purge_attempts == 1 and broken.purge_last_error == "storage_error", (
                    "une tentative qui n'a pas atteint le stockage est quand même comptée"
                )
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_retry_is_idempotent_across_passes() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Rejoue double")
            _promote(client, storage, instruction)
            upload_id = uuid.UUID(instruction["upload"]["id"])
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                assert (
                    retry_retained_document_purges(
                        db, settings=settings, storage=storage, organization_id=identity.organization_id
                    ).released
                    == 1
                )
                db.commit()
            calls_after_first_pass = len(storage.prefix_deletions)
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                second = retry_retained_document_purges(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert second == type(second)(examined=0, released=0, retained=0, failed_rows=0)
                assert len(storage.prefix_deletions) == calls_after_first_pass
                row = db.get(DocumentUpload, upload_id)
                assert row.purge_required is False and row.purge_attempts == 1
                events = list(
                    db.scalars(
                        select(func.count()).select_from(AuditEvent).where(
                            AuditEvent.entity_id == upload_id,
                            AuditEvent.action == "document.upload_purge_released",
                        )
                    )
                )
                assert events[0] == 1, "un registre fermé ne se referme pas deux fois"
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_attempts_budget_leaves_an_open_ledger_for_an_operator() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Budget épuisé")
            # La libération échoue dès le départ: l'objet est encore sous le préfixe, et
            # le registre reste ouvert - c'est l'état que la reprise doit trouver.
            storage.fail_operations = {"delete_prefix"}
            _promote(client, storage, instruction)
            upload_id = uuid.UUID(instruction["upload"]["id"])
            row = _upload_row(str(upload_id))
            storage.fail_operations = set()
            storage.fail_prefix_deletions = {row.quarantine_storage_key}
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                row = db.get(DocumentUpload, upload_id)
                row.purge_attempts = PURGE_MAX_ATTEMPTS - 1
                db.commit()
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                summary = retry_retained_document_purges(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert summary.retained == 1
                row = db.get(DocumentUpload, upload_id)
                assert row.purge_required is True, "l'épuisement du budget ne doit pas masquer l'objet restant"
                assert row.purge_last_error == "purge_attempts_exhausted"
                events = _upload_audit(db, action="upload_object_purge_retained", upload_id=upload_id)
                assert events[-1]["retries_scheduled"] is False
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                assert retry_retained_document_purges(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                ).examined == 0, (
                    "le worker arrête de marteler, le registre reste ouvert"
                )
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_abandoned_intent_is_swept_per_row_and_the_ledger_closes() -> None:
    """Le sweep isole les lignes et ne détruit rien avant le commit."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document_a, instruction_a = create_document_and_upload_intent(client, title="Abandon 1")
            document_b, instruction_b = create_document_and_upload_intent(client, title="Abandon 2")
            del document_b
            for instruction in (instruction_a, instruction_b):
                with SessionLocal() as db:
                    set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                    row = db.get(DocumentUpload, uuid.UUID(instruction["upload"]["id"]))
                    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
                    db.commit()

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                swept = sweep_expired_document_uploads(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                assert swept == 2
                # Avant commit: le stockage n'a pas encore été touché.
                assert storage.prefix_deletions == []
                row = db.get(DocumentUpload, uuid.UUID(instruction_a["upload"]["id"]))
                assert row.status == DocumentUploadStatus.EXPIRED
                assert row.purge_required is True and row.purge_reason == "abandoned"
                db.commit()
            assert len(storage.prefix_deletions) == 2, "la libération part avec le commit"
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                events = _upload_audit(
                    db, action="document.upload_swept", upload_id=uuid.UUID(instruction_a["upload"]["id"])
                )
                assert events[0]["quarantine_purge"] == "scheduled"
                assert "quarantine_deleted" not in events[0]
                statuses = list(
                    db.scalars(
                        select(Document.status).where(Document.organization_id == identity.organization_id)
                    ).all()
                )
            # Un abandon laisse une pièce en échec, pas en attente éternelle.
            assert statuses and all(item == DocumentStatus.FAILED for item in statuses), statuses
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_rolled_back_transition_touches_no_object() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Rollback")
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                row = db.get(DocumentUpload, uuid.UUID(instruction["upload"]["id"]))
                row.status = DocumentUploadStatus.EXPIRED
                from app.documents.purge import schedule_upload_purge

                schedule_upload_purge(
                    db,
                    storage=storage,
                    settings=settings,
                    upload=row,
                    organization_id=identity.organization_id,
                    reason="abandoned",
                    requested_at=datetime.now(timezone.utc),
                )
                db.flush()
                assert storage.prefix_deletions == [], "rien n'est détruit avant le commit"
                db.rollback()
                assert storage.prefix_deletions == [], "un rollback ne doit déclencher aucune libération"
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_prefix_guards_reject_anything_not_owned_by_one_tenant() -> None:
    other_org = uuid.uuid4()
    for bad in (
        "",
        "/",
        "quarantine/",
        "clean/",
        f"clean/{other_org}/",  # préfixe trop large: toute l'organisation
        f"quarantine/{uuid.uuid4()}/../../",
        f"{other_org}/document/upload/",  # préfixe non typé
        "quarantine/not-a-uuid/document/upload/source",
    ):
        with pytest.raises(ObjectStorageError):
            validate_tenant_scoped_prefix(bad)
    good = f"quarantine/{other_org}/{uuid.uuid4()}/{uuid.uuid4()}/"
    assert validate_tenant_scoped_prefix(good) == good


def test_retry_never_reaches_another_tenant_objects() -> None:
    """Deux locataires, une passe par locataire : les octets d'autrui ne bougent pas."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Locataire A")
            _promote(client, storage, instruction)
            row = _upload_row(instruction["upload"]["id"])
            prefix_a = _upload_object_prefix(row)

            # Un objet d'un autre locataire, posé dans le même bucket: rien dans la
            # passe de A ne doit le nommer, encore moins le supprimer.
            other_key = f"quarantine/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/source"
            calls_before_retry = len(storage.prefix_deletions)
            storage.objects[(settings.document_quarantine_bucket, other_key)] = StoredObject(
                payload=b"bytes of another tenant", content_type="application/pdf", etag="cafe0000"
            )
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                summary = retry_retained_document_purges(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id, limit=50
                )
                db.commit()
            assert summary.released == 1 and summary.examined == 1, summary
            touched_prefixes = [entry[1] for entry in storage.prefix_deletions[calls_before_retry:]]
            assert touched_prefixes == [prefix_a], touched_prefixes
            assert all(item.startswith(f"quarantine/{identity.organization_id}/") for item in touched_prefixes), (
                "la passe ne doit composer aucun préfixe hors de son locataire"
            )
            assert (settings.document_quarantine_bucket, other_key) in storage.objects, (
                "un objet d'un autre tenant ne doit jamais être touché par cette passe"
            )
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner
