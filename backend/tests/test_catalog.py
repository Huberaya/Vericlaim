from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.domain import AuditEvent
from tests.auth_support import authenticate_client


def _supplier_payload(
    *, legal_name: str, external_reference: str | None = None
) -> dict[str, object]:
    return {
        "legal_name": legal_name,
        "trading_name": f"{legal_name} France",
        "external_reference": external_reference,
        "country_code": "fr",
        "contact_email": "catalogue@example.test",
        "metadata": {"source": "purchase-master", "labels": ["eu"]},
    }


def _create_supplier(
    client: TestClient,
    *,
    legal_name: str,
    external_reference: str | None = None,
    key: str,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/suppliers",
        json=_supplier_payload(
            legal_name=legal_name, external_reference=external_reference
        ),
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["supplier"]


def test_catalog_creates_replays_lists_and_controls_document_context():
    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")

        missing_key = client.post(
            "/api/v1/suppliers", json=_supplier_payload(legal_name="Alizé SA")
        )
        assert missing_key.status_code == 422

        create_payload = _supplier_payload(
            legal_name="Alizé SA", external_reference="SUP-ALIZE"
        )
        created = client.post(
            "/api/v1/suppliers",
            json=create_payload,
            headers={"Idempotency-Key": "supplier-alize-create"},
        )
        assert created.status_code == 201, created.text
        supplier = created.json()["supplier"]
        assert created.json()["idempotent_replay"] is False
        assert supplier["country_code"] == "FR"
        assert supplier["archived_at"] is None

        replay = client.post(
            "/api/v1/suppliers",
            json=create_payload,
            headers={"Idempotency-Key": "supplier-alize-create"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["supplier"]["id"] == supplier["id"]
        idempotency_collision = client.post(
            "/api/v1/suppliers",
            json=_supplier_payload(legal_name="Même clé, autre demande"),
            headers={"Idempotency-Key": "supplier-alize-create"},
        )
        assert idempotency_collision.status_code == 409

        collision = client.post(
            "/api/v1/suppliers",
            json=_supplier_payload(
                legal_name="Alizé différente", external_reference="SUP-ALIZE"
            ),
            headers={"Idempotency-Key": "supplier-alize-other"},
        )
        assert collision.status_code == 409

        product_payload = {
            "supplier_id": supplier["id"],
            "reference": "SKU-ALIZE-01",
            "name": "Flacon Alizé",
            "category": "emballage",
            "country_of_sale": "fr",
            "lifecycle_status": "active",
            "metadata": {"material": "PET"},
        }
        product_created = client.post(
            "/api/v1/products",
            json=product_payload,
            headers={"Idempotency-Key": "product-alize-create"},
        )
        assert product_created.status_code == 201, product_created.text
        product = product_created.json()["product"]
        assert product["supplier_id"] == supplier["id"]
        assert product["country_of_sale"] == "FR"

        product_replay = client.post(
            "/api/v1/products",
            json=product_payload,
            headers={"Idempotency-Key": "product-alize-create"},
        )
        assert product_replay.status_code == 200
        assert product_replay.json()["idempotent_replay"] is True
        assert product_replay.json()["product"]["id"] == product["id"]

        other_supplier = _create_supplier(
            client, legal_name="Autre fournisseur", key="supplier-other"
        )
        mismatch = client.post(
            "/api/v1/documents",
            json={
                "title": "Rattachement incohérent",
                "supplier_id": other_supplier["id"],
                "product_id": product["id"],
            },
        )
        assert mismatch.status_code == 409

        document = client.post(
            "/api/v1/documents",
            json={
                "title": "Fiche du flacon Alizé",
                "document_type": "product_sheet",
                "supplier_id": supplier["id"],
                "product_id": product["id"],
                "tags": ["catalogue"],
            },
        )
        assert document.status_code == 201, document.text
        assert document.json()["supplier_id"] == supplier["id"]
        assert document.json()["product_id"] == product["id"]

        read_supplier = client.get(f"/api/v1/suppliers/{supplier['id']}")
        read_product = client.get(f"/api/v1/products/{product['id']}")
        assert read_supplier.status_code == 200
        assert read_product.status_code == 200
        assert read_product.json()["reference"] == "SKU-ALIZE-01"

        with SessionLocal() as db:
            actions = set(
                db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.organization_id == identity.organization_id
                    )
                ).all()
            )
            assert {
                "catalog.supplier_created",
                "catalog.product_created",
                "document.created",
            } <= actions


def test_catalog_enforces_tenant_rbac_csrf_and_supplier_product_integrity():
    from app.main import app

    with (
        TestClient(app) as owner_client,
        TestClient(app) as foreign_client,
        TestClient(app) as viewer_client,
    ):
        owner = authenticate_client(owner_client, role_code="owner")
        authenticate_client(foreign_client, role_code="analyst")
        authenticate_client(viewer_client, role_code="viewer")

        supplier = _create_supplier(
            owner_client,
            legal_name="Fournisseur propriétaire",
            external_reference="SUP-OWNER",
            key="owner-supplier",
        )
        product = owner_client.post(
            "/api/v1/products",
            json={
                "supplier_id": supplier["id"],
                "reference": "SKU-OWNER",
                "name": "Produit propriétaire",
            },
            headers={"Idempotency-Key": "owner-product"},
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["product"]["id"]

        assert (
            foreign_client.get(f"/api/v1/suppliers/{supplier['id']}").status_code == 404
        )
        foreign_product = foreign_client.post(
            "/api/v1/products",
            json={
                "supplier_id": supplier["id"],
                "reference": "SKU-FOREIGN",
                "name": "Produit interdit",
            },
            headers={"Idempotency-Key": "foreign-product"},
        )
        assert foreign_product.status_code == 404

        assert viewer_client.get("/api/v1/suppliers").status_code == 200
        assert viewer_client.get("/api/v1/products").status_code == 200
        assert (
            viewer_client.post(
                "/api/v1/suppliers",
                json=_supplier_payload(legal_name="Tentative lecteur"),
                headers={"Idempotency-Key": "viewer-denied"},
            ).status_code
            == 403
        )

        owner_client.headers.pop("X-CSRF-Token", None)
        csrf_denied = owner_client.patch(
            f"/api/v1/suppliers/{supplier['id']}", json={"trading_name": "Sans CSRF"}
        )
        assert csrf_denied.status_code == 403
        assert "CSRF" in csrf_denied.json()["detail"]
        # Restore the browser header from the double-submit cookie.
        owner_client.headers["X-CSRF-Token"] = owner_client.cookies.get(
            "vericlaim_csrf"
        )

        archive_supplier_first = owner_client.delete(
            f"/api/v1/suppliers/{supplier['id']}"
        )
        assert archive_supplier_first.status_code == 409
        assert "produits actifs" in archive_supplier_first.json()["detail"]
        assert owner.organization_id != UUID("00000000-0000-0000-0000-000000000000")

        assert owner_client.delete(f"/api/v1/products/{product_id}").status_code == 204
        # A repeat DELETE is a safe no-op and does not erase historic FKs.
        assert owner_client.delete(f"/api/v1/products/{product_id}").status_code == 204
        assert (
            owner_client.delete(f"/api/v1/suppliers/{supplier['id']}").status_code
            == 204
        )
        assert (
            owner_client.get(f"/api/v1/suppliers/{supplier['id']}").status_code == 404
        )
        assert owner_client.get(f"/api/v1/products/{product_id}").status_code == 404


def test_catalog_updates_are_audited_and_lists_use_opaque_cursors():
    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="admin")
        first = _create_supplier(
            client, legal_name="Alpha Industries", key="supplier-alpha"
        )
        second = _create_supplier(
            client, legal_name="Bravo Industrie", key="supplier-bravo"
        )
        third = _create_supplier(
            client, legal_name="Charlie Industrie", key="supplier-charlie"
        )

        first_page = client.get("/api/v1/suppliers?limit=2")
        assert first_page.status_code == 200, first_page.text
        first_body = first_page.json()
        assert [item["id"] for item in first_body["items"]] == [
            first["id"],
            second["id"],
        ]
        assert first_body["next_cursor"]
        second_page = client.get(
            f"/api/v1/suppliers?limit=2&cursor={first_body['next_cursor']}"
        )
        assert second_page.status_code == 200
        assert [item["id"] for item in second_page.json()["items"]] == [third["id"]]
        assert second_page.json()["next_cursor"] is None
        assert client.get("/api/v1/suppliers?cursor=not-a-cursor").status_code == 422

        patched = client.patch(
            f"/api/v1/suppliers/{first['id']}",
            json={"trading_name": "Alpha France", "metadata": {"reviewed": True}},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["trading_name"] == "Alpha France"
        assert patched.json()["metadata"] == {"reviewed": True}
        # Replaying an explicit PATCH state is a no-op, including the audit log.
        replay_patch = client.patch(
            f"/api/v1/suppliers/{first['id']}",
            json={"trading_name": "Alpha France", "metadata": {"reviewed": True}},
        )
        assert replay_patch.status_code == 200

        product = client.post(
            "/api/v1/products",
            json={
                "supplier_id": first["id"],
                "reference": "ALPHA-100",
                "name": "Article Alpha",
            },
            headers={"Idempotency-Key": "alpha-product"},
        )
        assert product.status_code == 201
        no_reassignment = client.patch(
            f"/api/v1/products/{product.json()['product']['id']}",
            json={"supplier_id": second["id"]},
        )
        assert no_reassignment.status_code == 422

        with SessionLocal() as db:
            actions = set(
                db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.organization_id == identity.organization_id
                    )
                ).all()
            )
            assert "catalog.supplier_updated" in actions
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.organization_id == identity.organization_id,
                        AuditEvent.entity_id == UUID(first["id"]),
                        AuditEvent.action == "catalog.supplier_updated",
                    )
                )
                == 1
            )
