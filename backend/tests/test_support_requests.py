"""C15 — support : l'aide décrit le produit qui tourne, les délais sont mesurables.

Ce que ces tests protègent, et qui ne se voit pas dans une capture d'écran :

1. **Aucune erreur inventée.** Chaque entrée de ``FREQUENT_ERRORS`` cite un
   ``error_code`` et le fichier de route qui le produit. Le code est vérifié dans ce
   fichier : une aide qui explique une erreur disparue envoie l'utilisateur chercher un
   message qu'il ne verra jamais.
2. **Aucun délai inventé.** L'objectif de première réponse vient du **plan réel** de
   l'organisation, il est figé sur la demande, et un plan inconnu fait **échouer** la
   création plutôt que produire un délai au hasard.
3. **Aucune disponibilité promise.** Le centre d'aide est publié avec sa phrase
   d'exclusion ; le contenu ne doit contenir aucun chiffre de disponibilité.
4. **La demande porte son contexte.** L'écran et le dernier code d'erreur envoyés par le
   client sont enregistrés tels quels : c'est ce qui permet de reproduire un incident.
5. **Une réponse sans texte est refusée**, et chaque transition écrit dans la chaîne
   d'audit.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.billing.plans import PLANS
from app.core.database import SessionLocal, create_tables
from app.models.domain import SupportRequestStatus
from app.support import help as support_help
from app.support import service as support
from app.support.service import SupportError
from app.main import app
from tests.auth_support import authenticate_client

BACKEND = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category": "extraction",
        "subject": "Un segment OCR reste marqué à revoir",
        "message": (
            "Le document a été téléversé ce matin et l'analyse signale un segment reconnu "
            "optiquement. Que faut-il vérifier avant de publier le rapport ?"
        ),
        "screen": "analyse",
        "last_error_code": "report_not_ready",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# 1. L'aide ne cite que des erreurs qui existent
# --------------------------------------------------------------------------- #


def test_every_help_entry_cites_an_error_the_code_really_produces():
    """Le contrôle qui empêche l'aide de pourrir : le code cité est dans le fichier cité."""

    failures: list[str] = []
    for entry in support_help.FREQUENT_ERRORS:
        source = BACKEND / entry["api_path"]
        assert source.exists(), f"fichier cité introuvable : {entry['api_path']}"
        text = source.read_text(encoding="utf-8")
        if entry["error_code"] not in text:
            failures.append(f"{entry['error_code']} absent de {entry['api_path']}")
    assert failures == [], (
        "aide qui décrit une erreur que le produit ne produit plus : " + "; ".join(failures)
    )


def test_the_published_help_is_the_source_of_the_page(client: TestClient):
    response = client.get("/api/v1/public/support")
    assert response.status_code == 200, response.text
    published = response.json()
    assert len(published["frequent_errors"]) == len(support_help.FREQUENT_ERRORS)
    assert published["help_url"] == "/aide"
    assert published["contact"]["email"] is None, (
        "sans adresse configurée, l'API publie null : elle n'invente pas de boîte aux lettres"
    )


def test_no_availability_figure_is_published(client: TestClient):
    """Un centre d'aide ne peut pas promettre une disponibilité qui n'est pas mesurée."""

    import re

    published = client.get("/api/v1/public/support").json()
    blob = str(published)
    # Le mensonge à interdire est un **chiffre** de disponibilité, pas le mot : la page
    # doit pouvoir écrire « aucune disponibilité n'est garantie » pour dire le contraire.
    figures = [
        match.group(0)
        for pattern in (r"\d{1,3}[,.]\d+\s*%", r"SLA\s+de\s+\d", r"\b99[,. ]9\b")
        for match in re.finditer(pattern, blob, flags=re.IGNORECASE)
    ]
    assert figures == [], f"chiffre de disponibilité publié sans mesure : {figures}"
    assert "non contractuels" in published["sla"]["statement"]
    assert len(published["sla"]["exclusions"]) == 3
    assert "n'est ni mesurée ni garantie" in published["sla"]["statement"]


# --------------------------------------------------------------------------- #
# 2. Le délai vient du plan réel
# --------------------------------------------------------------------------- #


def test_every_sold_plan_has_a_published_target():
    rows = support_help.published_sla()
    assert [row["plan_code"] for row in rows] == [plan.code for plan in PLANS]
    for row in rows:
        assert row["first_response_hours"] > 0
        assert row["incident_response_hours"] <= row["first_response_hours"], (
            "un incident ne peut pas être traité plus lentement qu'une question"
        )


def test_an_unknown_plan_does_not_get_a_target_at_random(client: TestClient):
    identity = authenticate_client(client, role_code="owner")
    with SessionLocal() as db:
        from app.identity.service import set_db_request_context

        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        from app.models.domain import SupportRequestCategory

        with pytest.raises(SupportError) as raised:
            support.create_request(
                db,
                organization_id=identity.organization_id,
                category=SupportRequestCategory.QUESTION,
                subject="Question sans plan",
                message="Une organisation sans offre ne doit pas obtenir de délai inventé.",
                requester_email="personne@example.org",
                plan_code="plan-qui-n-existe-pas",
            )
        db.rollback()
    assert "plan-qui-n-existe-pas" in str(raised.value)


def test_the_response_target_is_computed_and_frozen_on_the_request(client: TestClient):
    authenticate_client(client, role_code="owner")
    response = client.post("/api/v1/support/requests", json=_payload(category="incident"))
    assert response.status_code == 201, response.text
    created = response.json()
    # L'essai par défaut est l'offre Pro : l'objectif incident publié est de 8 h.
    assert created["plan_code"] == "pro"
    assert created["first_response_hours"] == support_help.first_response_hours("pro", "incident")
    created_at = datetime.fromisoformat(created["created_at"])
    due_at = datetime.fromisoformat(created["first_response_due_at"])
    # L'écart est calculé à la seconde de dépôt, pas à la seconde de réponse : la
    # tolérance porte sur l'ordre de grandeur, pas sur une valeur exacte.
    delta = due_at - created_at
    assert timedelta(hours=created["first_response_hours"]) - timedelta(seconds=5) <= delta
    assert delta <= timedelta(hours=created["first_response_hours"]) + timedelta(seconds=5)
    assert created["status"] == "open" and created["overdue"] is False


def test_the_request_keeps_the_screen_and_the_last_error(client: TestClient):
    """Le contexte reçu est celui envoyé : c'est ce qui rend un incident reproductible."""

    authenticate_client(client, role_code="owner")
    created = client.post(
        "/api/v1/support/requests",
        json=_payload(screen="rapport", last_error_code="report_object_missing"),
    ).json()
    assert created["screen"] == "rapport"
    assert created["last_error_code"] == "report_object_missing"


# --------------------------------------------------------------------------- #
# 3. Répondre : texte obligatoire, traçabilité
# --------------------------------------------------------------------------- #


def test_an_answer_without_text_is_refused(client: TestClient):
    # Une seule authentification : `authenticate_client` crée un compte (et donc une
    # organisation) à chaque appel, et une demande n'appartient qu'à une organisation.
    identity = authenticate_client(client, role_code="owner")
    created = client.post("/api/v1/support/requests", json=_payload()).json()

    refused = client.post(
        f"/api/v1/support/requests/{created['id']}/answer", json={"resolution": "court"}
    )
    assert refused.status_code == 422, refused.text

    # La garde du service, indépendamment du schéma
    with SessionLocal() as db:
        from app.identity.service import set_db_request_context
        from uuid import UUID

        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        with pytest.raises(SupportError):
            support.answer_request(
                db,
                organization_id=identity.organization_id,
                request_id=UUID(created["id"]),
                resolution="  ",
                actor_user_id=identity.user_id,
            )
        db.rollback()

    answered = client.post(
        f"/api/v1/support/requests/{created['id']}/answer",
        json={
            "resolution": (
                "Le segment optique doit être relu avant publication : utilisez la revue "
                "humaine pour le corriger, le rapport n'utilise que la version validée."
            )
        },
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "closed"
    assert answered.json()["resolution"].startswith("Le segment optique")


def test_answering_twice_is_refused(client: TestClient):
    authenticate_client(client, role_code="owner")
    created = client.post("/api/v1/support/requests", json=_payload()).json()
    first = client.post(
        f"/api/v1/support/requests/{created['id']}/answer",
        json={"resolution": "Première réponse écrite, suffisamment longue."},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/v1/support/requests/{created['id']}/answer",
        json={"resolution": "Deuxième réponse, qui ne doit pas passer."},
    )
    assert second.status_code == 400, second.text


def test_the_request_lifecycle_is_traced(client: TestClient):
    from sqlalchemy import select

    from app.audit.service import verify_audit_chain
    from app.models.domain import AuditEvent

    identity = authenticate_client(client, role_code="owner")
    created = client.post("/api/v1/support/requests", json=_payload()).json()
    client.post(
        f"/api/v1/support/requests/{created['id']}/answer",
        json={"resolution": "Réponse écrite et traçée dans la chaîne d'audit."},
    )
    with SessionLocal() as db:
        from app.identity.service import set_db_request_context

        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        actions = [
            row[0]
            for row in db.execute(
                select(AuditEvent.action)
                .where(
                    AuditEvent.organization_id == identity.organization_id,
                    AuditEvent.entity_id == __import__("uuid").UUID(created["id"]),
                )
                .order_by(AuditEvent.occurred_at.asc())
            ).all()
        ]
        assert actions == ["support.request_received", "support.request_closed"], actions
        assert verify_audit_chain(db, organization_id=identity.organization_id).is_valid is True


# --------------------------------------------------------------------------- #
# 4. Frontière de locataire, permissions, jeton CSRF
# --------------------------------------------------------------------------- #


def test_a_support_request_never_crosses_the_tenant_boundary(client: TestClient):
    owner = authenticate_client(client, role_code="owner")
    created = client.post("/api/v1/support/requests", json=_payload()).json()

    other = authenticate_client(
        client, role_code="owner", email=f"support.autre.{uuid4().hex[:10]}@example.org"
    )
    assert other.organization_id != owner.organization_id
    assert client.get(f"/api/v1/support/requests/{created['id']}").status_code == 404
    assert client.get("/api/v1/support/requests").json()["total"] == 0


def test_deposing_a_request_needs_management_rights_and_a_csrf_token(client: TestClient):
    authenticate_client(client, role_code="analyst")
    refused = client.post("/api/v1/support/requests", json=_payload())
    assert refused.status_code == 403, refused.text

    authenticate_client(client, role_code="owner")
    csrf = client.headers.get("X-CSRF-Token")
    del client.headers["X-CSRF-Token"]
    without_csrf = client.post("/api/v1/support/requests", json=_payload())
    assert without_csrf.status_code == 403, without_csrf.text

    client.headers["X-CSRF-Token"] = csrf
    assert client.post("/api/v1/support/requests", json=_payload()).status_code == 201


def test_the_listing_publishes_late_requests(client: TestClient):
    identity = authenticate_client(client, role_code="owner")
    created = client.post("/api/v1/support/requests", json=_payload()).json()

    # On recule l'échéance en base : le retard doit être publié, pas calculé par le lecteur.
    with SessionLocal() as db:
        from app.identity.service import set_db_request_context
        from uuid import UUID

        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        from app.models.domain import SupportRequest

        row = db.get(SupportRequest, UUID(created["id"]))
        # On recule la réception **et** l'échéance : la base interdit une échéance
        # antérieure à la réception, et cette contrainte est ce qui empêche un délai
        # négatif d'exister.
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=30)
        row.first_response_due_at = datetime.now(timezone.utc) - timedelta(hours=3)
        db.commit()

    listing = client.get("/api/v1/support/requests").json()
    assert listing["overdue"] >= 1
    late = [item for item in listing["requests"] if item["id"] == created["id"]][0]
    assert late["overdue"] is True and late["hours_remaining"] < 0
