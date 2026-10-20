"""Reprise des purges retenues sous PostgreSQL réel : concurrence, verrouillage, tenants.

Ces tests tournent sur le même serveur jetable que la suite d'isolation RLS (variable
``VERICLAIM_TEST_POSTGRES_URL``) et utilisent le rôle applicatif de runtime, pas un
super-utilisateur: la preuve porte donc sur ce que l'application peut réellement faire,
y compris ``FOR UPDATE SKIP LOCKED`` et les politiques RLS.

Ils ne prétendent pas valider MinIO ni Neon: seul le stockage d'objets est simulé, et le
comportant mesuré est l'exclusion entre deux passe de reprise.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.documents.service import _upload_object_prefix, retry_retained_document_purges
from app.documents.storage import PrefixDeletionResult
from app.identity.service import set_db_request_context
from app.models.domain import AuditEvent, Document, DocumentUpload
from tests.test_postgres_operational_integrity import verify_audit_chain
from tests.test_postgres_tenant_isolation import (
    APP_PASSWORD,
    APP_ROLE,
    ADMIN_DSN,
    _sqlalchemy_url,
    cluster,
    tenants,
)

pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="Disposable PostgreSQL URL not configured")


@dataclass
class CountingStorage:
    """Stockage de test qui compte les gestes et peut se mettre en travers d'une passe.

    ``block_once`` retient la première libération tant qu'un second appelant n'est pas
    entré: c'est la fenêtre pendant laquelle les lignes restent verrouillées par la
    transaction de la première passe, exactement ce que SKIP LOCKED doit résoudre.
    """

    calls: list[tuple[str, str]] = field(default_factory=list)
    blocked: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    barrier: Barrier | None = None
    _block_first: bool = True

    def delete_prefix(self, *, bucket: str, prefix: str) -> PrefixDeletionResult:
        self.calls.append((bucket, prefix))
        if self._block_first and self.barrier is not None:
            self._block_first = False
            self.blocked.set()
            self.barrier.wait(10)
        return PrefixDeletionResult(
            bucket=bucket,
            prefix=prefix,
            deleted_keys=(prefix + "source",),
            retained_keys=(),
            listed_keys=1,
            listing_complete=True,
            error_code=None,
        )


@pytest.fixture()
def runtime_engine(cluster):
    engine = create_engine(_sqlalchemy_url(ADMIN_DSN, dbname=cluster.database, user=APP_ROLE, password=APP_PASSWORD))
    try:
        yield engine
    finally:
        engine.dispose()


def _open_ledgers(engine, *, organization: uuid.UUID, count: int, started: datetime) -> list[uuid.UUID]:
    """Crée pour un tenant des imports dont le registre de libération est ouvert."""

    upload_ids: list[uuid.UUID] = []
    with Session(engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=organization)
        for index in range(count):
            document = Document(
                organization_id=organization,
                document_key=uuid.uuid4().hex,
                title=f"Pièce {index}",
            )
            db.add(document)
            db.flush()
            upload_id = uuid.uuid4()
            db.add(
                DocumentUpload(
                    id=upload_id,
                    organization_id=organization,
                    document_id=document.id,
                    quarantine_storage_key=f"quarantine/{organization}/{document.id}/{upload_id}/source",
                    source_filename=f"piece-{index}.pdf",
                    declared_content_type="application/pdf",
                    declared_size_bytes=128,
                    status="pending_upload",
                    expires_at=started + timedelta(minutes=index),
                    purge_required=True,
                    purge_kind="quarantine_prefix",
                    purge_reason="abandoned",
                    purge_requested_at=started + timedelta(seconds=index),
                )
            )
            upload_ids.append(upload_id)
        db.commit()
    return upload_ids


def test_two_concurrent_passes_never_share_a_row(runtime_engine, tenants) -> None:
    """SKIP LOCKED prouvé: trois lignes ouvertes, deux passe simultanées, chaque ligne traitée une fois."""
    started = datetime.now(timezone.utc) - timedelta(minutes=5)
    upload_ids = _open_ledgers(runtime_engine, organization=tenants.organization_a, count=3, started=started)
    barrier = Barrier(2)
    storage = CountingStorage(barrier=barrier)

    def run_pass(limit: int) -> tuple[int, int]:
        with Session(runtime_engine) as db:
            set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
            summary = retry_retained_document_purges(
                db,
                settings=_settings(),
                storage=storage,
                organization_id=tenants.organization_a,
                limit=limit,
            )
            db.commit()
            return summary.examined, summary.released

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_pass, 2)
        # La première passe est à l'intérieur de sa libération: ses deux lignes sont
        # verrouillées. La seconde passe ne doit ni attendre ni les redoubler.
        assert storage.blocked.wait(8)
        second = pool.submit(run_pass, 2)
        barrier.wait(10)  # libère la première passe, qui attendait la seconde
        first_result = first.result(timeout=20)
        second_result = second.result(timeout=20)

    assert sorted([first_result[0], second_result[0]]) == [1, 2]
    assert first_result[1] + second_result[1] == 3, "les trois lignes sont fermées, sans doublon"
    assert len(storage.calls) == 3, storage.calls
    assert len({prefix for _bucket, prefix in storage.calls}) == 3, "aucun préfixe libéré deux fois"
    with Session(runtime_engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
        rows = list(db.scalars(select(DocumentUpload).where(DocumentUpload.id.in_(upload_ids))).all())
        assert all(row.purge_required is False for row in rows)
        assert sorted(row.purge_attempts for row in rows) == [1, 1, 1], (
            "une tentative par ligne et par passe: le compteur ne double pas sous concurrence"
        )
        closure_count = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "document.upload_purge_released", AuditEvent.entity_id.in_(upload_ids))
        )
        assert closure_count == 3
        assert verify_audit_chain(db, tenants.organization_a).is_valid is True, "la chaîne d'audit doit rester valide après les événements du registre"


def test_a_pass_never_reaches_another_tenants_ledger(runtime_engine, tenants) -> None:
    """Une passe adressée au tenant A ne voit ni ne modifie les lignes ouvertes de B."""
    started = datetime.now(timezone.utc) - timedelta(minutes=2)
    ids_a = _open_ledgers(runtime_engine, organization=tenants.organization_a, count=2, started=started)
    ids_b = _open_ledgers(runtime_engine, organization=tenants.organization_b, count=2, started=started)
    storage = CountingStorage()

    with Session(runtime_engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
        summary = retry_retained_document_purges(
            db, settings=_settings(), storage=storage, organization_id=tenants.organization_a, limit=10
        )
        db.commit()

    assert summary.examined == 2 and summary.released == 2
    assert {prefix.split("/")[1] for _bucket, prefix in storage.calls} == {str(tenants.organization_a)}
    with Session(runtime_engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
        assert all(db.get(DocumentUpload, upload_id).purge_required is False for upload_id in ids_a)
        # B, sous son propre contexte, retrouve ses registres intacts: rien n'a été
        # fermé, aucune tentative consommée, et le stockage n'a jamais été appelé pour lui.
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_b)
        rows_b = [db.get(DocumentUpload, upload_id) for upload_id in ids_b]
        assert all(row.purge_required is True and row.purge_attempts == 0 for row in rows_b)
        assert not any(prefix.split("/")[1] == str(tenants.organization_b) for _bucket, prefix in storage.calls)
        for row in rows_b:
            assert _upload_object_prefix(row).startswith(f"quarantine/{tenants.organization_b}/")


def test_a_foreign_prefix_cannot_be_injected_into_the_release(runtime_engine, tenants) -> None:
    """La garde de préfixe est à la frontière du stockage, pas seulement dans le service."""
    from app.documents.storage import ObjectStorageError, validate_tenant_scoped_prefix

    other = tenants.organization_b
    for bad in ("", "quarantine/", f"quarantine/{other}/", f"clean/{other}", f"quarantine/{other}/../../"):
        with pytest.raises(ObjectStorageError):
            validate_tenant_scoped_prefix(bad)
    ids = _open_ledgers(
        runtime_engine,
        organization=tenants.organization_a,
        count=1,
        started=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with Session(runtime_engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
        row = db.get(DocumentUpload, ids[0])
        assert row is not None, "la ligne créée sous le contexte A doit être lisible sous ce même contexte"
        # Le préfixe que la passe produirait est ancré sur la ligne elle-même.
        prefix = _upload_object_prefix(row)
        assert prefix == f"quarantine/{row.organization_id}/{row.document_id}/{row.id}/"
        db.rollback()


def _settings():
    from app.core.config import settings

    return settings


@pytest.mark.parametrize("cancel", ["rollback", "close"])
@pytest.mark.parametrize("nested", [False, True])
def test_cancelled_purge_cannot_survive_session_reuse(runtime_engine, tenants, cancel, nested):
    """Real PostgreSQL rollback of both state and side-effect authorization, as app role."""
    from app.documents.purge import schedule_upload_purge

    upload_id = _open_ledgers(
        runtime_engine, organization=tenants.organization_a, count=1,
        started=datetime.now(timezone.utc) - timedelta(minutes=5),
    )[0]
    storage = CountingStorage()
    with Session(runtime_engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
        row = db.get(DocumentUpload, upload_id)
        row.purge_required = False
        row.purge_kind = None
        db.commit()
        savepoint = db.begin_nested() if nested else None
        schedule_upload_purge(
            db, storage=storage, settings=_settings(), upload=row,
            organization_id=tenants.organization_a, reason="abandoned",
            requested_at=datetime.now(timezone.utc),
        )
        db.flush()
        if savepoint:
            savepoint.commit()
        assert storage.calls == []
        getattr(db, cancel)()
        db.commit()
        assert storage.calls == []
        assert db.get(DocumentUpload, upload_id).purge_required is False


def test_savepoint_rollback_preserves_only_outer_purge(runtime_engine, tenants):
    from app.documents.purge import schedule_upload_purge

    ids = _open_ledgers(
        runtime_engine, organization=tenants.organization_a, count=2,
        started=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    storage = CountingStorage()
    with Session(runtime_engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
        rows = [db.get(DocumentUpload, upload_id) for upload_id in ids]
        for row in rows:
            row.purge_required = False
            row.purge_kind = None
        db.commit()
        # Read into this transaction before creating the inner savepoint.
        rows = [db.get(DocumentUpload, upload_id) for upload_id in ids]
        outer_prefix = _upload_object_prefix(rows[0])
        def schedule(row):
            schedule_upload_purge(
                db, storage=storage, settings=_settings(), upload=row,
                organization_id=tenants.organization_a, reason="abandoned",
                requested_at=datetime.now(timezone.utc),
            )
        schedule(rows[0])
        savepoint = db.begin_nested()
        schedule(rows[1])
        db.flush()
        savepoint.rollback()
        assert storage.calls == []
        db.commit()
        assert storage.calls == [(_settings().document_quarantine_bucket, outer_prefix)]
        assert db.get(DocumentUpload, ids[0]).purge_required is True
        assert db.get(DocumentUpload, ids[1]).purge_required is False


def test_second_pass_finishes_while_first_holds_row_locks(runtime_engine, tenants):
    """Overlap is mandatory, not merely two futures that might run sequentially."""
    _open_ledgers(
        runtime_engine, organization=tenants.organization_a, count=3,
        started=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    entered, release = Event(), Event()

    class BlockedStorage(CountingStorage):
        def delete_prefix(self, *, bucket, prefix):
            if self._block_first:
                self._block_first = False
                entered.set()
                assert release.wait(15), "test did not release first worker"
            return super().delete_prefix(bucket=bucket, prefix=prefix)

    first_storage, second_storage = BlockedStorage(), CountingStorage()

    def run(storage):
        with Session(runtime_engine) as db:
            set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=tenants.organization_a)
            summary = retry_retained_document_purges(
                db, settings=_settings(), storage=storage,
                organization_id=tenants.organization_a, limit=2,
            )
            db.commit()
            return summary

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, first_storage)
        try:
            assert entered.wait(8)
            # First has locked two rows and cannot finish until this thread releases it.
            second = pool.submit(run, second_storage).result(timeout=10)
            assert second.examined == second.released == 1
            assert not first.done()
        finally:
            release.set()
        assert first.result(timeout=10).released == 2
    calls = first_storage.calls + second_storage.calls
    assert len(calls) == len(set(calls)) == 3
