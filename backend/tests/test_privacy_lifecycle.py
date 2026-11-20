"""C20 — retention, erasure and export.

The plan states the defect: « Pas de suppression définitive outillée, pas d'export
complet côté client, politique de rétention factice ». It also states the trap:
« L'effacement ne doit pas transformer une piste d'audit valide en chaîne invalide.
Utiliser le mécanisme de conservation légale plutôt que la suppression de lignes
d'audit. » And the chantier that came before it (C8) left the policy *undeclared* by
default, which is honest but leaves the purge with nothing to apply.

Each test below targets one property that makes the replacement usable:

* nothing is purged when no duration is declared — no invented window;
* what is declared is applied, and recent data is left alone;
* the longest applicable window wins (evidence archive);
* a legal hold stops both the purge and an on-demand erasure;
* an erasure removes the storage objects **and** the rows, or neither;
* the audit chain still verifies after an erasure, and carries the tombstone;
* audit rows are never deleted;
* the export contains the rows, not a count of them.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.audit.service import verify_audit_chain
from app.core.config import settings
from app.core.database import SessionLocal, create_tables
from app.identity.service import append_audit_event, set_db_request_context
from app.main import app
from app.models.domain import (
    AuditEvent,
    Document,
    DocumentSegment,
    DocumentVersion,
    Evidence,
    EvidenceStatus,
    EvidenceType,
    EmailMessage,
    EmailMessageStatus,
    ExtractionStatus,
    LegalHold,
    OrganizationRetentionPolicy,
    SegmentType,
)
from tests.auth_support import TestIdentity, authenticate_client
from tests.document_support import FakeObjectStorage

PDF_BYTES = b"%PDF-1.7\n% privacy-lifecycle-test\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


@pytest.fixture()
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


def _install_storage():
    old = app.state.document_storage
    storage = FakeObjectStorage()
    app.state.document_storage = storage
    return storage, old


def _declare_policy(identity: TestIdentity, *, documents: int | None, evidence: int | None, audit: int | None = None):
    with SessionLocal() as db:
        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        db.add(
            OrganizationRetentionPolicy(
                organization_id=identity.organization_id,
                documents_retention_years=documents,
                evidence_archive_retention_years=evidence,
                audit_trail_retention_years=audit,
                configured_by_user_id=identity.user_id,
            )
        )
        db.commit()


def _document_with_version(
    identity: TestIdentity,
    *,
    age_days: int = 0,
    storage: FakeObjectStorage | None = None,
    with_segment: bool = True,
    with_evidence: bool = False,
    storage_key: str | None = None,
    title_marker: str = "Déclaration fournisseur confidentielle",
    with_audit: bool = False,
) -> dict[str, UUID]:
    """A realistic document: row, version, stored object, segment, optional evidence."""
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    key = storage_key or f"vericlaim/documents/{uuid4().hex}"
    with SessionLocal() as db:
        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        document = Document(
            organization_id=identity.organization_id,
            document_key=f"DOC-{uuid4().hex[:10]}",
            title=title_marker,
            document_type="supplier_declaration",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=identity.organization_id,
            document_id=document.id,
            version_number=1,
            source_filename="declaration.pdf",
            content_type="application/pdf",
            storage_key=key,
            sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
            size_bytes=len(PDF_BYTES),
            extraction_status=ExtractionStatus.COMPLETED,
        )
        db.add(version)
        db.flush()
        segment = None
        if with_segment:
            segment = DocumentSegment(
                organization_id=identity.organization_id,
                document_version_id=version.id,
                sequence_number=1,
                page_number=1,
                segment_type=SegmentType.PARAGRAPH,
                text="Notre emballage est 100 % recyclable et neutre en carbone.",
                source_sha256=hashlib.sha256(b"segment").hexdigest(),
            )
            db.add(segment)
            db.flush()
        if with_evidence:
            db.add(
                Evidence(
                    organization_id=identity.organization_id,
                    document_version_id=version.id,
                    evidence_type=EvidenceType.CERTIFICATE,
                    status=EvidenceStatus.VERIFIED,
                    reference="CERT-1",
                )
            )
            db.flush()
        document.created_at = created
        version.created_at = created
        db.flush()
        if with_audit:
            append_audit_event(
                db,
                organization_id=identity.organization_id,
                actor_user_id=identity.user_id,
                entity_type="document",
                entity_id=document.id,
                action="document.created",
                payload={"document_key": document.document_key, "title": document.title},
            )
        db.commit()
        identifiers = {
            "document_id": document.id,
            "version_id": version.id,
            "segment_id": segment.id if segment else None,  # type: ignore[dict-item]
        }
    if storage is not None:
        # The object is written where the version says it lives: this is the state
        # after a successful promotion, which is what the retention and erasure code
        # has to deal with. `put_bytes` is the same call the extraction worker makes.
        storage.put_bytes(
            bucket=settings.document_clean_bucket,
            key=key,
            payload=PDF_BYTES,
            content_type="application/pdf",
        )
    return identifiers


def _storage_keys_present(storage: FakeObjectStorage) -> list[tuple[str, str]]:
    return sorted(storage.objects)


def _count(db, model, identity: TestIdentity) -> int:
    """Count rows of one organization: the database persists between tests in this
    file, so an unscoped count would measure other tests' data as much as this one's."""
    return int(
        db.scalar(
            select(func.count(getattr(model, "id"))).where(
                model.organization_id == identity.organization_id
            )
        )
        or 0
    )


# --------------------------------------------------------------------------- #
# Retention: nothing invented, and what is declared is applied
# --------------------------------------------------------------------------- #


def test_nothing_is_purged_when_no_policy_is_declared(client):
    ident = authenticate_client(client, role_code="owner")
    _document_with_version(ident, age_days=3650)

    response = client.post("/api/v1/privacy/purge?dry_run=false")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policy_declared"] is False
    assert body["purged_count"] == 0, "une purge sans durée déclarée inventerait une durée"
    assert body["rows_deleted"] == 0
    assert any("Aucune politique de rétention n'est déclarée" in item["reason"] for item in body["not_enforced"])

    with SessionLocal() as db:
        assert _count(db, Document, ident) == 1


def test_a_declared_window_purges_only_what_is_past_it(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        _declare_policy(ident, documents=1, evidence=None)
        expired = _document_with_version(ident, age_days=800, storage=storage)
        recent = _document_with_version(ident, age_days=30, storage=storage)
        before = _storage_keys_present(storage)
        assert len(before) == 2, "les deux documents doivent être en stockage"

        response = client.post("/api/v1/privacy/purge?dry_run=false")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["purged_count"] == 1, body["decisions"]
        assert body["storage_objects_deleted"] == 1

        with SessionLocal() as db:
            remaining = db.scalars(
                select(Document.id).where(Document.organization_id == ident.organization_id)
            ).all()
        assert recent["document_id"] in remaining
        assert expired["document_id"] not in remaining
        assert len(_storage_keys_present(storage)) == 1, "l'objet du document purgé doit disparaître"
    finally:
        app.state.document_storage = old_storage


def test_dry_run_deletes_nothing(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        _declare_policy(ident, documents=1, evidence=None)
        _document_with_version(ident, age_days=800, storage=storage)

        response = client.post("/api/v1/privacy/purge")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is True
        assert body["purged_count"] == 1, "le plan doit voir le candidat"
        assert body["storage_objects_planned"] == 1, "le plan doit chiffrer ce qu'il ferait"
        assert body["storage_objects_deleted"] == 0, "un essai à blanc ne supprime rien"
        assert body["rows_deleted"] == 0

        assert len(_storage_keys_present(storage)) == 1
        with SessionLocal() as db:
            assert _count(db, Document, ident) == 1
    finally:
        app.state.document_storage = old_storage


def test_a_document_used_as_evidence_follows_the_longer_window(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        # Documents: 1 year. Evidence archive: 5 years. The document is one year and
        # one month old and carries an evidence record: it must survive, because
        # deleting it would destroy the basis of an analysis.
        _declare_policy(ident, documents=1, evidence=5)
        _document_with_version(ident, age_days=400, storage=storage, with_evidence=True)

        response = client.post("/api/v1/privacy/purge?dry_run=false")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["purged_count"] == 0, body["decisions"]
        assert any("preuve" in decision["reason"] for decision in body["decisions"] if decision["action"] == "keep")

        with SessionLocal() as db:
            assert _count(db, Document, ident) == 1
        assert len(_storage_keys_present(storage)) == 1
    finally:
        app.state.document_storage = old_storage


def test_a_document_that_feeds_an_analysis_is_never_purged_automatically(client):
    """A window may not destroy what an issued report was built from.

    `analysis_documents.document_version_id` is RESTRICT: deleting the link would
    silently change the input of an analysis that a client may already have reported
    on. Only an explicit erasure request may go there, and it records what it broke.
    """
    from app.models.domain import Analysis, AnalysisDocument, AnalysisStatus

    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        _declare_policy(ident, documents=1, evidence=None)
        document = _document_with_version(ident, age_days=800, storage=storage)
        with SessionLocal() as db:
            analysis = Analysis(
                organization_id=ident.organization_id,
                analysis_key=f"C20-{uuid4().hex[:8]}",
                status=AnalysisStatus.COMPLETED,
            )
            db.add(analysis)
            db.flush()
            db.add(
                AnalysisDocument(
                    organization_id=ident.organization_id,
                    analysis_id=analysis.id,
                    document_version_id=document["version_id"],
                )
            )
            db.commit()
            analysis_id = analysis.id

        response = client.post("/api/v1/privacy/purge?dry_run=false")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["purged_count"] == 0, body["decisions"]
        assert any(
            "analyse" in decision["reason"]
            for decision in body["decisions"]
            if decision["entity"] == "document"
        )
        assert any(
            item["item"] == "purge des documents rattachés à une analyse" for item in body["not_enforced"]
        )

        with SessionLocal() as db:
            assert _count(db, Document, ident) == 1, "le document d'entrée doit survivre"
            assert db.get(Analysis, analysis_id) is not None
            assert (
                db.scalar(
                    select(func.count(AnalysisDocument.id)).where(
                        AnalysisDocument.organization_id == ident.organization_id
                    )
                )
                == 1
            ), "le lien analyse ↔ document ne doit pas être supprimé par une purge"
        assert len(_storage_keys_present(storage)) == 1

        # An explicit erasure may go there, and says so.
        erased = client.post(
            "/api/v1/privacy/erasures",
            json={"scope": "document", "target_id": str(document["document_id"]), "reason": "demande RGPD"},
        )
        assert erased.status_code == 200, erased.text
        assert erased.json()["rows_deleted"]["analysis_documents"] == 1
    finally:
        app.state.document_storage = old_storage


# --------------------------------------------------------------------------- #
# Legal holds
# --------------------------------------------------------------------------- #


def _hold(identity: TestIdentity, *, days: int | None) -> UUID:
    with SessionLocal() as db:
        hold = LegalHold(
            organization_id=identity.organization_id,
            case_reference="DGCCRF-2026-089",
            reason="Enquête en cours",
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(days=days) if days is not None else None,
        )
        db.add(hold)
        db.commit()
        return hold.id


def test_a_legal_hold_stops_the_purge(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        _declare_policy(ident, documents=1, evidence=None)
        _document_with_version(ident, age_days=800, storage=storage)
        _hold(ident, days=None)

        response = client.post("/api/v1/privacy/purge?dry_run=false")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["purged_count"] == 0, body["decisions"]
        assert any("gel légal" in decision["reason"] for decision in body["decisions"])

        assert len(_storage_keys_present(storage)) == 1
        with SessionLocal() as db:
            assert _count(db, Document, ident) == 1
    finally:
        app.state.document_storage = old_storage


def test_a_legal_hold_refuses_an_on_demand_erasure(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        document = _document_with_version(ident, age_days=1, storage=storage)
        _hold(ident, days=None)

        response = client.post(
            "/api/v1/privacy/erasures",
            json={"scope": "document", "target_id": str(document["document_id"]), "reason": "demande du client"},
        )
        assert response.status_code == 409, response.text
        assert "DGCCRF-2026-089" in response.json()["detail"]["message"]

        assert len(_storage_keys_present(storage)) == 1, "aucun objet ne doit avoir été supprimé"
        with SessionLocal() as db:
            assert _count(db, DocumentVersion, ident) == 1
    finally:
        app.state.document_storage = old_storage


def test_an_expired_legal_hold_no_longer_blocks(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        document = _document_with_version(ident, age_days=1, storage=storage)
        _hold(ident, days=-1)

        response = client.post(
            "/api/v1/privacy/erasures",
            json={"scope": "document", "target_id": str(document["document_id"]), "reason": "demande du client"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["erased"] is True
    finally:
        app.state.document_storage = old_storage


# --------------------------------------------------------------------------- #
# Erasure: all of it, or none of it
# --------------------------------------------------------------------------- #


def test_erasure_removes_storage_objects_and_every_derived_row(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        document = _document_with_version(
            ident, age_days=1, storage=storage, with_segment=True, with_evidence=True
        )
        artefact_key = f"vericlaim/artifacts/{uuid4().hex}"
        with SessionLocal() as db:
            version = db.get(DocumentVersion, document["version_id"])
            assert version is not None
            version.extracted_text_storage_key = artefact_key
            db.commit()
        storage.put_bytes(
            bucket=settings.document_clean_bucket,
            key=artefact_key,
            payload=b"texte extrait",
            content_type="text/plain",
        )

        response = client.post(
            "/api/v1/privacy/erasures",
            json={"scope": "document", "target_id": str(document["document_id"]), "reason": "fin de contrat"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["erased"] is True
        assert body["storage_objects_deleted"] == 2, body
        assert body["rows_deleted"]["document_segments"] == 1
        assert body["rows_deleted"]["evidence"] == 1
        assert body["rows_deleted"]["documents"] == 1

        assert _storage_keys_present(storage) == []
        with SessionLocal() as db:
            assert _count(db, Document, ident) == 0
            assert _count(db, DocumentVersion, ident) == 0
            assert _count(db, DocumentSegment, ident) == 0
            assert _count(db, Evidence, ident) == 0
    finally:
        app.state.document_storage = old_storage


def test_the_audit_chain_still_verifies_after_an_erasure(client):
    """The trap of the plan: erasing content must not break the proof."""
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        document = _document_with_version(ident, age_days=1, storage=storage)
        with SessionLocal() as db:
            before_events = db.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.organization_id == ident.organization_id)
            )

        response = client.post(
            "/api/v1/privacy/erasures",
            json={"scope": "document", "target_id": str(document["document_id"]), "reason": "demande RGPD"},
        )
        assert response.status_code == 200, response.text

        with SessionLocal() as db:
            verification = verify_audit_chain(db, organization_id=ident.organization_id)
            after_events = db.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.organization_id == ident.organization_id)
            )
            tombstone = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == ident.organization_id,
                    AuditEvent.action == "document.erased",
                )
            )
        assert verification.is_valid is True, verification.error_detail
        assert after_events > before_events, "l'effacement doit laisser une trace"
        assert tombstone is not None
        assert tombstone.payload_json["manifest_sha256"] == response.json()["manifest_sha256"]
        assert tombstone.payload_json["audit_trail_kept"] is True
    finally:
        app.state.document_storage = old_storage


def test_a_failed_object_deletion_keeps_the_rows(client):
    """Fail closed: an unreachable store must not turn into an invisible orphan file."""
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        document = _document_with_version(ident, age_days=1, storage=storage)
        storage.fail_operations.add("delete")

        response = client.post(
            "/api/v1/privacy/erasures",
            json={"scope": "document", "target_id": str(document["document_id"]), "reason": "test"},
        )
        assert response.status_code == 500, response.text
        with SessionLocal() as db:
            assert _count(db, Document, ident) == 1, (
                "les lignes doivent survivre à un échec de suppression d'objet"
            )
            assert (
                db.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.organization_id == ident.organization_id,
                        AuditEvent.action == "document.erased",
                    )
                )
                == 0
            ), "aucune trace d'effacement ne doit être écrite pour un effacement qui n'a pas eu lieu"
    finally:
        app.state.document_storage = old_storage


def test_erasing_twice_says_so_and_deletes_nothing_more(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        document = _document_with_version(ident, age_days=1, storage=storage)
        payload = {"scope": "document", "target_id": str(document["document_id"]), "reason": "test"}
        assert client.post("/api/v1/privacy/erasures", json=payload).status_code == 200

        second = client.post("/api/v1/privacy/erasures", json=payload)
        assert second.status_code == 409, second.text
        assert "introuvable" in second.json()["detail"]["message"]
    finally:
        app.state.document_storage = old_storage


def test_an_erasure_never_reaches_another_organization(client):
    """Two organizations, two sessions: the attacker's own client must not be able to
    erase a document it does not own, and must not learn whether it exists."""
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        foreign = _document_with_version(ident, age_days=1, storage=storage)

        # A second, independent session belonging to another organization.
        with TestClient(app) as intruder:
            other = authenticate_client(intruder, role_code="owner")
            assert other.organization_id != ident.organization_id
            response = intruder.post(
                "/api/v1/privacy/erasures",
                json={"scope": "document", "target_id": str(foreign["document_id"]), "reason": "test"},
            )
        assert response.status_code == 409, response.text
        assert "introuvable dans cette organisation" in response.json()["detail"]["message"]

        with SessionLocal() as db:
            assert _count(db, Document, ident) == 1, "le document d'autrui doit survivre"
        assert len(_storage_keys_present(storage)) == 1
    finally:
        app.state.document_storage = old_storage


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def test_the_export_contains_rows_not_counts(client):
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        document = _document_with_version(ident, age_days=1, storage=storage, with_audit=True)
        response = client.get("/api/v1/privacy/export")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["counts"]["documents"] == 1
        assert body["counts"]["document_segments"] == 1
        assert len(body["document_segments"]) == 1
        assert body["document_segments"][0]["text"].startswith("Notre emballage")
        assert body["documents"][0]["id"] == str(document["document_id"])
        assert body["document_versions"][0]["storage_key"].startswith("vericlaim/")
        assert body["audit_events"], "l'export doit contenir la piste d'audit et ses empreintes"
        assert "event_hash" in body["audit_events"][0]
        assert body["export_metadata"]["laws"], "l'export doit dire ce qu'il ne contient pas"
    finally:
        app.state.document_storage = old_storage


def test_the_export_never_contains_another_organization(client):
    ident = authenticate_client(client, role_code="owner")
    with TestClient(app) as stranger:
        other = authenticate_client(stranger, role_code="owner")
        _document_with_version(other, age_days=1, title_marker="SECRET-AUTRE-SOCIETE")

    body = client.get("/api/v1/privacy/export").json()
    assert body["counts"]["documents"] == 0
    assert "SECRET-AUTRE-SOCIETE" not in str(body)
    assert str(other.organization_id) not in str(body)
    assert body["export_metadata"]["organization_id"] == str(ident.organization_id)


def _json_keys(node: object) -> list[str]:
    """Every key of a nested payload: a leak is searched by name, never by substring.

    Substring search would match the export's own text, which *talks about* passwords
    and tokens — the false positive that makes an automated leak check worthless.
    """
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            found.append(str(key))
            found.extend(_json_keys(value))
        return found
    if isinstance(node, list):
        found = []
        for item in node:
            found.extend(_json_keys(item))
        return found
    return []


def test_the_export_carries_the_members_the_holds_the_outbox_and_the_policy(client):
    """An export that announces "toutes les données détenues" must hold that word.

    The previous revision exported the compliance dossier while leaving out the
    members who can read the data, the legal holds that freeze it, the messages sent
    about it and the declared retention policy — with a scope sentence claiming the
    opposite. The assertion is on the scope claim *and* on the sections.
    """
    email = f"proprietaire.{uuid4().hex}@example.com"
    ident = authenticate_client(client, role_code="owner", email=email)
    _declare_policy(ident, documents=2, evidence=5, audit=1)

    hold = client.post(
        "/api/v1/enterprise/legal-holds",
        json={"case_reference": "DGCCRF-2026-089", "reason": "enquête en cours"},
    )
    assert hold.status_code == 201, hold.text

    with SessionLocal() as db:
        set_db_request_context(db, user_id=ident.user_id, organization_id=ident.organization_id)
        db.add(
            EmailMessage(
                organization_id=ident.organization_id,
                recipient_email="fournisseur@example.com",
                subject="Justificatif manquant",
                body_text="Bonjour,\n\nMerci de déposer la pièce : https://vericlaim.example/verify?token=JETON-VIVANT",
                purpose="evidence_request",
                status=EmailMessageStatus.NOT_CONFIGURED,
            )
        )
        db.commit()

    response = client.get("/api/v1/privacy/export")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["counts"]["members"] == 1
    member = body["members"][0]
    assert member["email"] == email, "l'export doit nommer les personnes qui peuvent lire les données"
    assert member["role_code"] == "owner"
    assert member["membership_status"] == "active"

    assert body["counts"]["legal_holds"] == 1
    assert body["counts"]["legal_holds_active"] == 1
    assert body["legal_holds"][0]["case_reference"] == "DGCCRF-2026-089"
    assert body["legal_holds"][0]["is_active"] is True

    assert body["counts"]["email_messages"] == 1
    assert body["email_messages"][0]["recipient_email"] == "fournisseur@example.com"
    assert body["email_messages"][0]["purpose"] == "evidence_request"

    assert body["retention_policy"]["documents_retention_years"] == 2
    assert body["retention_policy"]["evidence_archive_retention_years"] == 5
    assert body["retention_policy"]["configured_by_user_id"] == str(ident.user_id)
    assert body["organization"]["data_region"] == "eu"

    declared = " ".join(body["export_metadata"]["laws"])
    assert "membres" in declared or "Aucune ligne appartenant" in declared


def test_the_export_carries_no_credential_and_no_mail_body(client):
    """An export is handed to a customer: it must not become a set of live keys."""
    email = f"proprietaire.{uuid4().hex}@example.com"
    ident = authenticate_client(client, role_code="owner", email=email)
    with SessionLocal() as db:
        set_db_request_context(db, user_id=ident.user_id, organization_id=ident.organization_id)
        db.add(
            EmailMessage(
                organization_id=ident.organization_id,
                recipient_email="invite@example.com",
                subject="Votre accès",
                body_text="Activez votre compte : https://vericlaim.example/reset?token=JETON-SECRET-C20",
                purpose="password_reset",
                status=EmailMessageStatus.NOT_CONFIGURED,
            )
        )
        db.commit()

    body = client.get("/api/v1/privacy/export").json()
    serialized = str(body)

    assert "JETON-SECRET-C20" not in serialized, "un jeton à usage unique ne doit pas sortir dans un export"
    assert body["email_messages"], "l'enveloppe du message doit rester exportable"
    assert all(row["body_excluded"] is True for row in body["email_messages"])
    assert "body_text" not in _json_keys(body["email_messages"])

    suspect = [
        key
        for key in _json_keys(body)
        if any(marker in key.lower() for marker in ("password", "token", "secret", "api_key", "mfa"))
    ]
    assert suspect == [], f"clés sensibles exportées : {suspect}"

    declared = " ".join(body["export_metadata"]["laws"])
    assert "corps des e-mails est exclu" in declared, "l'omission doit être écrite, pas silencieuse"


def test_audit_rows_are_never_purged_and_the_reason_is_published(client):
    """The declared window on the audit trail is published, and never applied.

    Deleting an audit row would make `verify_audit_chain` fail from that point on, so
    the purge keeps them and says so. The test makes the purge delete something real
    first: otherwise it would pass on a purge that does nothing at all.
    """
    ident = authenticate_client(client, role_code="owner")
    storage, old_storage = _install_storage()
    try:
        _declare_policy(ident, documents=1, evidence=None, audit=1)
        with SessionLocal() as db:
            # A real event, written by the product's own function: a hand-built row
            # would carry hashes the chain cannot verify, and the test would then
            # measure its own fixture instead of the purge.
            set_db_request_context(db, user_id=ident.user_id, organization_id=ident.organization_id)
            event = append_audit_event(
                db,
                organization_id=ident.organization_id,
                actor_user_id=ident.user_id,
                entity_type="organization",
                entity_id=ident.organization_id,
                action="c20.witness_event",
                payload={"purpose": "témoin de la piste d'audit"},
            )
            db.commit()
            old_event_id = event.id
        _document_with_version(ident, age_days=800, storage=storage)

        response = client.post("/api/v1/privacy/purge?dry_run=false")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["purged_count"] == 1, "ce test ne prouve que si la purge supprime vraiment"
        assert any(
            item["item"] == "audit_trail_retention_years" for item in body["not_enforced"]
        ), "la durée déclarée pour la piste d'audit doit être publiée comme non appliquée"

        with SessionLocal() as db:
            assert db.get(AuditEvent, old_event_id) is not None, (
                "la purge ne doit jamais supprimer un événement d'audit : la chaîne cesserait de vérifier"
            )
            assert _count(db, Document, ident) == 0, "le document, lui, doit être parti"
            verification = verify_audit_chain(db, organization_id=ident.organization_id)
        assert verification.is_valid is True, verification.error_detail
    finally:
        app.state.document_storage = old_storage


# --------------------------------------------------------------------------- #
# Surface
# --------------------------------------------------------------------------- #


def test_a_viewer_cannot_purge_erase_or_export(client):
    authenticate_client(client, role_code="viewer")
    assert client.get("/api/v1/privacy/retention").status_code == 200
    assert client.post("/api/v1/privacy/purge").status_code == 403
    assert client.get("/api/v1/privacy/export").status_code == 403
    assert client.post(
        "/api/v1/privacy/erasures",
        json={"scope": "document", "target_id": str(uuid4()), "reason": "test"},
    ).status_code == 403


def test_an_anonymous_caller_is_rejected(client):
    assert client.get("/api/v1/privacy/export").status_code == 401
    assert client.post("/api/v1/privacy/purge").status_code == 401


def test_the_analyis_scope_is_refused_rather_than_faked(client):
    authenticate_client(client, role_code="owner")
    response = client.post(
        "/api/v1/privacy/erasures",
        json={"scope": "analysis", "target_id": str(uuid4()), "reason": "test"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "scope_not_implemented"


def test_the_retention_endpoint_separates_what_is_applied_from_what_is_not(client):
    ident = authenticate_client(client, role_code="owner")
    _declare_policy(ident, documents=3, evidence=5, audit=1)
    _hold(ident, days=10)

    body = client.get("/api/v1/privacy/retention").json()
    assert body["declared"]["documents_retention_years"] == 3
    assert body["declared"]["audit_trail_retention_years"] == 1
    assert [item["item"] for item in body["not_enforced"]] == ["audit_trail_retention_years"]
    assert body["active_legal_holds"][0]["case_reference"] == "DGCCRF-2026-089"
