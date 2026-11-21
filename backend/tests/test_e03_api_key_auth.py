"""E-03 — l'authentification par clé API est réelle, scopée et isolée par tenant.

Avant ce correctif, les clés étaient créables mais jamais acceptées : aucune
requête ne pouvait être authentifiée avec. Ces tests courent le chemin machine
complet : transport, refus, périmètres, isolation, CSRF inapplicable, audit et
limite contractuelle par clé.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_support import authenticate_client

EVALUATE_PAYLOAD = {
    "source_text": "Bouteille 100% biodégradable.",
    "context": {
        "as_of_date": "2026-09-24",
        "jurisdiction": "FR",
        "surface": "packaging",
        "consumer_facing": True,
        "product_identifier": "SKU-1",
    },
    "evidence": {"items": [], "legal_person": True},
}


def _create_key(client: TestClient, *, scopes: list[str], rate_limit_per_minute: int = 240) -> dict:
    response = client.post(
        "/api/v1/enterprise/api-keys",
        json={
            "name": "Clé de test",
            "scopes": scopes,
            "rate_limit_per_minute": rate_limit_per_minute,
            "expires_in_days": 30,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _machine_client(raw_key: str) -> TestClient:
    """Client sans cookie ni CSRF : le transport machine est l'en-tête seul."""
    machine = TestClient(app)
    machine.headers["X-API-Key"] = raw_key
    machine.headers["User-Agent"] = "erp-achats/1.0"
    return machine


def test_api_key_authenticates_machine_requests_without_cookies():
    with TestClient(app) as admin:
        authenticate_client(admin, role_code="owner")
        created = _create_key(admin, scopes=["audit:run", "audit:read", "rules:read"])

    machine = _machine_client(created["raw_api_key"])
    rules = machine.get("/api/v1/engine/rules")
    assert rules.status_code == 200, rules.text

    # Pas de cookie, pas de jeton CSRF : la mutation machine passe par l'en-tête.
    evaluation = machine.post("/api/v1/engine/evaluate", json=EVALUATE_PAYLOAD)
    assert evaluation.status_code == 200, evaluation.text
    assert evaluation.json()["extracted_source_text"] == EVALUATE_PAYLOAD["source_text"]


def test_api_key_scopes_are_enforced():
    with TestClient(app) as admin:
        authenticate_client(admin, role_code="owner")
        created = _create_key(admin, scopes=["catalog:read"])

    machine = _machine_client(created["raw_api_key"])
    assert machine.get("/api/v1/suppliers").status_code == 200
    # audit:run absent : l'évaluation est refusée malgré une session valide
    # d'organisation — la clé ne porte que ce qu'elle porte.
    assert machine.post("/api/v1/engine/evaluate", json=EVALUATE_PAYLOAD).status_code == 403


def test_invalid_revoked_and_expired_keys_are_refused():
    from app.core.database import SessionLocal
    from app.models.domain import ApiKey

    with TestClient(app) as admin:
        authenticate_client(admin, role_code="owner")
        created = _create_key(admin, scopes=["rules:read"])
        raw_key = created["raw_api_key"]

    machine = _machine_client(raw_key)
    assert machine.get("/api/v1/engine/rules").status_code == 200

    garbage = _machine_client("vc_live_" + "z" * 43)
    assert garbage.get("/api/v1/engine/rules").status_code == 401

    # Révocation : la clé devient inutilisable immédiatement.
    with SessionLocal() as db:
        key_row = db.get(ApiKey, UUID(created["id"]))
        key_row.is_active = False
        db.commit()
    assert machine.get("/api/v1/engine/rules").status_code == 401

    # Expiration : même refus.
    with SessionLocal() as db:
        key_row = db.get(ApiKey, UUID(created["id"]))
        key_row.is_active = True
        key_row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    assert machine.get("/api/v1/engine/rules").status_code == 401


def test_api_key_never_falls_back_to_the_cookie_session():
    """Une requête qui présente une clé invalide ne devient pas la session."""
    with TestClient(app) as admin:
        authenticate_client(admin, role_code="owner")  # session cookie présente
        res = admin.get("/api/v1/engine/rules")
        assert res.status_code == 200

        admin.headers["X-API-Key"] = "vc_live_" + "y" * 43
        refused = admin.get("/api/v1/engine/rules")
        assert refused.status_code == 401


def test_human_only_scopes_are_refused_at_creation():
    with TestClient(app) as admin:
        authenticate_client(admin, role_code="owner")
        for scope in ("members:manage", "organization:manage", "validation:manage", "unknown:scope"):
            res = admin.post(
                "/api/v1/enterprise/api-keys",
                json={"name": "Clé abusive", "scopes": [scope], "expires_in_days": 30},
            )
            assert res.status_code == 422, (scope, res.text)

        # Et le vocabulaire machine reste créable.
        created = _create_key(admin, scopes=["documents:manage", "evidence:manage"])
        assert created["scopes"] == ["documents:manage", "evidence:manage"]


def test_api_key_is_tenant_scoped():
    from app.core.database import SessionLocal

    with TestClient(app) as admin:
        identity = authenticate_client(admin, role_code="owner")
        created = _create_key(admin, scopes=["catalog:read", "catalog:manage"])

    machine = _machine_client(created["raw_api_key"])
    # Le catalogue de la clé est celui de SON organisation — ni plus, ni moins.
    assert machine.get("/api/v1/suppliers").json()["items"] == []

    # Un fournisseur d'une autre organisation n'apparaît jamais.
    from uuid import uuid4

    from app.models.domain import Organization, OrganizationStatus, Supplier

    with SessionLocal() as db:
        foreign_slug = f"org-etranger-e03-{uuid4().hex[:8]}"
        foreign_org = Organization(name="Étranger", slug=foreign_slug, status=OrganizationStatus.ACTIVE)
        db.add(foreign_org)
        db.flush()
        db.add(Supplier(organization_id=foreign_org.id, legal_name="Fournisseur étranger"))
        db.commit()
    listing = machine.get("/api/v1/suppliers").json()["items"]
    assert listing == []
    # L'identité de la clé porte bien l'organisation de la clé.
    assert identity.organization_id is not None


def test_key_mutations_need_no_csrf_but_are_audited():
    from app.core.database import SessionLocal
    from app.models.domain import AuditEvent
    from sqlalchemy import select

    with TestClient(app) as admin:
        identity = authenticate_client(admin, role_code="owner")
        created = _create_key(admin, scopes=["catalog:manage"])

    machine = _machine_client(created["raw_api_key"])
    # Pas d'en-tête CSRF sur ce client : la création passe par le contrat de clé.
    created_supplier = machine.post(
        "/api/v1/suppliers",
        json={"legal_name": "Fournisseur via clé"},
        headers={"Idempotency-Key": "e03-cle-0001"},
    )
    assert created_supplier.status_code in {200, 201}, created_supplier.text

    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == identity.organization_id,
                    AuditEvent.action == "catalog.supplier_created",
                )
            ).all()
        )
        assert events, "l'action machine doit être journalisée"
        # Attribution : le membre créateur de la clé, jamais un fantôme.
        assert events[-1].actor_user_id == identity.user_id


def test_api_key_last_used_at_is_tracked():
    from app.core.database import SessionLocal
    from app.models.domain import ApiKey

    with TestClient(app) as admin:
        authenticate_client(admin, role_code="owner")
        created = _create_key(admin, scopes=["rules:read"])

    machine = _machine_client(created["raw_api_key"])
    assert machine.get("/api/v1/engine/rules").status_code == 200

    with SessionLocal() as db:
        key_row = db.get(ApiKey, UUID(created["id"]))
        assert key_row.last_used_at is not None


def test_per_key_rate_limit_is_contractual():
    """La valeur stockée sur la clé est appliquée, indépendamment du reste."""
    with TestClient(app) as admin:
        authenticate_client(admin, role_code="owner")
        created = _create_key(admin, scopes=["rules:read"], rate_limit_per_minute=10)

    from app.core.rate_limit import LIMITER

    LIMITER.reset()
    machine = _machine_client(created["raw_api_key"])
    statuses = [machine.get("/api/v1/engine/rules").status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429
    refused = machine.get("/api/v1/engine/rules")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
