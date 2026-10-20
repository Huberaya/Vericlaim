"""Chantier B: durable document pipeline states and the vault lifecycle API.

Covers three confirmed defects: an upload intent abandoned by its client stayed
open forever with its bytes in quarantine; the claim step's repair branches did
not reconcile the logical document status; and no endpoint could enumerate or
retire a document, so a tenant had neither a vault view nor any way out of a
failed import.

Deliberately its own module, so it never edits the same test lines as the
parallel authentication branch.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.extraction import claim_next_document_extraction_job
from app.documents.service import (
    UPLOAD_SWEEP_GRACE_SECONDS,
    archive_document,
    sweep_expired_document_uploads,
)
from app.identity.service import set_db_request_context
from app.models.domain import (
    AuditEvent,
    Document,
    DocumentExtractionJobStatus,
    DocumentStatus,
    DocumentUpload,
    DocumentUploadStatus,
    DocumentVersion,
    ExtractionStatus,
)
from tests.auth_support import authenticate_client
from tests.document_lifecycle_support import (
    PDF_BYTES,
    create_document_and_upload_intent,
    install_document_fakes,
    promote_clean_version,
)


# Abandoned imports must not stay open, and must not look valid


def test_abandoned_upload_is_expired_and_its_quarantine_bytes_released() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document, instruction = create_document_and_upload_intent(client, title="Import abandonné")
            bucket, key = storage.upload_latest(payload=PDF_BYTES)
            assert (bucket, key) in storage.objects
            upload_id = UUID(instruction["upload"]["id"])

            with SessionLocal() as db:
                row = db.get(DocumentUpload, upload_id)
                # Push the intent well past its TTL plus the scan-time grace.
                row.expires_at = row.expires_at - timedelta(
                    seconds=settings.document_upload_ttl_seconds + UPLOAD_SWEEP_GRACE_SECONDS + settings.document_clamav_timeout_seconds + 1
                )
                db.commit()

            with SessionLocal() as db:
                swept = sweep_expired_document_uploads(
                    db,
                    settings=settings,
                    storage=storage,
                    organization_id=identity.organization_id,
                )
                db.commit()
                assert swept == 1

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                upload = db.get(DocumentUpload, upload_id)
                assert upload.status == DocumentUploadStatus.EXPIRED
                assert upload.scan_error_code == "upload_abandoned"
                stored = db.get(Document, UUID(document["id"]))
                assert stored is not None
                # A shell with no promoted version is a failed import, not a
                # document still awaiting a control.
                assert stored.status == DocumentStatus.FAILED
                assert db.scalar(
                    select(AuditEvent).where(
                        AuditEvent.organization_id == identity.organization_id,
                        AuditEvent.action == "document.upload_swept",
                    )
                ) is not None
                db.commit()

            assert (bucket, key) not in storage.objects, "quarantine bytes must be released"
            assert storage.copy_requests == [], "an abandoned import is never promoted"

            # The sweep is idempotent: a second pass finds nothing to close.
            with SessionLocal() as db:
                again = sweep_expired_document_uploads(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert again == 0
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_upload_still_inside_its_scan_budget_is_not_swept() -> None:
    """A slow ClamAV verdict must never be raced by the housekeeping sweep."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _, instruction = create_document_and_upload_intent(client, title="Scan en vol")
            bucket, key = storage.upload_latest(payload=PDF_BYTES)
            upload_id = UUID(instruction["upload"]["id"])

            with SessionLocal() as db:
                row = db.get(DocumentUpload, upload_id)
                row.status = DocumentUploadStatus.SCANNING
                # Expired link, but still inside the grace window.
                row.expires_at = row.expires_at - timedelta(seconds=1)
                db.commit()

            with SessionLocal() as db:
                swept = sweep_expired_document_uploads(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert swept == 0

            with SessionLocal() as db:
                assert db.get(DocumentUpload, upload_id).status == DocumentUploadStatus.SCANNING
            assert (bucket, key) in storage.objects, "bytes under scan must remain untouched"
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_completed_upload_is_never_swept() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _, instruction = create_document_and_upload_intent(client, title="Version propre")
            storage.upload_latest(payload=PDF_BYTES)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completion.status_code == 201, completion.text
            upload_id = UUID(instruction["upload"]["id"])

            with SessionLocal() as db:
                db.get(DocumentUpload, upload_id).expires_at -= timedelta(days=1)
                db.commit()

            with SessionLocal() as db:
                swept = sweep_expired_document_uploads(
                    db, settings=settings, storage=storage, organization_id=identity.organization_id
                )
                db.commit()
                assert swept == 0, "a promoted version is never recycled"
            clean_keys = [bucket_key for bucket_key, _ in storage.objects.items() if bucket_key[0] == settings.document_clean_bucket]
            assert clean_keys, "the promoted clean object must survive the sweep"
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


# Worker reconciliation: a repaired claim must reconcile the logical status


def test_claim_repair_reconciles_a_stalled_document_status() -> None:
    """A version finished out-of-band must move the document out of PROCESSING.

    The two idempotent branches of the claim step close a job without running
    it. They are also the branches that repair a job whose worker died after
    committing its result, so they have to converge on the version-derived
    status exactly like every other transition does; otherwise the vault shows
    an endless in-progress treatment for finished evidence.
    """
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document, instruction = create_document_and_upload_intent(client, title="Reprise après crash")
            storage.upload_latest(payload=PDF_BYTES)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completion.status_code == 201, completion.text

            # Simulate a lost write: the version reached a terminal state while
            # its job is still queued and the document still says PROCESSING.
            with SessionLocal() as db:
                version = db.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.organization_id == identity.organization_id,
                        DocumentVersion.document_id == UUID(document["id"]),
                    )
                )
                assert version is not None
                version.extraction_status = ExtractionStatus.COMPLETED
                version.extraction_job.status = DocumentExtractionJobStatus.QUEUED
                version.extraction_job.lease_expires_at = None
                db.get(Document, version.document_id).status = DocumentStatus.PROCESSING
                db.commit()
                version_id = version.id
                document_id = version.document_id

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                claim = claim_next_document_extraction_job(
                    db, settings=settings, organization_id=identity.organization_id, worker_id="pytest-repair"
                )
                assert claim is None, "a completed version must not be re-extracted"
                assert db.get(Document, document_id).status == DocumentStatus.READY
                db.commit()

            with SessionLocal() as db:
                version = db.get(DocumentVersion, version_id)
                version.extraction_status = ExtractionStatus.REVIEW_REQUIRED
                version.extraction_job.status = DocumentExtractionJobStatus.QUEUED
                db.get(Document, document_id).status = DocumentStatus.PROCESSING
                db.commit()

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                assert (
                    claim_next_document_extraction_job(
                        db, settings=settings, organization_id=identity.organization_id, worker_id="pytest-repair"
                    )
                    is None
                )
                assert db.get(Document, document_id).status == DocumentStatus.READY
                db.commit()
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


# Document lifecycle API: enumerate, retire, and never leak across tenants


def test_document_list_is_scoped_paged_and_filterable() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            created = []
            for index in range(3):
                response = client.post(
                    "/api/v1/documents",
                    json={"title": f"Pièce {index}", "document_type": "certificate" if index else "supplier_declaration"},
                )
                assert response.status_code == 201, response.text
                created.append(response.json())

            def titles_in(payload: dict) -> list[str]:
                return [item["title"] for item in payload["items"]]

            listing = client.get("/api/v1/documents", params={"limit": 100})
            assert listing.status_code == 200, listing.text
            body = listing.json()
            assert set(body) == {"items", "next_cursor"}, "contrat de page aligné sur celui du catalogue"
            assert {item["id"] for item in body["items"]} == {item["id"] for item in created}
            assert body["next_cursor"] is None, "une page complète sans suite ne doit pas promettre de curseur"
            assert [item["title"] for item in body["items"]] == sorted(
                item["title"] for item in body["items"]
            ), "l'ordre doit être stable pour servir de frontière de curseur"

            filtered = client.get("/api/v1/documents", params={"document_type": "certificate"}).json()
            assert len(filtered["items"]) == 2
            assert {item["id"] for item in filtered["items"]} == {created[1]["id"], created[2]["id"]}
            assert len(client.get("/api/v1/documents", params={"document_type": "supplier_declaration"}).json()["items"]) == 1
            # Les titres créés ici sont "Pièce 0/1/2": la recherche porte sur le
            # titre et ne tient pas compte de la casse.
            assert len(client.get("/api/v1/documents", params={"q": "PIÈCE 1"}).json()["items"]) == 1
            assert len(client.get("/api/v1/documents", params={"q": "pièce"}).json()["items"]) == 3
            assert client.get("/api/v1/documents", params={"q": "absent"}).json()["items"] == []
            quarantined = client.get("/api/v1/documents", params={"status": "quarantined"}).json()
            assert len(quarantined["items"]) == 3
            assert client.get("/api/v1/documents", params={"status": "ready"}).json()["items"] == []

            page = client.get("/api/v1/documents", params={"limit": 2}).json()
            assert len(page["items"]) == 2 and page["next_cursor"]
            rest = client.get("/api/v1/documents", params={"limit": 2, "cursor": page["next_cursor"]}).json()
            assert len(rest["items"]) == 1
            assert rest["next_cursor"] is None
            assert {i["id"] for i in page["items"]}.isdisjoint({i["id"] for i in rest["items"]})
            assert titles_in(page) + titles_in(rest) == titles_in({
                "items": sorted(page["items"] + rest["items"], key=lambda item: item["title"])
            })
            assert client.get("/api/v1/documents", params={"cursor": "non-base-64!!"}).status_code == 422

            assert client.get("/api/v1/documents", params={"limit": 500}).status_code == 422
            assert client.get("/api/v1/documents", params={"limit": 0}).status_code == 422

            with SessionLocal() as db:
                row = db.get(Document, UUID(created[0]["id"]))
                assert row is not None and row.deleted_at is None and row.status != DocumentStatus.ARCHIVED

            archived = client.post(f"/api/v1/documents/{created[0]['id']}/archive")
            assert archived.status_code == 200, archived.text
            assert archived.json()["already_archived"] is False
            assert archived.json()["document"]["status"] == "archived"

            assert len(client.get("/api/v1/documents").json()["items"]) == 2
            assert client.get(f"/api/v1/documents/{created[0]['id']}").status_code == 404
            retired_upload = client.post(
                f"/api/v1/documents/{created[0]['id']}/versions",
                json={"source_filename": "fiche.pdf", "content_type": "application/pdf", "size_bytes": len(PDF_BYTES)},
            )
            assert retired_upload.status_code == 404, "a retired document accepts no new version"

            # A second organisation must not see, reach or retire the first
            # one's documents, even with the identifiers in hand.
            with TestClient(app) as other_client:
                other = authenticate_client(other_client, role_code="analyst")
                assert other.organization_id != identity.organization_id
                assert other_client.get("/api/v1/documents").json()["items"] == []
                assert other_client.get(f"/api/v1/documents/{created[1]['id']}").status_code == 404
                assert other_client.post(f"/api/v1/documents/{created[1]['id']}/archive").status_code == 404
                with SessionLocal() as db:
                    still_there = db.get(Document, UUID(created[1]["id"]))
                    assert still_there is not None and still_there.deleted_at is None

            # Re-archiving is a no-op, not an error, and stays tenant-scoped.
            with SessionLocal() as db:
                document, already = archive_document(
                    db,
                    organization_id=identity.organization_id,
                    actor_user_id=identity.user_id,
                    document_id=UUID(created[0]["id"]),
                    request_id=None,
                )
                assert already is True
                assert document.status == DocumentStatus.ARCHIVED
                db.commit()
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_archive_retires_the_document_without_destroying_evidence() -> None:
    """Visibility transition only: objects, versions and audit rows survive."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document, instruction = create_document_and_upload_intent(client, title="Preuve à archiver")
            storage.upload_latest(payload=PDF_BYTES)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completion.status_code == 201, completion.text
            clean_before = sorted(key for bucket, key in storage.objects if bucket == settings.document_clean_bucket)
            assert clean_before

            assert client.post(f"/api/v1/documents/{document['id']}/archive").status_code == 200

            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                stored = db.get(Document, UUID(document["id"]))
                assert stored is not None and stored.deleted_at is not None
                assert stored.status == DocumentStatus.ARCHIVED
                assert (
                    db.scalar(
                        select(AuditEvent).where(
                            AuditEvent.organization_id == identity.organization_id,
                            AuditEvent.action == "document.archived",
                        )
                    )
                    is not None
                )
                db.commit()

            assert sorted(key for bucket, key in storage.objects if bucket == settings.document_clean_bucket) == clean_before
            with SessionLocal() as db:
                version = db.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.organization_id == identity.organization_id,
                        DocumentVersion.document_id == UUID(document["id"]),
                    )
                )
                assert version is not None, "immutable evidence is preserved for audit"
            # Une pièce retirée reste prouvable: elle disparaît du flux actif
            # mais reste énumérable par un filtre explicite.
            retired = client.get("/api/v1/documents", params={"status": "archived"})
            assert retired.status_code == 200
            assert len(retired.json()["items"]) == 1, "l'archive doit rester énumérable par le locataire"
            assert retired.json()["items"][0]["id"] == document["id"]
            assert retired.json()["items"][0]["status"] == "archived"
            assert document["id"] not in {item["id"] for item in client.get("/api/v1/documents").json()["items"]}
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_archive_requires_the_management_permission() -> None:
    """Retirement is a management action; enumeration stays a read action."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            manager = authenticate_client(client, role_code="analyst")
            document, _ = create_document_and_upload_intent(client, title="Sanction lecture seule")
        with TestClient(app) as reader_client:
            reader = authenticate_client(reader_client, role_code="viewer")
            assert reader.organization_id != manager.organization_id
            # The reader's own vault is empty, and an authorization failure is
            # reported before any existence lookup, so a foreign identifier
            # never becomes an oracle.
            assert reader_client.get("/api/v1/documents").json()["items"] == []
            assert reader_client.post(f"/api/v1/documents/{document['id']}/archive").status_code == 403
            assert reader_client.get("/api/v1/documents").status_code == 200
            assert reader_client.post("/api/v1/documents", json={"title": "Pièce du lecteur"}).status_code == 403
            assert (
                reader_client.post(f"/api/v1/documents/{uuid4()}/archive").status_code == 403
            ), "documents:read must not be able to retire a document"
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner
