"""Lot P0-A : la borne d'import vient du serveur, jamais du client.

Trois défauts étaient réunis sur ce contrat :

1. l'instruction d'import renvoyait ``max_size_bytes = body.size_bytes``, c'est-à-dire
   la taille que le client venait de déclarer : toute vérification anticipée côté
   navigateur était une tautologie, et un intégrateur tiers lisant ce champ comme le
   plafond du service lisait en fait son propre chiffre ;
2. rien ne publiait la borne avant l'appel d'intention, si bien que le frontend ne
   pouvait la connaître qu'après avoir déclaré un fichier voué au refus ;
3. le garde d'authentification de développement lisait le singleton global de
   configuration, alors que le reste de l'application peut servir une autre
   ``app.state.settings`` : le garde et les contrats publiés pouvaient diverger.

Les tests ci-dessous épinglent le serveur comme source unique : la borne déclarée est
recalculée par le serveur, le plafond réellement appliqué borne la politique du POST,
un objet plus volumineux que la déclaration ne publie jamais de version, et la
configuration servie entraîne tous les lecteurs à la fois.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.api.v1.auth import dev_login_allowed
from app.core.database import SessionLocal
from app.identity.service import set_db_request_context
from app.models.domain import AuditEvent, DocumentUpload, DocumentUploadStatus
from tests.auth_support import authenticate_client
from tests.document_lifecycle_support import (
    PDF_BYTES,
    create_document_and_upload_intent,
    install_document_fakes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERRIDE_MAX_BYTES = 256


def config_get_settings_production_like() -> None:
    """Rejoue la validation d'ouverture avec APP_ENV=production et le flag autorisé."""
    import os

    from app.core import config

    saved = {k: os.environ.get(k) for k in ("APP_ENV", "AUTH_DEV_LOGIN_ENABLED")}
    os.environ["APP_ENV"] = "production"
    os.environ["AUTH_DEV_LOGIN_ENABLED"] = "true"
    try:
        config.get_settings()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _serve_override(client: TestClient, **updates: object) -> None:
    """Installe une configuration de test sur l'application, comme le ferait un hôte."""

    client.app.state.settings = replace(settings, **updates)


def _restore(app: object) -> None:
    app.state.settings = settings


def test_instruction_and_status_publish_the_server_bound_not_the_declaration() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            status_body = client.get("/api/v1/auth/status").json()
            assert status_body["document_upload_max_bytes"] == settings.max_upload_bytes
            _document, instruction = create_document_and_upload_intent(client, title="Borne serveur")
            # La déclaration du client (len(PDF_BYTES)) ne doit jamais devenir la limite.
            assert instruction["max_size_bytes"] == settings.max_upload_bytes
            assert instruction["max_size_bytes"] != len(PDF_BYTES)
            assert instruction["upload"]["declared_size_bytes"] == len(PDF_BYTES)
            assert status_body["document_upload_max_bytes"] == instruction["max_size_bytes"]
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner
        _restore(app)


def test_oversized_declaration_is_refused_before_any_upload_policy_is_minted() -> None:
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            _serve_override(client, max_upload_bytes=OVERRIDE_MAX_BYTES)
            document, _instruction = create_document_and_upload_intent(client, title="Trop gros")
            before = len(storage.upload_requests)
            response = client.post(
                f"/api/v1/documents/{document['id']}/versions",
                json={
                    "source_filename": "gros.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": OVERRIDE_MAX_BYTES + 1,
                },
            )
            assert response.status_code == 422, response.text
            assert len(storage.upload_requests) == before, (
                "aucune politique d'upload ne doit être signée pour une déclaration hors borne"
            )
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner
        _restore(app)


def test_presigned_policy_ceiling_stays_under_the_server_bound() -> None:
    """La politique est bornée à la déclaration du client — et ne peut donc pas dépasser le plafond serveur."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            _serve_override(client, max_upload_bytes=OVERRIDE_MAX_BYTES)
            _document, instruction = create_document_and_upload_intent(client, title="Politique bornée")
            del instruction
            bucket, key, content_type, policy_max, expires_in = storage.upload_requests[-1]
            assert policy_max <= OVERRIDE_MAX_BYTES, "jamais un plafond supérieur à la limite du service"
            assert policy_max == len(PDF_BYTES), "exactement ce que le client a annoncé de déposer"
            assert bucket and key and content_type and expires_in > 0
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner
        _restore(app)


def test_undersized_declaration_with_a_larger_object_never_publishes_a_version() -> None:
    """Un client qui contourne le contrôle navigateur : le serveur décide, pas la déclaration."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            _serve_override(client, max_upload_bytes=OVERRIDE_MAX_BYTES)
            document, instruction = create_document_and_upload_intent(client, title="Déclaration mensongère")
            bucket, key = storage.upload_latest(payload=b"%PDF-1.7\n" + b"x" * (OVERRIDE_MAX_BYTES * 4))
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            # 422: la complétion a refusé le corps, pas la requête elle-même.
            assert completion.status_code == 422, completion.text
            body = completion.json()
            assert body["document"]["status"] == "failed", (
                "un import refusé ne doit pas laisser la pièce en attente d'un contrôle"
            )
            assert body["version"] is None, "un objet plus volumineux que la déclaration n'est jamais publié"
            assert body["upload"]["status"] == DocumentUploadStatus.REJECTED.value
            assert (bucket, key) not in storage.objects, "l'objet refusé doit sortir de la quarantaine"
            with SessionLocal() as db:
                set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
                upload = db.get(DocumentUpload, uuid.UUID(instruction["upload"]["id"]))
                assert upload is not None and upload.scan_error_code == "size_mismatch"
                actions = list(
                    db.scalars(
                        select(AuditEvent.action).where(
                            AuditEvent.entity_type == "document_upload",
                            AuditEvent.entity_id == upload.id,
                            AuditEvent.action == "document.upload_rejected",
                        )
                    ).all()
                )
                assert actions == ["document.upload_rejected"]
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner
        _restore(app)


def test_dev_login_stays_closed_unless_the_served_settings_allow_it() -> None:
    """Faillir fermé, et lire la configuration réellement servie — pas un singleton oublié."""
    app, storage, scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            # Par défaut (AUTH_DEV_LOGIN_ENABLED absent) : ni la porte, ni son annonce.
            assert client.get("/api/v1/auth/status").json()["dev_login_enabled"] is False
            refused = client.post("/api/v1/auth/dev-login", json={})
            assert refused.status_code == 404, "la porte ne doit même pas être annoncée"

            # Une installation qui autorise explicitement doit être vue par le garde
            # comme par le statut : les deux lisent désormais la même configuration.
            _serve_override(client, auth_dev_login_enabled=True)
            assert client.get("/api/v1/auth/status").json()["dev_login_enabled"] is True
            granted = client.post("/api/v1/auth/dev-login", json={})
            assert granted.status_code == 200, granted.text
        # Défense en profondeur : une configuration déjà assemblée qui prétend autoriser
        # le contournement ne doit pas obtenir l'autorisation dans un environnement
        # de production-like — le garde tranche, il ne se contente pas de relayer le flag.
        assert dev_login_allowed(replace(settings, environment="production", auth_dev_login_enabled=True)) is False
        assert dev_login_allowed(replace(settings, environment="staging", auth_dev_login_enabled=True)) is False
        # Et le chargeur refuse à l'ouverture la même combinaison.
        with pytest.raises(RuntimeError):
            config_get_settings_production_like()
    finally:
        app.state.document_storage, app.state.document_scanner = old_storage, old_scanner


def test_settings_of_follows_the_host_application() -> None:
    from app.core.config import settings_of

    class _NoState:
        pass

    class _State:
        settings = "installée"

    class _App:
        state = _State()

    assert settings_of(_NoState()) is settings
    assert settings_of(_App()) == "installée"


def test_frontend_consumes_the_published_bound() -> None:
    """Cohérence du contrat côté client : la borne publiée est lue, la constante ne sert plus de limite.

    Vérification statique, volontairement dans la suite backend : le dépôt n'a pas de
    runner de tests JavaScript, et cette parution de contrat ne doit pas dépendre d'un
    `npm test` inexistant.
    """
    types = REPO_ROOT / "frontend/src/lib/types.ts"
    api = REPO_ROOT / "frontend/src/lib/api.ts"
    auth = REPO_ROOT / "frontend/src/lib/auth.ts"
    if not types.exists():
        pytest.skip("frontend/src absent de ce checkout : parité de contrat non vérifiable ici")
    assert "document_upload_max_bytes" in types.read_text(encoding="utf-8")

    api_text = api.read_text(encoding="utf-8")
    assert "export function documentImportMaxBytes" in api_text
    assert "export function noteServerDocumentUploadBound" in api_text
    assert "serverDocumentUploadMaxBytes ?? DOCUMENT_IMPORT_MAX_BYTES" in api_text

    auth_text = auth.read_text(encoding="utf-8")
    assert "noteServerDocumentUploadBound(status.document_upload_max_bytes)" in auth_text

    for component in ("DocumentUploader.tsx", "SecureDocumentVault.tsx"):
        text = (REPO_ROOT / "frontend/src/components" / component).read_text(encoding="utf-8")
        assert "documentImportMaxBytes()" in text, f"{component} doit suivre la borne publiée"
        assert "> DOCUMENT_IMPORT_MAX_BYTES" not in text, (
            f"{component} ne doit plus refuser sur une constante compilée"
        )
