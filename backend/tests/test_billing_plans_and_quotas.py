"""C13 — plans, quotas, abonnement et facturation : ce que le produit applique.

Deux exigences gouvernent cette suite :

1. **Aucun quota n'est déclaré appliqué sans une mesure qui le montre.** Les tests
   consomment réellement des documents par l'API, font tourner le vrai worker
   d'extraction pour compter les pages OCR, invitent de vrais membres et
   déclarent une vraie politique de conservation.
2. **Aucun chemin ne doit pouvoir contourner le contrôle.** La garde
   d'abonnement est vérifiée sur l'application réellement montée, route par
   route : c'est la leçon du chantier C24, où un contrôle écrit mais jamais
   appelé passait tous les tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.billing import plans as catalogue
from app.billing.http import (
    EXEMPT_WRITE_ROUTES,
    SENSITIVE_WRITE_PREFIXES,
    route_has_billing_gate,
)
from app.billing.providers import LocalProvider, StripeProvider, sign_payload
from app.billing.usage import add_months
from app.core.config import Settings, settings
from app.core.database import SessionLocal
from app.main import app
from app.models.domain import (
    BillingProviderEvent,
    BillingSubscription,
    BillingSubscriptionStatus,
    BillingUsageEvent,
    Document,
    Organization,
)
from tests.auth_support import authenticate_client

REQUEST = Client = TestClient
TEXT_BYTES = "Première allégation environnementale.\n\nDeuxième passage vérifiable.".encode()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
# Une URL PostgreSQL factice : la configuration de production refusa le SQLite
# avant même d'examiner le prestataire de paiement, et le contrôle du prestataire
# est ce qui est testé ici. Aucune connexion n'est ouverte par ``get_settings``.
POSTGRES_PLACEHOLDER_URL = "postgresql+psycopg://vericlaim_app:mot-de-passe@127.0.0.1:5432/vericlaim"


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------


def _subscribe(client: TestClient, *, plan_code: str = "starter") -> dict:
    """Le cycle de souscription, par l'API, sans intervention manuelle."""

    checkout = client.post("/api/v1/billing/checkout", json={"plan_code": plan_code})
    assert checkout.status_code == 201, checkout.text
    session_id = checkout.json()["session_id"]
    paid = client.post(f"/api/v1/billing/local/checkout/{session_id}/pay", json={})
    assert paid.status_code == 200, paid.text
    return paid.json()


def _create_document(client: TestClient, *, title: str = "Déclaration fournisseur") -> dict:
    response = client.post(
        "/api/v1/documents",
        json={"title": title, "document_type": "supplier_declaration", "tags": ["test"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _invite(client: TestClient, *, email: str, role_code: str = "analyst"):
    return client.post(
        "/api/v1/organizations/current/members/invitations",
        json={"email": email, "role_code": role_code},
    )


def _sign(provider: LocalProvider, *, event_type: str, payload: dict):
    return provider.envelope(event_type=event_type, payload=payload)


def _post_webhook(client: TestClient, body: bytes, headers: dict[str, str]):
    return client.post(
        "/api/v1/billing/webhook/local",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )


def _set_subscription_status(organization_id: UUID, status: BillingSubscriptionStatus) -> None:
    with SessionLocal() as db:
        subscription = db.scalar(
            select(BillingSubscription).where(BillingSubscription.organization_id == organization_id)
        )
        assert subscription is not None
        subscription.status = status
        db.commit()


def _expire_period(organization_id: UUID, *, days_ago: int = 1) -> None:
    """Amène l'abonnement à son échéance, comme le ferait le calendrier.

    Le renouvellement d'un vrai prestataire arrive *à* la date d'échéance : reculer
    la période sans reculer la date d'effet d'une descente programmée testerait un
    instant qui n'arrive jamais.
    """

    with SessionLocal() as db:
        subscription = db.scalar(
            select(BillingSubscription).where(BillingSubscription.organization_id == organization_id)
        )
        assert subscription is not None
        end = datetime.now(timezone.utc) - timedelta(days=days_ago)
        subscription.current_period_start = end - timedelta(days=30)
        subscription.current_period_end = end
        if subscription.pending_plan_effective_at is not None:
            subscription.pending_plan_effective_at = end
        db.commit()


def _backdate_document(document_id: str, *, days: int) -> None:
    with SessionLocal() as db:
        document = db.get(Document, UUID(document_id))
        assert document is not None
        document.created_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()


def _iter_api_routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        router = getattr(route, "original_router", None)
        if router is not None:
            yield from _iter_api_routes(router.routes)


# ---------------------------------------------------------------------------
# Le catalogue : ce qui est publié est ce qui est appliqué
# ---------------------------------------------------------------------------


def test_the_catalogue_is_public_and_says_when_its_prices_are_not_validated():
    with TestClient(app) as client:
        response = client.get("/api/v1/billing/plans")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["catalogue_version"] == catalogue.CATALOGUE_VERSION
    assert body["pricing_confirmed"] is False
    assert "provisoires" in body["pricing_status"]
    assert body["overage_policy"] == "blocked_no_charge"
    assert [plan["code"] for plan in body["plans"]] == ["starter", "pro", "enterprise"]
    starter = body["plans"][0]
    assert starter["quotas"] == {
        "documents_per_month": 30,
        "ocr_pages_per_month": 300,
        "seats": 2,
        "retention_months": 12,
    }
    assert starter["entitlements"] == []
    assert body["plans"][2]["price_cents_per_month_excl_vat"] is None
    assert body["plans"][2]["limits_are_reference_values"] is True
    # Le catalogue est celui du code, pas une copie : les valeurs publiées sont
    # exactement celles que l'application des quotas utilise.
    for published, plan in zip(body["plans"], catalogue.PLANS):
        assert published["quotas"]["documents_per_month"] == plan.quotas.documents_per_month
        assert published["quotas"]["ocr_pages_per_month"] == plan.quotas.ocr_pages_per_month
        assert published["quotas"]["seats"] == plan.quotas.seats
        assert published["quotas"]["retention_months"] == plan.quotas.retention_months


def test_every_advertised_entitlement_is_enforced_by_a_route_that_exists():
    """Une grille tarifaire n'annonce que des droits réellement appliqués."""

    paths = {route.path for route in _iter_api_routes(app.routes)}
    for entitlement in catalogue.ENTITLEMENTS:
        marker = entitlement.enforced_by.split(" ", 1)[0]
        assert marker in paths, (
            f"le droit « {entitlement.code} » est annoncé comme appliqué par {marker!r}, "
            "qui n'est pas une route de l'application"
        )


def test_the_header_says_which_provider_collects_the_money_and_whether_it_can():
    body = _catalogue_body()
    assert body["provider"] == settings.billing_provider
    assert body["collects_money"] is settings.billing_collects_money
    if settings.billing_provider == "local":
        # Le prestataire de développement n'encaisse pas : la page publique doit
        # le dire au lieu d'afficher un bouton de paiement qui ne paie rien.
        assert body["collects_money"] is False
        assert any("encaissement" in note for note in body["notes"])


def _catalogue_body() -> dict:
    with TestClient(app) as client:
        response = client.get("/api/v1/billing/plans")
        assert response.status_code == 200
        return response.json()


# ---------------------------------------------------------------------------
# Essai dérivé : pas de ligne, pas de remise à zéro possible
# ---------------------------------------------------------------------------


def test_a_new_organization_starts_an_derived_trial_without_any_row_in_the_database():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        body = client.get("/api/v1/billing/subscription").json()

    assert body["plan_code"] == catalogue.DEFAULT_TRIAL_PLAN_CODE
    assert body["status"] == "trialing"
    assert body["granted"] is True
    assert body["trial_derived_from_organization_creation"] is True
    assert body["provider"] is None
    with SessionLocal() as db:
        rows = db.scalar(
            select(func.count())
            .select_from(BillingSubscription)
            .where(BillingSubscription.organization_id == identity.organization_id)
        )
        organization = db.get(Organization, identity.organization_id)
    assert rows == 0, "un essai ne doit pas exister comme ligne : sinon un appel d'API peut le relancer"
    assert organization is not None
    expected_end = organization.created_at + timedelta(days=catalogue.TRIAL_DAYS)
    assert body["trial_ends_at"] is not None
    assert datetime.fromisoformat(body["trial_ends_at"]).date() == expected_end.date()


def test_an_expired_trial_blocks_production_and_leaves_reading_open():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        existing = _create_document(client, title="Document de la période d'essai")
        with SessionLocal() as db:
            organization = db.get(Organization, identity.organization_id)
            assert organization is not None
            organization.created_at = datetime.now(timezone.utc) - timedelta(
                days=catalogue.TRIAL_DAYS + 5
            )
            db.commit()

        refused = client.post(
            "/api/v1/documents",
            json={"title": "Import après la fin de l'essai", "document_type": "other"},
        )
        assert refused.status_code == 402, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "subscription_inactive"
        assert detail["status"] == "trial_expired"
        assert "Essai" in detail["reason"] and "souscrivez" in detail["reason"]

        # Ce qui existe déjà reste consultable : un client en défaut de paiement
        # ne perd pas l'accès à ses propres données.
        listing = client.get("/api/v1/documents")
        assert listing.status_code == 200, listing.text
        assert any(item["id"] == existing["id"] for item in listing.json()["items"])
        subscription = client.get("/api/v1/billing/subscription").json()
        assert subscription["granted"] is False
        assert subscription["available_actions"] == ["subscribe"]


# ---------------------------------------------------------------------------
# Quotas : contrôle avant écriture, comptage par journal
# ---------------------------------------------------------------------------


def test_the_document_quota_stops_the_thirty_first_document_and_names_the_quota():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")

        for index in range(30):
            _create_document(client, title=f"Document {index + 1} de la période")

        usage = client.get("/api/v1/billing/usage").json()
        documents_line = next(line for line in usage["lines"] if line["metric"] == "documents")
        assert documents_line["used"] == 30
        assert documents_line["limit"] == 30
        assert documents_line["remaining"] == 0
        assert documents_line["exceeded"] is True

        refused = client.post(
            "/api/v1/documents",
            json={"title": "Trente et unième document", "document_type": "other"},
        )
        assert refused.status_code == 402, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "quota_exceeded"
        assert detail["metric"] == "documents"
        assert detail["used"] == 30 and detail["limit"] == 30 and detail["requested"] == 1
        assert detail["plan_code"] == "starter"
        assert detail["overage_policy"] == "blocked_no_charge"
        assert "aucun dépassement ne sera facturé" in detail["message"]
        assert detail["resets_at"] is not None
        assert [plan["code"] for plan in detail["upgrade_options"]] == ["pro", "enterprise"]

        # Rien n'a été écrit : ni document, ni consommation.
        with SessionLocal() as db:
            documents = db.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.organization_id == identity.organization_id)
            )
            events = db.scalar(
                select(func.count())
                .select_from(BillingUsageEvent)
                .where(BillingUsageEvent.organization_id == identity.organization_id)
            )
        assert documents == 30
        assert events == 30
        after = client.get("/api/v1/billing/usage").json()
        assert next(line for line in after["lines"] if line["metric"] == "documents")["used"] == 30


def test_the_usage_journal_is_the_only_counter_and_it_is_idempotent():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        first = _create_document(client, title="Un document pour le journal")

        with SessionLocal() as db:
            events = db.scalars(
                select(BillingUsageEvent).where(
                    BillingUsageEvent.organization_id == identity.organization_id,
                    BillingUsageEvent.metric == "documents",
                )
            ).all()
        assert len(events) == 1
        assert events[0].idempotency_key == f"document:{first['id']}"
        assert events[0].quantity == 1
        assert events[0].source_type == "document"

        # Rejouer la même source ne double pas la consommation : c'est la clé
        # d'idempotence, pas la discipline de l'appelant, qui l'empêche.
        from app.billing.plans import Metric
        from app.billing.usage import record_usage

        with SessionLocal() as db:
            result = record_usage(
                db,
                organization_id=identity.organization_id,
                metric=Metric.DOCUMENTS,
                quantity=1,
                source_type="document",
                source_id=first["id"],
                period_key=events[0].period_key,
                idempotency_key=f"document:{first['id']}",
            )
            db.commit()
        assert result.recorded is False
        usage = client.get("/api/v1/billing/usage").json()
        assert next(line for line in usage["lines"] if line["metric"] == "documents")["used"] == 1


def _two_page_pdf() -> bytes:
    """Un PDF de deux pages, construit pour le test : le nombre de pages est un fait."""

    import fitz

    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Première page de la déclaration.")
        pdf.new_page().insert_text((72, 72), "Deuxième page de la déclaration.")
        return pdf.tobytes()


def test_ocr_pages_are_counted_from_the_real_page_count_after_the_real_worker_run(monkeypatch):
    """La consommation OCR est mesurée sur le travail réellement effectué."""

    from tests.test_secure_documents import _install_document_fakes, _request_upload

    payload = _two_page_pdf()
    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            document = _create_document(client, title="Déclaration à extraire")
            instruction = _request_upload(
                client,
                str(document["id"]),
                filename="declaration.pdf",
                payload=payload,
            )
            storage.upload_latest(payload=payload, content_type="application/pdf")
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 201, completed.text
            version_id = UUID(completed.json()["version"]["id"])

            usage_before = client.get("/api/v1/billing/usage").json()
            line = next(item for item in usage_before["lines"] if item["metric"] == "ocr_pages")
            assert line["used"] == 0, "rien n'est compté avant que le travail n'ait eu lieu"

            from app.models.domain import DocumentVersion
            from app.workers.document_extraction_worker import DocumentExtractionWorker

            worker = DocumentExtractionWorker(
                settings=settings,
                storage=storage,
                session_factory=SessionLocal,
                worker_id="pytest-billing-worker",
            )
            monkeypatch.setattr(
                worker, "_active_organization_ids", lambda: [identity.organization_id]
            )
            assert worker.run_once() is True

            with SessionLocal() as db:
                version = db.scalar(
                    select(DocumentVersion).where(DocumentVersion.id == version_id)
                )
                assert version is not None
                pages = version.page_count
                journal = db.scalars(
                    select(BillingUsageEvent).where(
                        BillingUsageEvent.organization_id == identity.organization_id,
                        BillingUsageEvent.metric == "ocr_pages",
                    )
                ).all()
            assert pages == 2
            assert len(journal) == 1
            assert journal[0].quantity == pages
            assert journal[0].idempotency_key == f"ocr:{version_id}"

            after = client.get("/api/v1/billing/usage").json()
            line_after = next(item for item in after["lines"] if item["metric"] == "ocr_pages")
            assert line_after["used"] == pages
            assert line_after["limit"] == 300
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


def test_a_full_ocr_quota_stops_the_next_upload_before_the_worker_runs(monkeypatch):
    from tests.test_secure_documents import _install_document_fakes, _request_upload

    application, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(application) as client:
            identity = authenticate_client(client, role_code="owner")
            _subscribe(client, plan_code="starter")
            # Le quota du plan est de 300 pages : on remplit le journal au-delà
            # pour vérifier que le refus a lieu **avant** la mise en file OCR.
            from app.billing.enforcement import settle_usage
            from app.billing.plans import Metric

            # La période de comptage est celle de l'abonnement payé, pas le mois
            # calendaire : la fixture passe donc par le même résolveur que le code.
            with SessionLocal() as db:
                settle_usage(
                    db,
                    organization_id=identity.organization_id,
                    metric=Metric.OCR_PAGES,
                    quantity=300,
                    source_type="test_fixture",
                    source_id="saturated",
                    idempotency_key="ocr:saturation-test",
                )
                db.commit()
            line = next(
                item
                for item in client.get("/api/v1/billing/usage").json()["lines"]
                if item["metric"] == "ocr_pages"
            )
            assert line["used"] == 300, "la fixture doit saturer le quota réellement appliqué"

            document = _create_document(client, title="Import refusé par le quota OCR")
            instruction = _request_upload(
                client, str(document["id"]), filename="declaration.txt", payload=TEXT_BYTES
            )
            storage.upload_latest(payload=TEXT_BYTES)
            completed = client.post(
                f"/api/v1/document-uploads/{instruction['upload']['id']}/complete"
            )
            assert completed.status_code == 402, completed.text
            detail = completed.json()["detail"]
            assert detail["metric"] == "ocr_pages"
            assert detail["used"] == 300 and detail["limit"] == 300

            from app.models.domain import DocumentExtractionJob

            with SessionLocal() as db:
                jobs = db.scalar(
                    select(func.count())
                    .select_from(DocumentExtractionJob)
                    .where(DocumentExtractionJob.organization_id == identity.organization_id)
                )
            assert jobs == 0, "aucun travail OCR ne doit être mis en file quand le quota est plein"
    finally:
        application.state.document_storage = old_storage
        application.state.document_scanner = old_scanner


def test_the_seat_quota_refuses_the_invitation_that_would_exceed_the_plan():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")  # 2 sièges, dont le propriétaire

        first_email = f"premier.{uuid4().hex[:8]}@example.com"
        first = _invite(client, email=first_email)
        assert first.status_code == 201, first.text

        usage = client.get("/api/v1/billing/usage").json()
        seats = next(line for line in usage["lines"] if line["metric"] == "seats")
        assert seats["used"] == 2 and seats["limit"] == 2

        refused = _invite(client, email=f"second.{uuid4().hex[:8]}@example.com")
        assert refused.status_code == 402, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "quota_exceeded"
        assert detail["metric"] == "seats"
        assert detail["used"] == 2 and detail["limit"] == 2

        # Réinviter la personne déjà invitée ne consomme pas de siège
        # supplémentaire : le contrôle porte sur les sièges libres, pas sur l'appel.
        again = _invite(client, email=first_email)
        assert again.status_code == 201, again.text
        with SessionLocal() as db:
            members = db.scalar(
                select(func.count())
                .select_from(BillingUsageEvent)
                .where(
                    BillingUsageEvent.organization_id == identity.organization_id,
                    BillingUsageEvent.metric == "seats",
                )
            )
        assert members == 0, "les sièges sont une jauge : ils ne s'écrivent pas dans le journal"


# ---------------------------------------------------------------------------
# Cycle complet : montée, descente, résiliation, panne de paiement
# ---------------------------------------------------------------------------


def test_an_upgrade_applies_immediately_and_the_new_period_restores_the_quota():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        for index in range(30):
            _create_document(client, title=f"Saturation {index}")
        assert (
            client.post("/api/v1/documents", json={"title": "Refus attendu"}).status_code == 402
        )

        upgrade = client.post("/api/v1/billing/subscription/plan", json={"plan_code": "pro"})
        assert upgrade.status_code == 200, upgrade.text
        body = upgrade.json()
        assert body["applied"] is True
        assert body["effective"] == "immediate"
        assert body["state_synced"] is True
        assert body["subscription"]["plan_code"] == "pro"
        assert body["subscription"]["granted"] is True

        created = _create_document(client, title="Après montée de gamme")
        assert created["id"]
        usage = client.get("/api/v1/billing/usage").json()
        documents = next(line for line in usage["lines"] if line["metric"] == "documents")
        assert documents["limit"] == 300
        assert documents["used"] == 1, "la nouvelle période payée repart de zéro"
        assert usage["plan_code"] == "pro"


def test_the_upgrade_response_names_the_plan_the_organization_is_leaving():
    """La réponse dit d'où l'on vient, pas où l'on est.

    Défaut mesuré par la preuve HTTP (`audit/produce_c13_evidence.py`, étape 8) : le champ
    `current_plan_code` était lu sur l'abonnement **après** l'application de l'événement, donc
    il annonçait « Pro » à une organisation qui venait de quitter Starter. Un champ qui nomme
    l'état précédent et publie l'état courant se vérifie au moment où le client contrôle ce
    qu'il vient d'acheter.
    """

    with TestClient(app) as client:
        authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        before = client.get("/api/v1/billing/subscription").json()
        assert before["plan_code"] == "starter"

        response = client.post("/api/v1/billing/subscription/plan", json={"plan_code": "pro"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["applied"] is True
    assert payload["requested_plan_code"] == "pro"
    assert payload["current_plan_code"] == "starter", (
        "l'offre quittée doit être celle d'avant l'application, pas celle d'après"
    )
    assert payload["subscription"]["plan_code"] == "pro"


def test_a_downgrade_is_scheduled_for_the_end_of_the_paid_period_and_not_applied_now():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="pro")

        downgrade = client.post("/api/v1/billing/subscription/plan", json={"plan_code": "starter"})
        assert downgrade.status_code == 200, downgrade.text
        body = downgrade.json()
        assert body["applied"] is False
        assert body["effective"] == "period_end"
        assert body["requested_plan_code"] == "starter"
        assert body["subscription"]["plan_code"] == "pro", "l'offre payée reste active"
        assert body["subscription"]["pending_plan_code"] == "starter"
        assert body["subscription"]["pending_plan_effective_at"] is not None
        assert "aucun remboursement partiel" in body["detail"]

        # Les quotas de l'offre payée restent appliqués jusqu'à l'échéance.
        documents = next(
            line
            for line in client.get("/api/v1/billing/usage").json()["lines"]
            if line["metric"] == "documents"
        )
        assert documents["limit"] == 300


def test_the_scheduled_downgrade_is_applied_by_the_renewal_event():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="pro")
        client.post("/api/v1/billing/subscription/plan", json={"plan_code": "starter"})

        with SessionLocal() as db:
            subscription = db.scalar(
                select(BillingSubscription).where(
                    BillingSubscription.organization_id == identity.organization_id
                )
            )
            assert subscription is not None
            period_end = subscription.current_period_end
            provider_subscription_id = subscription.provider_subscription_id

        # Un renouvellement arrive *à* l'échéance : on place l'abonnement à cette
        # date avant d'envoyer l'événement, sinon on testerait un instant impossible.
        _expire_period(identity.organization_id, days_ago=0)

        provider = LocalProvider(settings)
        # Le renouvellement arrive à l'échéance : la descente programmée doit
        # s'appliquer à ce moment-là, et pas avant.
        envelope = provider.envelope(
            event_type="invoice.paid",
            payload={
                "provider_subscription_id": provider_subscription_id,
                "provider_invoice_id": f"in_test_{uuid4().hex}",
                "amount_cents": 4900,
                "currency": "eur",
                "period_start": period_end.astimezone(timezone.utc).isoformat(),
                "period_end": add_months(period_end, 1).astimezone(timezone.utc).isoformat(),
            },
        )
        ack = _post_webhook(client, envelope.body, envelope.headers)
        assert ack.status_code == 200, ack.text
        assert "descente de gamme appliquée" in ack.json()["detail"]

        subscription_body = client.get("/api/v1/billing/subscription").json()
        assert subscription_body["plan_code"] == "starter"
        assert subscription_body["pending_plan_code"] is None
        invoices = client.get("/api/v1/billing/invoices").json()["invoices"]
        assert len(invoices) == 1
        assert invoices[0]["amount_cents"] == 4900
        assert invoices[0]["status"] == "paid"


def test_a_cancellation_keeps_access_until_the_last_paid_day_then_blocks():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")

        cancelled = client.post(
            "/api/v1/billing/subscription/cancel", json={"at_period_end": True}
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["applied"] is True
        assert cancelled.json()["subscription"]["cancel_at_period_end"] is True
        assert cancelled.json()["subscription"]["granted"] is True
        assert "dernier jour payé" in cancelled.json()["subscription"]["reason"]

        still_allowed = _create_document(client, title="Toujours payé aujourd'hui")
        assert still_allowed["id"]

        # La résiliation peut être annulée jusqu'à l'échéance.
        resumed = client.post("/api/v1/billing/subscription/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["subscription"]["cancel_at_period_end"] is False

        client.post("/api/v1/billing/subscription/cancel", json={"at_period_end": True})
        _expire_period(identity.organization_id)
        after = client.get("/api/v1/billing/subscription").json()
        assert after["granted"] is False
        assert "Abonnement résilié" in after["reason"]
        refused = client.post("/api/v1/documents", json={"title": "Après la fin de période"})
        assert refused.status_code == 402, refused.text
        assert refused.json()["detail"]["code"] == "subscription_inactive"


def test_a_failed_payment_suspends_production_and_says_why():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        with SessionLocal() as db:
            subscription = db.scalar(
                select(BillingSubscription).where(
                    BillingSubscription.organization_id == identity.organization_id
                )
            )
            assert subscription is not None
            provider_subscription_id = subscription.provider_subscription_id

        provider = LocalProvider(settings)
        envelope = provider.envelope(
            event_type="payment.failed",
            payload={
                "provider_subscription_id": provider_subscription_id,
                "amount_cents": 4900,
            },
        )
        ack = _post_webhook(client, envelope.body, envelope.headers)
        assert ack.status_code == 200 and ack.json()["applied"] is True

        subscription_body = client.get("/api/v1/billing/subscription").json()
        assert subscription_body["status"] == "past_due"
        assert subscription_body["granted"] is False
        assert "paiement a été refusé" in subscription_body["reason"]

        refused = client.post("/api/v1/documents", json={"title": "Pendant l'impayé"})
        assert refused.status_code == 402, refused.text
        assert refused.json()["detail"]["code"] == "subscription_inactive"
        assert client.get("/api/v1/documents").status_code == 200

        # Un paiement qui rentre rétablit l'accès, par le même chemin d'événement.
        renewed = provider.envelope(
            event_type="invoice.paid",
            payload={
                "provider_subscription_id": provider_subscription_id,
                "provider_invoice_id": f"in_test_{uuid4().hex}",
                "amount_cents": 4900,
                "currency": "eur",
            },
        )
        assert _post_webhook(client, renewed.body, renewed.headers).status_code == 200
        assert client.get("/api/v1/billing/subscription").json()["granted"] is True
        assert _create_document(client, title="Après régularisation")["id"]


# ---------------------------------------------------------------------------
# Webhook : signature, unicité, idempotence
# ---------------------------------------------------------------------------


def test_a_webhook_without_a_valid_signature_changes_nothing():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        with SessionLocal() as db:
            before = db.scalar(
                select(BillingSubscription).where(
                    BillingSubscription.organization_id == identity.organization_id
                )
            )
            assert before is not None
            provider_subscription_id = before.provider_subscription_id
            plan_before = before.plan_code

        provider = LocalProvider(settings)
        envelope = provider.envelope(
            event_type="subscription.updated",
            payload={
                "provider_subscription_id": provider_subscription_id,
                "plan_code": "enterprise",
            },
        )
        tampered = envelope.body.replace(b"enterprise", b"starterxxx")
        response = _post_webhook(
            client, tampered, {"x-vericlaim-signature": envelope.headers["x-vericlaim-signature"]}
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "invalid_signature"

        unsigned = _post_webhook(client, envelope.body, {})
        assert unsigned.status_code == 400
        assert unsigned.json()["detail"]["code"] == "invalid_signature"

        assert (
            client.get("/api/v1/billing/subscription").json()["plan_code"] == plan_before
        ), "un événement non signé ne doit rien changer"
        with SessionLocal() as db:
            events = db.scalar(
                select(func.count())
                .select_from(BillingProviderEvent)
                .where(BillingProviderEvent.provider == "local")
            )
        assert events >= 1  # l'événement du paiement initial uniquement


def test_a_replayed_webhook_is_acknowledged_once_and_applied_once():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        with SessionLocal() as db:
            subscription = db.scalar(
                select(BillingSubscription).where(
                    BillingSubscription.organization_id == identity.organization_id
                )
            )
            assert subscription is not None
            provider_subscription_id = subscription.provider_subscription_id

        provider = LocalProvider(settings)
        # Un renouvellement payé : c'est l'événement qui écrit une facture, donc le
        # seul sur lequel la non-duplication peut être mesurée.
        envelope = provider.envelope(
            event_type="invoice.paid",
            payload={
                "provider_subscription_id": provider_subscription_id,
                # Identifiant unique au test : la base de test persiste d'une
                # exécution à l'autre, et un identifiant constant ferait croire à
                # un doublon de facture qui n'en est pas un.
                "provider_invoice_id": f"in_rejeu_{uuid4().hex}",
                "amount_cents": 4900,
                "currency": "eur",
                "period_start": datetime.now(timezone.utc).isoformat(),
                "period_end": add_months(datetime.now(timezone.utc), 1).isoformat(),
            },
        )
        first = _post_webhook(client, envelope.body, envelope.headers)
        second = _post_webhook(client, envelope.body, envelope.headers)
        assert first.status_code == 200 and first.json()["duplicate"] is False
        assert second.status_code == 200 and second.json()["duplicate"] is True
        assert second.json()["detail"] == first.json()["detail"]

        with SessionLocal() as db:
            events = db.scalar(
                select(func.count())
                .select_from(BillingProviderEvent)
                .where(BillingProviderEvent.provider_event_id == envelope.provider_event_id)
            )
        assert events == 1, "un rejeu ne doit pas créer un second événement"

        invoices = client.get("/api/v1/billing/invoices").json()["invoices"]
        assert len(invoices) == 1, "le renouvellement rejoué ne doit pas dupliquer la facture"


def test_an_event_for_an_unknown_subscription_is_recorded_but_not_applied():
    with TestClient(app) as client:
        authenticate_client(client, role_code="owner")
        provider = LocalProvider(settings)
        envelope = provider.envelope(
            event_type="invoice.paid",
            payload={"provider_subscription_id": f"sub_inconnu_{uuid4().hex}", "amount_cents": 4900},
        )
        ack = _post_webhook(client, envelope.body, envelope.headers)
        assert ack.status_code == 200, ack.text
        body = ack.json()
        assert body["applied"] is False
        assert "rien n'a été appliqué" in body["detail"]
        with SessionLocal() as db:
            event = db.scalar(
                select(BillingProviderEvent).where(
                    BillingProviderEvent.provider_event_id == envelope.provider_event_id
                )
            )
        assert event is not None and event.applied is False and event.signature_verified is True


def test_the_webhook_only_answers_for_the_provider_that_is_configured():
    with TestClient(app) as client:
        provider = LocalProvider(settings)
        envelope = provider.envelope(event_type="invoice.paid", payload={"amount_cents": 100})
        response = client.post(
            "/api/v1/billing/webhook/stripe",
            content=envelope.body,
            headers={**envelope.headers, "content-type": "application/json"},
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "unknown_provider"


# ---------------------------------------------------------------------------
# Rétention : le plafond de l'offre est appliqué, pas seulement affiché
# ---------------------------------------------------------------------------


def test_the_retention_actually_applied_is_capped_by_the_plan():
    with TestClient(app) as client_essai:
        identity = authenticate_client(client_essai, role_code="owner")
        document = _create_document(client_essai, title="Document ancien sous essai Pro")
        _backdate_document(document["id"], days=365 * 2)
        declared = client_essai.put(
            "/api/v1/pilot/retention-policy",
            json={"documents_retention_years": 10, "evidence_archive_retention_years": 5},
        )
        assert declared.status_code == 200, declared.text

        essai = client_essai.get("/api/v1/privacy/retention").json()
        assert essai["plan"]["ceiling_months"] == catalogue.PLAN_BY_CODE["pro"].quotas.retention_months
        assert essai["plan"]["applied_months"]["documents"] == 3, (
            "l'essai Pro couvre 36 mois : une déclaration de 10 ans est ramenée à 3 ans"
        )
        assert essai["plan"]["limited_by_plan"] is True
        assert "ne finance pas" in essai["plan"]["note"]

        purge_essai = client_essai.post("/api/v1/privacy/purge").json()
        decision = next(
            item for item in purge_essai["decisions"] if item["entity_id"] == document["id"]
        )
        assert decision["action"] == "keep", (
            "sous l'essai Pro, un document de 2 ans reste dans la fenêtre plafonnée à 3 ans"
        )

    with TestClient(app) as client_starter:
        identity = authenticate_client(client_starter, role_code="owner")
        _subscribe(client_starter, plan_code="starter")
        document = _create_document(client_starter, title="Document ancien sous Starter")
        _backdate_document(document["id"], days=365 * 2)
        declared = client_starter.put(
            "/api/v1/pilot/retention-policy",
            json={"documents_retention_years": 10, "evidence_archive_retention_years": 5},
        )
        assert declared.status_code == 200, declared.text

        retention = client_starter.get("/api/v1/privacy/retention").json()
        assert retention["plan"]["plan_code"] == "starter"
        assert retention["plan"]["ceiling_months"] == 12
        assert retention["plan"]["applied_months"]["documents"] == 1
        assert retention["plan"]["limited_by_plan"] is True
        assert "ne finance pas" in retention["plan"]["note"]

        plan = client_starter.post("/api/v1/privacy/purge").json()
        purged = [
            decision
            for decision in plan["decisions"]
            if decision["entity"] == "document" and decision["entity_id"] == document["id"]
        ]
        assert purged and purged[0]["action"] == "purge", (
            "un document de 2 ans dépasse la fenêtre de 12 mois de l'offre Starter : "
            "le plafond doit être appliqué, pas seulement affiché"
        )

        # La durée déclarée n'est pas réécrite : le client relit ce qu'il a déclaré.
        assert retention["declared"]["documents_retention_years"] == 10


# ---------------------------------------------------------------------------
# Droits binaires et garde de route
# ---------------------------------------------------------------------------


def test_an_api_key_is_refused_on_starter_and_allowed_on_pro():
    with TestClient(app) as client:
        authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        refused = client.post(
            "/api/v1/enterprise/api-keys",
            json={"name": "Intégration ERP", "scopes": ["documents:read"]},
        )
        assert refused.status_code == 402, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "entitlement_not_included"
        assert detail["entitlement"] == "api_keys"
        assert [plan["code"] for plan in detail["upgrade_options"]] == ["pro", "enterprise"]

        client.post("/api/v1/billing/subscription/plan", json={"plan_code": "pro"})
        created = client.post(
            "/api/v1/enterprise/api-keys",
            json={"name": "Intégration ERP", "scopes": ["documents:read"]},
        )
        assert created.status_code == 201, created.text
        assert created.json()["raw_api_key"].startswith("vc_live_"), (
            "la clé brute n'est rendue qu'une fois : le préfixe doit être celui que "
            "l'organisation reconnaîtra dans ses journaux"
        )


def test_every_sensitive_route_of_the_running_application_carries_the_gate():
    """La garde est vérifiée sur les routes réellement montées, pas sur un fichier."""

    exempt = {(method, path) for method, path, _ in EXEMPT_WRITE_ROUTES}
    for method, path, reason in EXEMPT_WRITE_ROUTES:
        assert len(reason) > 40, f"l'exemption {method} {path} doit porter une raison écrite"

    ungated: list[str] = []
    gated = 0
    for route in _iter_api_routes(app.routes):
        methods = {method for method in route.methods if method not in {"GET", "HEAD", "OPTIONS"}}
        if not methods or not any(route.path.startswith(prefix) for prefix in SENSITIVE_WRITE_PREFIXES):
            continue
        for method in methods:
            if (method, route.path) in exempt:
                continue
            if route_has_billing_gate(route):
                gated += 1
            else:
                ungated.append(f"{method} {route.path}")
    assert ungated == [], (
        "ces routes produisent un artefact payant sans vérifier l'abonnement : " + ", ".join(ungated)
    )
    assert gated >= 14, f"trop peu de routes protégées ({gated}) pour croire à la garde"


def test_reading_is_never_blocked_by_the_subscription_gate():
    """Un client en défaut de paiement garde l'accès à ce qu'il a déjà produit."""

    for route in _iter_api_routes(app.routes):
        methods = set(route.methods or set())
        if "GET" not in methods:
            continue
        if not any(route.path.startswith(prefix) for prefix in SENSITIVE_WRITE_PREFIXES):
            continue
        assert not route_has_billing_gate(route), (
            f"la lecture {route.path} ne doit pas dépendre du paiement"
        )


def test_the_privacy_routes_are_never_behind_the_payment_gate():
    """Droit à l'effacement et portabilité : jamais conditionnés à un paiement."""

    privacy_paths = []
    for route in _iter_api_routes(app.routes):
        if not route.path.startswith("/api/v1/privacy"):
            continue
        privacy_paths.append(route.path)
        assert not route_has_billing_gate(route), (
            f"{route.path} : un droit de la personne ne se conditionne pas à un abonnement"
        )
    assert privacy_paths, "les routes de vie privée doivent exister"


# ---------------------------------------------------------------------------
# Adaptateur Stripe : écrit contre l'API, jamais exécuté
# ---------------------------------------------------------------------------


def _stripe_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        frontend_url="https://app.example.test",
        billing_provider="stripe",
        stripe_secret_key="sk_test_not_a_real_key",
        stripe_webhook_secret="whsec_not_a_real_secret",
        stripe_price_ids_json=json.dumps({"starter": "price_start", "pro": "price_pro"}),
        auto_create_schema=False,
        database_url=f"sqlite:///{tmp_path / 'stripe.db'}",
    )


def test_the_stripe_adapter_builds_the_documented_checkout_request_without_any_network(tmp_path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.test/1"})

    provider = StripeProvider(_stripe_settings(tmp_path), transport=httpx.MockTransport(handler))
    from app.models.domain import Organization

    organization = Organization(name="Client", slug="client-test")
    organization.id = uuid4()
    result = provider.create_checkout(
        __import__("app.billing.providers", fromlist=["CheckoutRequest"]).CheckoutRequest(
            organization=organization,
            plan_code="pro",
            price_id=provider.price_id_for("pro"),
            success_url="https://app.example.test/app",
            cancel_url="https://app.example.test/pricing",
            customer_email="direction@example.test",
        )
    )
    assert result.provider_session_id == "cs_test_1"
    assert result.url == "https://checkout.stripe.test/1"

    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.stripe.com/v1/checkout/sessions"
    body = request.content.decode()
    assert "mode=subscription" in body
    assert "line_items%5B0%5D%5Bprice%5D=price_pro" in body
    assert f"subscription_data%5Bmetadata%5D%5Borganization_id%5D={organization.id}" in body
    assert request.headers["authorization"].startswith("Basic ")


def test_the_stripe_adapter_maps_its_own_events_and_refuses_unsigned_ones(tmp_path):
    settings_stripe = _stripe_settings(tmp_path)
    provider = StripeProvider(settings_stripe)
    payload = json.dumps(
        {
            "id": "evt_test_1",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_test_1",
                    "cancel_at_period_end": True,
                    "status": "active",
                    "metadata": {"plan_code": "pro"},
                    "items": {"data": [{"price": {"id": "price_pro"}, "current_period_end": 1790000000}]},
                }
            },
        }
    ).encode()
    signature = sign_payload(secret="whsec_not_a_real_secret", body=payload)
    envelope = provider.verify_event(body=payload, headers={"stripe-signature": signature})
    assert envelope.event_type == "subscription.updated"
    assert envelope.payload["provider_subscription_id"] == "sub_test_1"
    assert envelope.payload["cancel_at_period_end"] is True
    assert envelope.payload["plan_code"] == "pro"

    with pytest.raises(Exception):
        provider.verify_event(body=payload, headers={"stripe-signature": "t=1,v1=deadbeef"})

    ignored = json.dumps({"id": "evt_test_2", "type": "customer.created", "data": {"object": {}}}).encode()
    with pytest.raises(Exception) as excinfo:
        provider.verify_event(
            body=ignored,
            headers={"stripe-signature": sign_payload(secret="whsec_not_a_real_secret", body=ignored)},
        )
    assert "ignoré" in str(excinfo.value)


def test_the_stripe_provider_is_never_selected_in_this_environment():
    """L'adaptateur existe, mais aucune clé n'est présente : il ne doit pas être actif."""

    assert settings.billing_provider == "local"
    assert settings.billing_collects_money is False
    assert settings.stripe_secret_key is None


# ---------------------------------------------------------------------------
# Configuration : le prestataire simulé ne peut pas encaisser en production
# ---------------------------------------------------------------------------


PRODUCTION_ENV = {
    "APP_ENV": "production",
    "DATABASE_URL": POSTGRES_PLACEHOLDER_URL,
    "AUTO_CREATE_SCHEMA": "false",
    "AUTH_SESSION_SECRET": "p" * 48,
    "AUTH_COOKIE_SECURE": "true",
    "FRONTEND_URL": "https://app.example",
    "CORS_ORIGINS": "https://app.example",
    "OIDC_ISSUER": "https://idp.example",
    "OIDC_CLIENT_ID": "vericlaim-web",
    "OIDC_REDIRECT_URI": "https://app.example/api/v1/auth/callback",
    "DOCUMENT_STORAGE_BACKEND": "s3",
    "DOCUMENT_STORAGE_ENDPOINT": "https://minio.example",
    "DOCUMENT_STORAGE_PUBLIC_ENDPOINT": "https://objects.example",
    "DOCUMENT_STORAGE_SSE_MODE": "aes256",
    "DOCUMENT_QUARANTINE_BUCKET": "vericlaim-quarantine",
    "DOCUMENT_CLEAN_BUCKET": "vericlaim-documents",
    "DOCUMENT_SCANNER_MODE": "clamav",
    "DOCUMENT_CLAMAV_HOST": "clamav.example",
    "REPORT_SIGNING_KEY": "Rk7Qm2Xv9BpL4Tn6Ys3Wz8Hd5Jc0Fa1Ug",
}


def _run_settings_in_subprocess(
    overrides: dict[str, str], *, script: str | None = None
) -> subprocess.CompletedProcess:
    environment = {**PRODUCTION_ENV, **overrides}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            script
            or "from app.core.config import settings; print(settings.billing_provider, settings.billing_collects_money)",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_the_local_provider_is_refused_in_a_production_like_environment(tmp_path):
    result = _run_settings_in_subprocess(
        {
            "APP_ENV": "production",
            "BILLING_PROVIDER": "local",
        }
    )
    assert result.returncode != 0
    assert "BILLING_PROVIDER=local is refused" in (result.stderr + result.stdout)


def test_the_simulated_payment_route_does_not_exist_in_a_production_like_boot():
    """La route qui simule un paiement n'est **pas montée** en production.

    Deux barrières, et deux mesures distinctes : la configuration refuse le prestataire
    `local` en production (test ci-dessus), et même avec un prestataire désactivé, le routeur
    de paiement simulé n'est pas enregistré dans l'application. Une route qui existe et qu'une
    variable d'environnement protège reste une route déployée : c'est cette seconde barrière
    qui est vérifiée ici, sur l'application réellement construite, par son schéma OpenAPI.
    """

    probe = (
        "from app.main import app;"
        "paths = app.openapi()['paths'];"
        "print('LOCAL_CHECKOUT_ROUTE', any('local/checkout' in path for path in paths));"
        "print('PLANS_ROUTE', '/api/v1/billing/plans' in paths)"
    )

    disabled = _run_settings_in_subprocess(
        {"APP_ENV": "production", "BILLING_PROVIDER": "disabled"}, script=probe
    )
    assert disabled.returncode == 0, disabled.stderr
    assert "LOCAL_CHECKOUT_ROUTE False" in disabled.stdout, disabled.stdout

    # Avec un prestataire réel configuré, le module reste monté : la route de paiement
    # simulé est la seule à disparaître. Sans cette seconde mesure, l'absence ci-dessus
    # pourrait venir d'un module de facturation entier absent, et ne prouverait rien.
    stripe = _run_settings_in_subprocess(
        {
            "APP_ENV": "production",
            "BILLING_PROVIDER": "stripe",
            "STRIPE_SECRET_KEY": "sk_live_not_a_real_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_not_a_real_secret",
        },
        script=probe,
    )
    assert stripe.returncode == 0, stripe.stderr
    assert "LOCAL_CHECKOUT_ROUTE False" in stripe.stdout, stripe.stdout
    assert "PLANS_ROUTE True" in stripe.stdout, stripe.stdout


def test_a_production_like_boot_without_a_provider_simply_stops_selling(tmp_path):
    result = _run_settings_in_subprocess(
        {
            "APP_ENV": "production",
            "BILLING_PROVIDER": "disabled",
        }
    )
    assert result.returncode == 0, result.stderr
    assert "disabled False" in result.stdout


def test_the_stripe_provider_requires_both_keys(tmp_path):
    result = _run_settings_in_subprocess(
        {
            "APP_ENV": "production",
            "BILLING_PROVIDER": "stripe",
            "STRIPE_SECRET_KEY": "sk_live_not_a_real_key",
        }
    )
    assert result.returncode != 0
    assert "STRIPE_WEBHOOK_SECRET" in (result.stderr + result.stdout)


# ---------------------------------------------------------------------------
# Le portail dit ce qu'il sait, et rien de plus
# ---------------------------------------------------------------------------


def test_the_portal_reports_the_usage_of_the_period_and_the_provider_in_force():
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        _subscribe(client, plan_code="starter")
        _create_document(client, title="Un document pour l'affichage")
        usage = client.get("/api/v1/billing/usage").json()
        subscription = client.get("/api/v1/billing/subscription").json()
        health = client.get("/api/v1/billing/health").json()

    assert usage["overage_policy"] == "blocked_no_charge"
    assert usage["period_start"] < usage["period_end"]
    assert usage["resets_at"] == usage["period_end"]
    assert subscription["seats_used"] >= 1
    assert health["provider"] == settings.billing_provider
    assert health["collects_money"] == settings.billing_collects_money
    assert any("Stripe" in line for line in health["limitations"])
    assert any("aucun dépassement facturé" in line for line in health["limitations"])
    assert {
        point["metric"] for point in health["enforcement_points"]
    } >= {"documents", "ocr_pages", "seats", "retention", "subscription_status"}
