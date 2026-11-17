"""A-13 : la durabilité d'une réponse ne dépend d'aucune discipline locale.

Cause première, mesurée sur cette pile (FastAPI 0.163.1 / Starlette 1.10.0) :
une dépendance ``yield`` est démontée **après** que la réponse a été envoyée.
L'ordre réel d'une requête était donc

    corps du routeur → réponse construite et envoyée → teardown (commit)

Un client recevait ``201`` avant que la transaction ne soit validée. Quand la
validation échouait — ou simplement quand la requête suivante arrivait avant
elle — la ligne annoncée n'existait pas. C'est la trace lue en CI :

    version ... does not exist at all (no row even for the admin role):
    the upload was never committed

Puis, en local sous un seul cœur, la même cause produisait les symptômes
« Aucune organisation active » (409), « Produit introuvable » (404) et
« authentification requise » (401) sur des créations qui venaient de réussir.

La correction n'est pas une suite de ``commit`` dispersés : c'est
``Depends(get_db, scope="function")``, qui rétablit l'ordre

    corps du routeur → commit (teardown) → réponse envoyée

Ce module vérifie **l'invariant**, pas un endpoint : il échoue si une seule
route de l'application déclare la session sans ``scope="function"``, et il
mesure l'ordre effectif sur le parcours d'upload de A-13.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from app.core.database import SessionLocal, get_db
from app.models.domain import DocumentUpload, DocumentUploadStatus, DocumentVersion
from fastapi.routing import APIRoute
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient
from tests.auth_support import authenticate_client
from tests.document_lifecycle_support import (
    PDF_BYTES,
    create_document_and_upload_intent,
    install_document_fakes,
)


def _db_dependencies(route: APIRoute) -> list[tuple[str, object]]:
    """Retourne (nom du paramètre, dépendance) pour la session de chaque route."""

    found: list[tuple[str, object]] = []
    dependant = route.dependant
    for sub in dependant.dependencies:
        if sub.call is get_db:
            found.append((sub.name or "db", sub))
        for nested in sub.dependencies:
            if nested.call is get_db:
                found.append((nested.name or "db", nested))
    return found


def _iter_api_routes():
    """Parcourt toutes les routes, y compris celles des routers inclus.

    FastAPI 0.163 conserve les inclusions sous forme d'enveloppes
    (``_IncludedRouter``) plutôt qu'en aplatissant ``app.routes`` : ignorer cette
    structure ferait passer ce garde-fou pour un test vide, ce qui est pire que
    pas de test du tout.
    """

    from app.main import app

    def walk(routes, prefix: str = ""):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            else:
                original = getattr(route, "original_router", None)
                if original is not None:
                    yield from walk(original.routes)
                elif hasattr(route, "routes"):
                    yield from walk(route.routes)

    yield from walk(app.routes)


def test_every_route_keeps_the_session_commit_before_the_response() -> None:
    """Aucune route ne doit dépendre de la session hors du scope « function ».

    Ce test est le garde-fou du défaut : il ne décrit pas un endpoint, il décrit
    la règle. Une route ajoutée demain avec ``Depends(get_db)`` sans scope échoue
    ici, au lieu de produire un 201 non durable en recette.
    """

    offenders: list[str] = []
    observed = 0
    for route in _iter_api_routes():
        for parameter_name, dependency in _db_dependencies(route):
            observed += 1
            if getattr(dependency, "scope", "request") != "function":
                offenders.append(f"{sorted(route.methods)} {route.path} (paramètre {parameter_name})")

    assert observed > 0, "aucune dépendance de session détectée : l'inspection est cassée"
    assert not offenders, (
        "ces routes déclarent la session sans scope='function' : leur commit "
        "s'exécute après l'envoi de la réponse, donc un 2xx peut précéder la "
        "validation de la transaction:\n" + "\n".join(offenders)
    )


class _OrderProbe(BaseHTTPMiddleware):
    """Observe ce que la base contient au moment exact où la réponse est prête."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if PROBE["armed"] and request.url.path.endswith("/complete") and response.status_code == 201:
            with SessionLocal() as observer:
                PROBE["recorder"]["version_visible"] = observer.scalar(
                    select(DocumentVersion.id).order_by(DocumentVersion.created_at.desc()).limit(1)
                )
        return response


PROBE: dict = {"armed": False, "recorder": {}}

# Enregistré à l'import : une application démarrée refuse tout nouveau middleware,
# et aucun test ne doit dépendre de l'ordre d'exécution de ses voisins.
_probe_app = __import__("app.main", fromlist=["app"]).app
_probe_app.add_middleware(_OrderProbe)
_probe_app.middleware_stack = _probe_app.build_middleware_stack()


def test_201_is_only_sent_once_the_version_is_durable() -> None:
    """À l'instant du 201, la version est déjà validée et lisible ailleurs.

    Sans ``scope="function"``, la ligne n'est pas encore validée quand la réponse
    est prête : le corps du test le dit explicitement plutôt que d'attendre un
    échec lointain dans une autre requête.
    """

    app, storage, _scanner, old_storage, old_scanner = install_document_fakes()
    PROBE["recorder"] = {}
    PROBE["armed"] = True
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Durabilité")
            storage.upload_latest(payload=PDF_BYTES)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")

        assert completion.status_code == 201, completion.text
        announced = UUID(completion.json()["version"]["id"])
        visible = PROBE["recorder"].get("version_visible")
        assert visible is not None, (
            "le 201 est parti alors qu'aucune version n'était visible depuis une autre "
            "connexion : la réponse a précédé la validation de la transaction"
        )
        assert visible == announced, "la réponse annonce une version que la base n'expose pas"

        with SessionLocal() as db:
            upload = db.get(DocumentUpload, UUID(instruction["upload"]["id"]))
            assert upload is not None
            assert upload.status == DocumentUploadStatus.CLEAN
            assert upload.finalized_document_version_id == announced
    finally:
        PROBE["armed"] = False
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_failed_commit_never_returns_201(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une validation qui échoue produit une erreur, jamais un succès.

    C'est la garantie directement opposable à la trace CI : un 201 ne doit
    jamais accompagner une transaction annulée.
    """

    from sqlalchemy.orm import Session as ORMSession

    app, storage, _scanner, old_storage, old_scanner = install_document_fakes()
    original_commit = ORMSession.commit
    calls = {"count": 0}

    def commit_then_fail(self):
        calls["count"] += 1
        original_commit(self)
        raise RuntimeError("validation interrompue après écriture (injection contrôlée)")

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            authenticate_client(client, role_code="analyst")
            _document, instruction = create_document_and_upload_intent(client, title="Échec injecté")
            storage.upload_latest(payload=PDF_BYTES)

            monkeypatch.setattr(ORMSession, "commit", commit_then_fail)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            monkeypatch.undo()

            assert calls["count"] >= 1, "la validation a été tentée pendant la requête"
            assert completion.status_code != 201, (
                "un 201 a été rendu alors que la validation avait échoué: " f"{completion.text}"
            )
            assert completion.status_code >= 500
    finally:
        monkeypatch.undo()
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def test_next_request_sees_the_version_announced_by_the_201() -> None:
    """Ce que le 201 annonce est ce que la requête suivante retrouve.

    C'est le parcours utilisateur de A-13 : l'import suivant relit l'historique
    du document et doit y trouver la version annoncée.
    """

    app, storage, _scanner, old_storage, old_scanner = install_document_fakes()
    try:
        with TestClient(app) as client:
            authenticate_client(client, role_code="analyst")
            document, instruction = create_document_and_upload_intent(client, title="Relecture")
            storage.upload_latest(payload=PDF_BYTES)
            completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completion.status_code == 201, completion.text
            announced = completion.json()["version"]["id"]

            detail = client.get(f"/api/v1/documents/{document['id']}")
            assert detail.status_code == 200, detail.text
            assert [str(version["id"]) for version in detail.json()["versions"]] == [announced]

            _document2, instruction2 = create_document_and_upload_intent(client, title="Second import")
            storage.upload_latest(payload=PDF_BYTES)
            completion2 = client.post(f"/api/v1/document-uploads/{instruction2['upload']['id']}/complete")
            assert completion2.status_code == 201, completion2.text
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner
