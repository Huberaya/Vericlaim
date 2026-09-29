"""Tests for Chantier 7 — Regulatory Governance & Versioned Rule Book Engine."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_support import authenticate_client


def test_rulebook_overview_and_summary():
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")

        res = client.get("/api/v1/regulatory/rulebook")
        assert res.status_code == 200, res.text
        data = res.json()

        assert "2026-09-24+" in data["rulebook_version"]
        assert len(data["sha256_fingerprint"]) == 64
        assert data["total_rules"] >= 7
        assert data["jurisdiction_breakdown"]["FR"] >= 3
        assert data["jurisdiction_breakdown"]["EU"] >= 3
        assert data["jurisdiction_breakdown"]["INTERNATIONAL"] >= 2
        assert "pré-audit" in data["disclaimer"]


def test_rules_filtering_and_citations():
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")

        # 1. List all rules
        all_rules = client.get("/api/v1/regulatory/rules").json()
        assert len(all_rules) >= 7

        # 2. Filter by Jurisdiction (FR)
        fr_rules = client.get("/api/v1/regulatory/rules?jurisdiction=FR").json()
        assert len(fr_rules) >= 3
        assert all(r["jurisdiction"] == "FR" for r in fr_rules)

        # 3. Filter by Jurisdiction (EU)
        eu_rules = client.get("/api/v1/regulatory/rules?jurisdiction=EU").json()
        assert len(eu_rules) >= 3
        assert all(r["jurisdiction"] == "EU" for r in eu_rules)

        # 4. Filter by Legal Status (proposal)
        proposals = client.get("/api/v1/regulatory/rules?legal_status=proposal").json()
        assert len(proposals) == 1
        assert proposals[0]["rule_id"] == "RULE_EU_COMPARATIVE_LCA"

        # 5. Filter by Claim Type (carbon_neutrality)
        carbon_rules = client.get("/api/v1/regulatory/rules?claim_type=carbon_neutrality").json()
        assert len(carbon_rules) >= 2

        # 6. Search filter
        search_res = client.get("/api/v1/regulatory/rules?search=biod%C3%A9gradable").json()
        assert len(search_res) >= 1
        assert search_res[0]["rule_id"] == "RULE_AGEC_BIODEGRADABLE"


def test_rule_detail_exact_citations():
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")

        # 1. Test AGEC Biodegradable Rule Detail
        res = client.get("/api/v1/regulatory/rules/RULE_AGEC_BIODEGRADABLE")
        assert res.status_code == 200, res.text
        rule = res.json()

        assert rule["rule_id"] == "RULE_AGEC_BIODEGRADABLE"
        assert rule["jurisdiction"] == "FR"
        assert rule["legal_status"] == "in_force"
        assert len(rule["official_citations"]) >= 2

        # Verify exact citations
        articles = [c["article"] for c in rule["official_citations"]]
        assert any("L. 541-9-1" in a for a in articles)
        assert any("R. 541-230" in a for a in articles)
        assert all("legifrance.gouv.fr" in c["url"] for c in rule["official_citations"])

        # Verify Sanctions ceiling
        assert rule["sanction"] is not None
        assert int(rule["sanction"]["max_legal_person_eur"]) == 15000

        # 2. Test EU Generic Claim Rule Detail
        res_eu = client.get("/api/v1/regulatory/rules/RULE_EU_GENERIC_CLAIM")
        assert res_eu.status_code == 200
        rule_eu = res_eu.json()
        assert rule_eu["jurisdiction"] == "EU"
        assert len(rule_eu["safe_harbors"]) >= 2
        assert any(sh["safe_harbor_id"] == "EU_ECOLABEL_RELEVANT" for sh in rule_eu["safe_harbors"])
        assert "2026-09-27" in str(rule_eu["effective_from"])
        assert rule_eu["incomplete_coverage_warning"] is not None

        # 3. Test Unknown Rule 404
        not_found = client.get("/api/v1/regulatory/rules/RULE_NON_EXISTENT")
        assert not_found.status_code == 404


def test_rulebook_changelog_and_diffs():
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")

        res = client.get("/api/v1/regulatory/changelog")
        assert res.status_code == 200, res.text
        changelog = res.json()

        assert len(changelog) >= 3
        v_latest = changelog[0]
        assert v_latest["version"] == "2026-09-24"
        assert any(item["rule_id"] == "RULE_EU_GENERIC_CLAIM" for item in v_latest["diff_items"])


def test_rule_governance_review_rbac():
    with TestClient(app) as client, TestClient(app) as viewer_client:
        authenticate_client(client, role_code="admin")
        authenticate_client(viewer_client, role_code="viewer")

        # 1. Viewer cannot submit a review (lacks rules:manage)
        viewer_res = viewer_client.post(
            "/api/v1/regulatory/rules/RULE_AGEC_BIODEGRADABLE/review",
            json={
                "review_status": "approved_legal",
                "confidence_level": "high",
                "legal_notes": "Revue trimestrielle effectuée.",
            },
        )
        assert viewer_res.status_code == 403

        # 2. Admin can submit a review with CSRF
        admin_res = client.post(
            "/api/v1/regulatory/rules/RULE_AGEC_BIODEGRADABLE/review",
            json={
                "review_status": "approved_legal",
                "confidence_level": "high",
                "legal_notes": "Audit de conformité semestriel validé.",
            },
        )
        assert admin_res.status_code == 200, admin_res.text
        data = admin_res.json()
        assert data["governance_review"]["legal_notes"] == "Audit de conformité semestriel validé."
        assert data["governance_review"]["review_status"] == "approved_legal"
