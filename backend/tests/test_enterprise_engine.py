"""Tests for Chantier 9 — Enterprise Industrialization (API Keys, Observability Metrics, SCIM 2.0 Provisioning, Legal Holds)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_support import authenticate_client


def test_api_key_lifecycle_and_tenant_isolation():
    with TestClient(app) as client, TestClient(app) as foreign_client:
        identity = authenticate_client(client, role_code="owner")
        foreign_identity = authenticate_client(foreign_client, role_code="owner")

        # 1. Create API Key
        res = client.post(
            "/api/v1/enterprise/api-keys",
            json={
                "name": "Integration ERP Achats",
                "scopes": ["audit:run", "catalog:read"],
                "rate_limit_per_minute": 240,
                "expires_in_days": 180,
            },
        )
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["name"] == "Integration ERP Achats"
        assert data["prefix"].startswith("vc_live_")
        assert len(data["raw_api_key"]) > 30
        key_id = data["id"]

        # 2. List API Keys
        list_res = client.get("/api/v1/enterprise/api-keys")
        assert list_res.status_code == 200
        keys = list_res.json()
        assert len(keys) == 1
        assert keys[0]["id"] == key_id
        assert keys[0]["is_active"] is True

        # 3. Foreign tenant sees 0 keys
        foreign_list = foreign_client.get("/api/v1/enterprise/api-keys")
        assert foreign_list.status_code == 200
        assert len(foreign_list.json()) == 0

        # 4. Revoke API Key
        del_res = client.delete(f"/api/v1/enterprise/api-keys/{key_id}")
        assert del_res.status_code == 204

        # 5. Verify it is inactive
        list_after = client.get("/api/v1/enterprise/api-keys").json()
        assert list_after[0]["is_active"] is False


def test_enterprise_metrics_and_alerts():
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")

        # Metrics
        res = client.get("/api/v1/enterprise/metrics")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["service_status"] == "healthy"
        assert data["database_status"] == "connected"
        assert data["uptime_seconds"] > 0
        assert data["memory_usage_mb"] > 0

        # Alerts
        alert_res = client.get("/api/v1/enterprise/alerts")
        assert alert_res.status_code == 200, alert_res.text
        alerts = alert_res.json()
        assert len(alerts) >= 2
        assert any("SSO" in a["title"] for a in alerts)


def test_scim_user_provisioning():
    with TestClient(app) as client:
        authenticate_client(client, role_code="owner")

        scim_payload = {
            "userName": "auditeur.externe@entreprise.fr",
            "displayName": "Auditeur Externe",
            "emails": [{"value": "auditeur.externe@entreprise.fr", "primary": True}],
            "active": True,
        }

        res = client.post("/api/v1/enterprise/scim/v2/Users", json=scim_payload)
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["userName"] == "auditeur.externe@entreprise.fr"
        assert data["displayName"] == "Auditeur Externe"
        assert data["active"] is True
        assert "urn:ietf:params:scim:schemas:core:2.0:User" in data["schemas"]


def test_legal_hold_and_ediscovery():
    with TestClient(app) as client:
        authenticate_client(client, role_code="owner")

        # 1. Create Legal Hold
        res = client.post(
            "/api/v1/enterprise/legal-holds",
            json={
                "case_reference": "AFFAIRE-DGCCRF-2026-089",
                "reason": "Demande de communication de pièces pour contrôle de conformité AGEC",
            },
        )
        assert res.status_code == 201, res.text
        hold = res.json()
        assert hold["case_reference"] == "AFFAIRE-DGCCRF-2026-089"
        assert hold["is_active"] is True

        # 2. List Legal Holds
        list_res = client.get("/api/v1/enterprise/legal-holds")
        assert list_res.status_code == 200
        assert len(list_res.json()) >= 1
