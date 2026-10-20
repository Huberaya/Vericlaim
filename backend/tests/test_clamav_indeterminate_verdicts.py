"""Lot P0-C : une verdict antivirus indéterminé ne devient jamais un verdict sain.

Le client clamd de ce dépôt rejette déjà les trames ambiguës (test_clamav_protocol.py).
Ce fichier ferme les trous restants, mesurés sur cette base:

- erreur de connexion réseau (port fermé) - pas « propre par défaut » ;
- réponse tronquée non vide sans terminateur NUL ;
- verdict « FOUND » sans nom de signature, et verdict plausible mais inconnu ;
- aucune fuite du nom de signature dans l'erreur exposée à l'appelant ;
- hôte ClamAV non configuré : la fabrique refuse, elle n'assemble pas un client boiteux ;
- délai nul : échec, pas succès ;
- et surtout le contrat d'état: un verdict indéterminé ne publie rien, ne libère rien,
  n'émet aucun événement `document.upload_clean`, et laisse une reprise possible.

Aucun de ces tests ne prétends valider un vrai moteur ClamAV: les pairs sont des serveurs
TCP locaux qui jouent le protocole, exactement comme le fichier de tests voisin.
"""

from __future__ import annotations

import socket
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.scanner import (
    ClamAvMalwareScanner,
    MalwareScanError,
    MalwareScannerUnavailableError,
    build_malware_scanner,
)
from app.identity.service import set_db_request_context
from app.models.domain import AuditEvent, Document, DocumentStatus, DocumentUpload, DocumentUploadStatus
from tests.auth_support import authenticate_client
from tests.document_lifecycle_support import (
    PDF_BYTES,
    create_document_and_upload_intent,
    install_document_fakes,
)
from tests.test_clamav_protocol import protocol_peer


def _scan_with(response: bytes) -> tuple[bool, BaseException | None]:
    """Retourne (a_tenu_la_connexion, exception) pour une réponse clamd donnée."""

    with protocol_peer(response) as (scanner, _):
        try:
            scanner.scan(b"untrusted bytes")
            return True, None
        except Exception as exc:  # noqa: BLE001 - le test inspecte le type
            return False, exc


def test_connection_refused_is_unavailable_not_clean() -> None:
    # Un port fermé localement: l'erreur réseau doit rester une indisponibilité.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    # Le contexte est fermé: le port est libre, la connexion est refusée.
    scanner = ClamAvMalwareScanner(host="127.0.0.1", port=port, timeout_seconds=1)
    with pytest.raises(MalwareScannerUnavailableError):
        scanner.scan(b"untrusted bytes")


@pytest.mark.parametrize(
    "response",
    [
        b"stream: O",  # tronqué, puis EOF, sans terminateur
        b"stream:  FOUND\x00",  # « FOUND » sans nom de signature
        b"stream: Clean\x00",  # plausible, mais aucun verdict clamd
        b"stream: OK\x00\x00",  # double terminateur
        b"\x00",  # trame vide
        b"stream: OK\x00et deuxieme ligne",  # données après la trame
    ],
)
def test_malformed_clamd_frames_are_never_clean(response: bytes) -> None:
    held, exc = _scan_with(response)
    assert held is False
    assert isinstance(exc, MalwareScanError), exc


def test_signature_name_is_not_leaked_to_callers() -> None:
    with protocol_peer(b"stream: Eicar-Signature-Confidentielle FOUND\x00") as (scanner, _):
        result = scanner.scan(b"payload")
    assert result.is_clean is False
    assert result.engine == "clamav"
    assert "Eicar" not in repr(result)


def test_zero_timeout_fails_closed_instead_of_trusting_the_peer() -> None:
    # Un service d'écoute qui n'accepte personne: la liaison aboutit dans la file du
    # noyau, mais aucun verdict n'arrive. Un délai nul ne doit surtout pas se lire
    # comme un « OK » par défaut.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        scanner = ClamAvMalwareScanner(host="127.0.0.1", port=port, timeout_seconds=0)
        with pytest.raises(MalwareScannerUnavailableError):
            scanner.scan(b"untrusted bytes")
    # Réglage explicite du délai nul sur un client déjà construit: même refus.
    closed = ClamAvMalwareScanner(host="127.0.0.1", port=9, timeout_seconds=1)
    closed._timeout_seconds = 0  # noqa: SLF001 - configuration pathologique volontaire
    with pytest.raises(MalwareScannerUnavailableError):
        closed.scan(b"untrusted bytes")


def test_scanner_mode_clamav_without_host_is_refused_by_the_factory() -> None:
    broken = replace(settings, document_scanner_mode="clamav", document_clamav_host=None)
    with pytest.raises(MalwareScannerUnavailableError):
        build_malware_scanner(broken)
    zero_wait = replace(settings, document_scanner_mode="clamav", document_clamav_host="av.local", document_clamav_timeout_seconds=0)
    with pytest.raises(MalwareScannerUnavailableError):
        build_malware_scanner(zero_wait)


def test_test_mode_scanner_is_refused_outside_the_test_environment() -> None:
    misconfigured = replace(settings, environment="staging", document_scanner_mode="test")
    with pytest.raises(MalwareScannerUnavailableError):
        build_malware_scanner(misconfigured)


from app.documents.scanner import MalwareScanResult


class ExplodingScanner:
    """Un scanner dont le verdict n'est pas crédible: ni sain, ni infecté."""

    def __init__(self) -> None:
        self.calls = 0

    def scan(self, payload: bytes):
        del payload
        self.calls += 1
        raise MalwareScanError("Le service antivirus a retourné une réponse invalide.")


def _upload_row(upload_id: str) -> DocumentUpload:
    with SessionLocal() as db:
        return db.get(DocumentUpload, uuid.UUID(upload_id))


@pytest.mark.parametrize("error", [MalwareScanError("verdict invalide"), MalwareScannerUnavailableError("antivirus indisponible")])
def test_indeterminate_verdict_publishes_nothing_and_purges_nothing(error: Exception) -> None:
    app, storage, _scanner, old_storage, old_scanner = install_document_fakes()
    exploding = ExplodingScanner()
    app.state.document_scanner = exploding
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document, instruction = create_document_and_upload_intent(client, title="Verdict douteux")
            storage.upload_latest(payload=PDF_BYTES)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completion.status_code == 503, completion.text
            body = completion.json()
            assert body["version"] is None
            assert body["upload"]["status"] == DocumentUploadStatus.FAILED.value
            assert body["upload"]["scan_error_code"] == ("scanner_error" if isinstance(error, MalwareScanError) else "scanner_unavailable")
            assert not storage.copy_requests, "une copie propre ne peut pas être demandée sur un verdict douteux"

            row = _upload_row(instruction["upload"]["id"])
            assert row.purge_required is False, (
                "les octets restent en quarantaine: un verdict douteux ne doit ni publier ni détruire"
            )
            assert storage.prefix_deletions == []
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                assert db.get(Document, uuid.UUID(document["id"])).status == DocumentStatus.QUARANTINED
                events = list(
                    db.scalars(
                        select(AuditEvent.action).where(
                            AuditEvent.entity_type == "document_upload",
                            AuditEvent.entity_id == row.id,
                        )
                    ).all()
                )
                assert set(events) == {"document.upload_requested", "document.upload_scan_failed"}, events
                assert "document.upload_clean" not in events, (
                    "aucun événement de promotion ne doit exister aux côtés de l'échec de contrôle"
                )
                assert "document.upload_purge_released" not in events

            # Reprise après rétablissement du service: une seule version, pas de doublon.
            app.state.document_scanner = _scanner
            retry = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert retry.status_code == 201, retry.text
            assert retry.json()["version"] is not None
            assert exploding.calls == 1
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_malware_verdict_schedules_the_release_only_after_the_rejection_commit() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()

    class Infected:
        def scan(self, payload: bytes):
            del payload
            return MalwareScanResult(is_clean=False, engine="fake-clamav")

    app.state.document_scanner = Infected()
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Infecté")
            storage.upload_latest(payload=PDF_BYTES)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completion.status_code == 422, completion.text
            assert completion.json()["upload"]["scan_error_code"] == "malware_detected"
            row = _upload_row(instruction["upload"]["id"])
            assert row.status == DocumentUploadStatus.REJECTED
            assert row.purge_required is True and row.purge_reason == "rejected"
            assert row.purge_attempts == 0
            key = row.quarantine_storage_key
            calls = [entry[1] for entry in storage.prefix_deletions]
            assert calls == [f"quarantine/{row.organization_id}/{row.document_id}/{row.id}/"]
            assert (storage.upload_requests[0][0], key) not in storage.objects
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_audit_timestamp_of_a_failed_control_is_not_a_release_proof() -> None:
    """L'horodatage de scan ne doit jamais faire croire à une libération."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    exploding = ExplodingScanner()
    app.state.document_scanner = exploding
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Preuve d'absence")
            storage.upload_latest(payload=PDF_BYTES)
            client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            row = _upload_row(instruction["upload"]["id"])
            completed = row.scan_completed_at
            assert completed is not None
            # SQLite rend un horodatage naïf, PostgreSQL un horodatage absorbant le fuseau:
            # la comparaison est faite dans le repère le plus restrictif des deux.
            if completed.tzinfo is not None:
                completed = completed.replace(tzinfo=None)
            assert completed <= datetime.now(timezone.utc).replace(tzinfo=None)
            assert row.uploaded_sha256 is not None, "l'objet est bien identifié, même non publié"
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                event = db.scalar(
                    select(AuditEvent).where(
                        AuditEvent.entity_type == "document_upload",
                        AuditEvent.entity_id == row.id,
                        AuditEvent.action == "document.upload_scan_failed",
                    )
                )
                assert event is not None
                assert "quarantine" not in str(event.payload_json), (
                    "un échec de contrôle n'a rien à dire sur la libération de l'objet"
                )
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner
