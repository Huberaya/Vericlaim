"""C12 — la procédure d'exercice des droits est opérationnelle, pas déclarative.

Le plan l'écrit noir sur blanc : « Procédure d'exercice des droits (accès, portabilité,
effacement) **opérationnelle**, pas seulement déclarative. » Une page qui décrit une
procédure ne la met pas en œuvre. Ce que ces tests exigent :

* une demande reçue est **datée**, et son échéance est **calculée** (un mois, art. 12.3),
  pas saisie — sinon le délai est négociable, donc il n'existe pas ;
* l'échéance tient compte des fins de mois (31 janvier + un mois = 28 février) ;
* une demande close « accordée » sans **référence de ce qui a été remis** est refusée ;
* une prolongation exige un motif écrit et ne s'applique qu'une fois ;
* un retard est **visible** dans la réponse de liste, sans qu'il faille le calculer ;
* la frontière de locataire tient, et une demande ne se modifie pas sans la permission
  d'administration ni sans jeton CSRF ;
* ce que le produit publie comme outillé est **vrai** : chaque route citée existe, et
  les deux parcours outillés (export, effacement) produisent réellement la référence
  qui permet de clore la demande.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.core.database import SessionLocal, create_tables
from app.main import app
from app.privacy import rights
from tests.auth_support import authenticate_client


@pytest.fixture()
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


def _create(client: TestClient, **overrides) -> dict:
    body = {
        "request_type": "access",
        "requester_email": f"personne.{uuid4().hex[:10]}@example.org",
        "requester_name": "Camille Martin",
        "requester_is_member": False,
        "details": "Je demande une copie des données me concernant dans votre outil.",
    }
    body.update(overrides)
    response = client.post("/api/v1/data-rights", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 1. Le délai est calculé, jamais saisi
# --------------------------------------------------------------------------- #


def test_a_received_request_gets_a_computed_deadline(client: TestClient):
    authenticate_client(client, role_code="owner")
    created = _create(client)

    received = datetime.fromisoformat(created["received_at"].replace("Z", "+00:00"))
    due = datetime.fromisoformat(created["due_at"].replace("Z", "+00:00"))
    assert due == rights.add_months(received, 1), (
        "l'échéance doit être calculée à un mois de la réception, jamais fournie par le client"
    )
    assert created["status"] == "received"
    assert created["article"] == "RGPD art. 15"
    assert created["overdue"] is False


def test_the_deadline_follows_the_calendar_and_not_a_thirty_day_count():
    """31 janvier + un mois = fin février, pas le 2 ou 3 mars."""

    january = datetime(2026, 1, 31, 9, 30, tzinfo=timezone.utc)
    assert rights.add_months(january, 1) == datetime(2026, 2, 28, 9, 30, tzinfo=timezone.utc)
    august = datetime(2026, 8, 31, 9, 30, tzinfo=timezone.utc)
    assert rights.add_months(august, 1) == datetime(2026, 9, 30, 9, 30, tzinfo=timezone.utc)
    december = datetime(2026, 12, 15, 9, 30, tzinfo=timezone.utc)
    assert rights.add_months(december, 1) == datetime(2027, 1, 15, 9, 30, tzinfo=timezone.utc)
    # Le cas bissextile, qui est celui qui casse les calculs naïfs.
    assert rights.add_months(datetime(2028, 1, 31, tzinfo=timezone.utc), 1) == datetime(
        2028, 2, 29, tzinfo=timezone.utc
    )


def test_a_request_already_late_when_recorded_keeps_its_real_receipt_date(client: TestClient):
    """Une demande reçue par courrier il y a deux mois est en retard dès l'enregistrement."""

    authenticate_client(client, role_code="owner")
    late = (datetime.now(timezone.utc) - timedelta(days=62)).isoformat()
    created = _create(client, received_at=late)
    assert created["overdue"] is True
    assert created["days_remaining"] < 0
    listing = client.get("/api/v1/data-rights").json()
    assert listing["overdue"] == 1, "le retard doit être publié, pas laissé au lecteur"


def test_an_unreadable_receipt_date_is_refused(client: TestClient):
    authenticate_client(client, role_code="owner")
    response = client.post(
        "/api/v1/data-rights",
        json={
            "request_type": "access",
            "requester_email": "personne@example.org",
            "details": "Demande de copie de mes données personnelles.",
            "received_at": "le 3 du mois dernier",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_received_at"


def test_a_request_without_text_is_refused_twice_over(client: TestClient):
    """Le schéma refuse (422) et le service refuse aussi (400) : deux chemins, deux gardes.

    Le schéma protège l'API ; le service protège tout autre appelant (une reprise de
    données, un script d'import). Une demande sans texte ne peut pas être répondue de
    façon justifiable, donc elle ne doit pas exister en base.
    """

    identity = authenticate_client(client, role_code="owner")
    refused = client.post(
        "/api/v1/data-rights",
        json={
            "request_type": "erasure",
            "requester_email": "personne@example.org",
            "details": "court",
        },
    )
    assert refused.status_code == 422, refused.text

    from app.privacy.rights import RightsError
    from app.models.domain import DataSubjectRight

    with SessionLocal() as db:
        from app.identity.service import set_db_request_context

        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        with pytest.raises(RightsError):
            rights.record_request(
                db,
                organization_id=identity.organization_id,
                request_type=DataSubjectRight.ERASURE,
                requester_email="personne@example.org",
                details="court",
            )
        db.rollback()
    assert client.get("/api/v1/data-rights").json()["total"] == 0


# --------------------------------------------------------------------------- #
# 2. Un accord sans preuve est refusé
# --------------------------------------------------------------------------- #


def test_a_grant_without_a_delivery_reference_is_refused(client: TestClient):
    authenticate_client(client, role_code="owner")
    created = _create(client)
    response = client.post(
        f"/api/v1/data-rights/{created['id']}/close",
        json={"outcome": "granted", "outcome_detail": "export transmis"},
    )
    assert response.status_code == 400, response.text
    assert "référence" in response.json()["detail"]["message"]


def test_a_refusal_without_a_reason_is_refused(client: TestClient):
    authenticate_client(client, role_code="owner")
    created = _create(client, request_type="objection")
    response = client.post(
        f"/api/v1/data-rights/{created['id']}/close",
        json={"outcome": "refused"},
    )
    assert response.status_code == 400, response.text
    assert "motif" in response.json()["detail"]["message"]


def test_a_closed_request_keeps_what_was_actually_delivered(client: TestClient):
    authenticate_client(client, role_code="owner")
    created = _create(client)
    closed = client.post(
        f"/api/v1/data-rights/{created['id']}/close",
        json={
            "outcome": "granted",
            "outcome_reference": "export_manifest_9f2c41",
            "outcome_detail": "Export complet transmis au demandeur.",
        },
    )
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["status"] == "completed"
    assert body["outcome"] == "granted"
    assert body["outcome_reference"] == "export_manifest_9f2c41"
    assert body["completed_at"] is not None

    again = client.post(
        f"/api/v1/data-rights/{created['id']}/close",
        json={"outcome": "granted", "outcome_reference": "autre_reference"},
    )
    assert again.status_code == 400, "une demande close ne se reclôt pas"


# --------------------------------------------------------------------------- #
# 3. Le délai peut être prolongé, une fois, et motiVé
# --------------------------------------------------------------------------- #


def test_an_extension_requires_a_reason_and_happens_only_once(client: TestClient):
    authenticate_client(client, role_code="owner")
    created = _create(client)

    refused = client.post(
        f"/api/v1/data-rights/{created['id']}/extend",
        json={"reason": "court"},
    )
    assert refused.status_code == 422, refused.text  # le schéma exige un motif lisible

    extended = client.post(
        f"/api/v1/data-rights/{created['id']}/extend",
        json={
            "reason": (
                "Volume de données important et demandes complexes de la même personne : "
                "le délai de deux mois prévu à l'article 12.3 est appliqué."
            )
        },
    )
    assert extended.status_code == 200, extended.text
    body = extended.json()
    initial = datetime.fromisoformat(created["due_at"].replace("Z", "+00:00"))
    assert datetime.fromisoformat(body["extension_due_at"].replace("Z", "+00:00")) == rights.add_months(
        initial, 2
    )
    assert body["status"] == "in_progress"
    assert body["extension_reason"]

    twice = client.post(
        f"/api/v1/data-rights/{created['id']}/extend",
        json={"reason": "Encore deux mois, pour la même raison écrite ici."},
    )
    assert twice.status_code == 400, twice.text


def test_an_extension_is_never_a_silent_way_to_bury_a_late_request(client: TestClient):
    """Prolonger ne remet pas le compteur à zéro : la date d'origine reste publiée."""

    authenticate_client(client, role_code="owner")
    created = _create(client)
    client.post(
        f"/api/v1/data-rights/{created['id']}/extend",
        json={"reason": "Complexité documentée : plusieurs sources doivent être recoupées."},
    )
    detail = client.get(f"/api/v1/data-rights/{created['id']}").json()
    assert detail["due_at"] == created["due_at"], "l'échéance d'origine reste celle qui a couru"
    assert detail["effective_due_at"] != created["due_at"]
    # La demande, enregistrée à l'instant, n'est pas en retard : la prolongation est
    # régulière. Mais l'indicateur de retard d'origine existe et serait vrai pour une
    # demande reçue il y a plus d'un mois (voir le test suivant).
    assert detail["overdue"] is False
    assert detail["initial_deadline_passed"] is False


def test_a_late_request_stays_visibly_late_even_after_an_extension(client: TestClient):
    """Prolonger ne blanchit pas un retard : les deux indicateurs coexistent.

    Une demande reçue il y a deux mois est en retard. Après une prolongation régulière,
    la demande redevient « dans les délais » pour le second délai — mais le premier mois
    a bien été dépassé, et cela doit rester lisible. Sinon la prolongation serait un
    moyen d'effacer un retard, ce que l'article 12.3 n'autorise pas.
    """

    authenticate_client(client, role_code="owner")
    late = (datetime.now(timezone.utc) - timedelta(days=62)).isoformat()
    created = _create(client, received_at=late)
    assert created["overdue"] is True and created["initial_deadline_passed"] is True

    client.post(
        f"/api/v1/data-rights/{created['id']}/extend",
        json={"reason": "Complexité établie : plusieurs sources doivent être recoupées."},
    )
    after = client.get(f"/api/v1/data-rights/{created['id']}").json()
    assert after["overdue"] is False, "le délai prolongé n'est pas dépassé"
    assert after["initial_deadline_passed"] is True, "le premier mois a bien été dépassé"
    listing = client.get("/api/v1/data-rights").json()
    assert listing["initial_deadline_missed"] == 1, "le retard d'origine reste compté"
    assert listing["overdue"] == 0


# --------------------------------------------------------------------------- #
# 4. La frontière de locataire, et qui peut agir
# --------------------------------------------------------------------------- #


def test_a_request_never_crosses_the_tenant_boundary(client: TestClient):
    owner = authenticate_client(client, role_code="owner")
    created = _create(client)

    other = authenticate_client(
        client, role_code="owner", email=f"autre.proprietaire.{uuid4().hex[:10]}@example.org"
    )
    assert other.organization_id != owner.organization_id
    assert client.get(f"/api/v1/data-rights/{created['id']}").status_code == 404
    assert (
        client.post(
            f"/api/v1/data-rights/{created['id']}/close",
            json={"outcome": "refused", "refusal_reason": "Refusée par la mauvaise organisation."},
        ).status_code
        == 404
    )
    assert client.get("/api/v1/data-rights").json()["total"] == 0


def test_recording_a_request_needs_management_rights_and_a_csrf_token(client: TestClient):
    authenticate_client(client, role_code="analyst")
    response = client.post(
        "/api/v1/data-rights",
        json={
            "request_type": "access",
            "requester_email": "personne@example.org",
            "details": "Demande de copie de mes données personnelles.",
        },
    )
    assert response.status_code == 403, response.text

    identity = authenticate_client(client, role_code="owner")
    csrf = client.headers.get("X-CSRF-Token")
    del client.headers["X-CSRF-Token"]
    without_csrf = client.post(
        "/api/v1/data-rights",
        json={
            "request_type": "access",
            "requester_email": "personne@example.org",
            "details": "Demande de copie de mes données personnelles.",
        },
    )
    assert without_csrf.status_code == 403, without_csrf.text
    client.headers["X-CSRF-Token"] = csrf
    assert identity.organization_id


# --------------------------------------------------------------------------- #
# 5. Ce qui est publié comme outillé doit l'être vraiment
# --------------------------------------------------------------------------- #


def _registered_paths() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        original = getattr(route, "original_router", None)
        for candidate in (route,) + tuple(getattr(original, "routes", []) if original else ()):
            path = getattr(candidate, "path", None)
            if not path:
                continue
            for method in getattr(candidate, "methods", ()) or ():
                found.add((method, path))
    return found


def test_the_published_procedure_does_not_promise_a_route_that_does_not_exist(client: TestClient):
    """Chaque route annoncée comme outillée doit exister, sinon la page légale ment."""

    authenticate_client(client, role_code="owner")
    procedure = client.get("/api/v1/data-rights/procedure").json()

    assert len(procedure["handling"]) == 6
    assert procedure["response_window_months"] == 1
    registered = _registered_paths()
    automated = [row for row in procedure["handling"] if row["automated"]]
    assert automated, "au moins l'accès, la portabilité et l'effacement sont outillés"
    for row in automated:
        route = row["route"]
        assert route, f"{row['request_type']} est annoncé outillé sans route"
        method, _, path = route.partition(" ")
        assert (method, path) in registered, f"route annoncée inexistante : {route}"
    for row in procedure["handling"]:
        assert row["note"].strip(), "un droit sans explication est un droit non publié"
    unhandled = [row["request_type"] for row in procedure["handling"] if not row["automated"]]
    assert set(unhandled) == {"rectification", "restriction", "objection"}, (
        "les droits non outillés doivent être publiés comme tels, pas passés sous silence"
    )


def test_the_export_really_produces_the_reference_that_closes_an_access_request(client: TestClient):
    """Le parcours complet : demande d'accès → export → clôture avec l'empreinte réelle."""

    authenticate_client(client, role_code="owner")
    created = _create(client, request_type="access")
    export = client.get("/api/v1/privacy/export")
    assert export.status_code == 200, export.text
    payload = export.json()
    reference = payload["export_metadata"]["manifest_sha256"]
    assert reference and len(reference) == 64, (
        "l'export doit porter l'empreinte de ce qui a été remis, sinon la demande d'accès "
        "ne peut pas être close avec une preuve"
    )

    closed = client.post(
        f"/api/v1/data-rights/{created['id']}/close",
        json={
            "outcome": "granted",
            "outcome_reference": reference,
            "outcome_detail": "Export complet transmis au demandeur.",
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["outcome_reference"] == reference


def test_the_erasure_manifest_can_close_an_erasure_request(client: TestClient):
    """L'effacement outillé (C20) fournit la référence du manifeste qui clôt la demande."""

    from tests.document_support import FakeObjectStorage
    from tests.test_privacy_lifecycle import _document_with_version, _install_storage

    identity = authenticate_client(client, role_code="owner")
    storage, previous = _install_storage()
    try:
        identifiers = _document_with_version(identity, storage=storage)
        document_id = identifiers["document_id"]
        request = _create(client, request_type="erasure", details="Supprimez les données me concernant.")
        erased = client.post(
            "/api/v1/privacy/erasures",
            json={
                "scope": "document",
                "target_id": str(document_id),
                "reason": "Demande d'effacement de la personne concernée.",
            },
        )
        assert erased.status_code == 200, erased.text
        manifest = erased.json()["manifest_sha256"]
        assert manifest
        closed = client.post(
            f"/api/v1/data-rights/{request['id']}/close",
            json={
                "outcome": "granted",
                "outcome_reference": manifest,
                "outcome_detail": "Contenu effacé ; le journal d'audit conserve la trace.",
            },
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["outcome_reference"] == manifest
    finally:
        app.state.document_storage = previous


def test_the_requests_are_traced_in_the_audit_chain(client: TestClient):
    """Recevoir, prolonger et clore laissent trois traces vérifiables."""

    from app.audit.service import verify_audit_chain

    identity = authenticate_client(client, role_code="owner")
    created = _create(client)
    client.post(
        f"/api/v1/data-rights/{created['id']}/extend",
        json={"reason": "Volume important : le délai supplémentaire est nécessaire."},
    )
    client.post(
        f"/api/v1/data-rights/{created['id']}/close",
        json={"outcome": "refused", "refusal_reason": "Le demandeur n'est pas la personne concernée."},
    )

    with SessionLocal() as db:
        from app.identity.service import set_db_request_context
        from sqlalchemy import select
        from app.models.domain import AuditEvent

        set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
        events = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.organization_id == identity.organization_id,
                AuditEvent.entity_type == "data_subject_request",
            )
            .order_by(AuditEvent.occurred_at.asc())
        ).all()
        actions = [event.action for event in events]
        assert actions == [
            "privacy.rights_request_received",
            "privacy.rights_request_extended",
            "privacy.rights_request_closed",
        ], actions
        chain = verify_audit_chain(db, organization_id=identity.organization_id)
        assert chain.is_valid is True


# --------------------------------------------------------------------------- #
# 6. La promesse « aucun transfert de document à un tiers IA » est surveillée
# --------------------------------------------------------------------------- #


def test_no_inference_dependency_is_installed():
    """La promesse faite aux clients est vérifiable, donc elle doit rester vraie.

    Elle est écrite dans `docs/legal/sous-traitants-et-transferts.md` §3 et destinée au
    DPA : « aucun transfert de document à un tiers IA ». Un document, un texte extrait
    ou un verdict ne quittent l'instance que si une dépendance le permet. Ce test
    échoue le jour où l'une d'elles entre dans le projet — c'est-à-dire le jour où la
    clause contractuelle deviendrait fausse.
    """

    import pathlib as _pathlib

    requirements = (_pathlib.Path(__file__).resolve().parents[1] / "requirements.txt").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "openai",
        "anthropic",
        "google-generativeai",
        "google.generativeai",
        "cohere",
        "mistralai",
        "transformers",
        "torch",
        "langchain",
        "llama",
        "ollama",
        "huggingface",
    )
    lowered = requirements.lower()
    found = [name for name in forbidden if name in lowered]
    assert found == [], (
        "une dépendance d'inférence est présente : la clause « aucun transfert de "
        f"document à un tiers IA » doit être retirée ou re-vérifiée ({found})"
    )


def test_the_document_pipeline_makes_no_outbound_call():
    """Ni OCR distant, ni service de détection : le pipeline lit et écrit localement.

    Seuls deux clients sortants existent dans le projet, et aucun ne reçoit de document :
    le stockage objet du client (`boto3`) et le prestataire de paiement (facturation).
    """

    import pathlib as _pathlib
    import re

    root = _pathlib.Path(__file__).resolve().parents[1] / "app"
    pipeline = (
        root / "engine",
        root / "analyses",
        root / "documents",
        root / "reports",
        root / "privacy",
    )
    pattern = re.compile(r"\b(requests\.|httpx\.|aiohttp\.|urllib\.request)")
    offenders: list[str] = []
    for directory in pipeline:
        for path in directory.rglob("*.py"):
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(root)}:{line_no}: {line.strip()[:80]}")
    assert offenders == [], f"appel réseau sortant dans le pipeline documentaire : {offenders}"


def test_the_public_procedure_page_reads_the_same_source_as_the_product(client: TestClient):
    """La page légale publique et l'application ne peuvent pas se contredire.

    La page publique lit `GET /api/v1/public/rights-procedure`, sans session : si la
    carte publiée n'était disponible que pour un compte connecté, la page serait
    forcément recopiée à la main, et dériverait.
    """

    public = client.get("/api/v1/public/rights-procedure")
    assert public.status_code == 200, public.text
    payload = public.json()
    assert len(payload["handling"]) == 6
    assert payload["contact_email"] is None, (
        "sans adresse configurée, l'API publie null : elle n'invente pas de contact"
    )

    identity = authenticate_client(client, role_code="owner")
    private = client.get("/api/v1/data-rights/procedure")
    assert private.status_code == 200, private.text
    assert private.json()["handling"] == payload["handling"], (
        "les deux vues doivent décrire le même produit"
    )
    assert identity.organization_id


def test_the_export_reference_can_be_recomputed_by_whoever_receives_it(client: TestClient):
    """L'empreinte remise au demandeur doit être vérifiable **par lui**.

    Une référence que seul le producteur peut recalculer n'est pas une preuve : c'est
    une affirmation. Au premier essai, celle-ci ne tenait pas — l'empreinte était
    calculée sur le dictionnaire Python, où `str(datetime)` vaut
    « 2026-09-30 19:15:55+00:00 » alors que le JSON reçu porte
    « 2026-09-30T19:15:55+00:00 » : deux empreintes différentes pour le même contenu.
    Ce test recalcule le calcul de l'extérieur, avec la même canonicalisation écrite
    ici, sans appeler le code du service.
    """

    import hashlib
    import json
    import re

    authenticate_client(client, role_code="owner")
    response = client.get("/api/v1/privacy/export")
    assert response.status_code == 200, response.text
    export = response.json()
    published = export["export_metadata"]["manifest_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", published), published

    received = json.loads(json.dumps(export))
    received["export_metadata"]["manifest_sha256"] = ""
    recomputed = hashlib.sha256(
        json.dumps(
            received, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert recomputed == published, (
        "l'empreinte publiée ne correspond pas au contenu reçu : la référence opposable "
        f"remise au demandeur n'est pas vérifiable (publiée {published}, recalculée {recomputed})"
    )
