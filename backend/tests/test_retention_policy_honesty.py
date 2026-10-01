"""C8 acceptance: no invented retention, encryption or hosting values.

`GET /api/v1/pilot/retention-policy` used to return the same literals to every
tenant — `dpo@vericlaim.ai`, `AES-256 / TLS 1.3`, `EU (Paris / Frankfurt)`, 5 and
10 years, reviewed on 2026-09-24 — presented as a compliance artefact. None of
those were agreed with any client, and a hosting region published by the vendor is
a contractual commitment nobody made.

These tests fail if any fabricated value comes back, whatever the route.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal, create_tables
from app.main import app
from app.models.domain import OrganizationRetentionPolicy
from tests.auth_support import authenticate_client

# The exact literals the endpoint used to answer, for every tenant.
FABRICATED_VALUES = (
    "dpo@vericlaim.ai",
    "AES-256 / TLS 1.3",
    "EU (Paris / Frankfurt)",
    "2026-09-24",
)

RETENTION_PATH = "/api/v1/pilot/retention-policy"


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def _undeclared_policy(client: TestClient, identity) -> dict:
    response = client.get(RETENTION_PATH)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_organization_that_declared_nothing_gets_nothing():
    """Silence must not be dressed up as a policy."""
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        payload = _undeclared_policy(client, identity)

        assert payload["configured"] is False
        assert payload["documents_retention_years"] is None
        assert payload["audit_trail_retention_years"] is None
        assert payload["evidence_archive_retention_years"] is None
        assert payload["gdpr_contact_email"] is None
        assert payload["encryption_standard"] is None
        assert payload["storage_region"] is None
        assert payload["last_policy_review"] is None
        assert "non contractuel" in payload["status"]
        # Deux affirmations distinctes, et il faut les deux : rien ne supprime sans
        # intervention (aucun ordonnanceur n'est livré), mais la suppression existe
        # depuis C20. Une réponse qui nierait la seconde serait fausse.
        assert payload["automatic_deletion_implemented"] is False
        assert payload["deletion_available"] is True
        assert payload["deletion_job"] == "python -m app.privacy.purge_job"
        assert payload["enforcement_endpoint"] == "/api/v1/privacy/retention"
        # The missing fields are named, so an absence cannot read as an approval.
        assert set(payload["undeclared_fields"]) >= {
            "documents_retention_years",
            "audit_trail_retention_years",
            "evidence_archive_retention_years",
            "gdpr_contact_email",
            "encryption_standard",
            "storage_region",
            "last_policy_review",
        }


def test_no_fabricated_compliance_value_is_ever_returned():
    """The four literal values that made this endpoint false, and nowhere to hide."""
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        response = client.get(RETENTION_PATH)
        body = response.text
        for fabricated in FABRICATED_VALUES:
            assert fabricated not in body, f"valeur inventée de retour : {fabricated}"


def test_the_hosting_region_is_not_asserted_by_the_platform():
    """A region is an infrastructure decision, not a constant in the response schema."""
    from app.models.pilot_schemas import RetentionPolicyResponse

    defaults = RetentionPolicyResponse.model_fields
    for field in ("storage_region", "encryption_standard", "gdpr_contact_email"):
        assert defaults[field].default is None, (
            f"{field} porte une valeur par défaut : une organisation qui n'a rien "
            "déclaré se verrait attribuer un engagement qu'elle n'a pas pris"
        )


def test_a_declared_policy_is_returned_with_its_author():
    """When a client does declare one, the declaration is stored and attributed."""
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        declared = {
            "documents_retention_years": 7,
            "audit_trail_retention_years": 12,
            "evidence_archive_retention_years": 7,
            "gdpr_contact_email": "dpo@client.example",
            "storage_region": "Union européenne — région déclarée par le client",
            "last_policy_review": "2026-09-15",
        }
        response = client.put(RETENTION_PATH, json=declared)
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["configured"] is True
        assert payload["documents_retention_years"] == 7
        assert payload["audit_trail_retention_years"] == 12
        assert payload["gdpr_contact_email"] == "dpo@client.example"
        assert payload["storage_region"] == "Union européenne — région déclarée par le client"
        assert payload["last_policy_review"] == "2026-09-15"
        assert payload["declared_by_user_id"] is not None
        assert payload["declared_at"] is not None
        # A declaration is not an enforcement: nothing deletes on a schedule here.
        assert payload["automatic_deletion_implemented"] is False
        assert "documents_retention_years" not in payload["undeclared_fields"]
        # …et la réponse dit désormais où la politique déclarée *est* appliquée, au lieu
        # d'affirmer que la plateforme n'efface rien (ce qui était faux depuis C20).
        assert any("privacy/purge" in line for line in payload["limitations"]), payload["limitations"]
        assert any(
            "piste d'audit n'est jamais appliquée" in line for line in payload["limitations"]
        ), payload["limitations"]


def test_a_declaration_does_not_leak_to_another_organization():
    """Retention is per-organization data, not a global setting."""
    with TestClient(app) as client_a:
        identity_a = authenticate_client(client_a, role_code="owner")
        declared = client_a.put(RETENTION_PATH, json={"documents_retention_years": 42})
        assert declared.status_code == 200, declared.text

    with TestClient(app) as client_b:
        identity_b = authenticate_client(client_b, role_code="owner")
        assert identity_b.organization_id != identity_a.organization_id
        payload = _undeclared_policy(client_b, identity_b)
        assert payload["configured"] is False
        assert payload["documents_retention_years"] is None

    # And the declaration really belongs to organization A in the database.
    with SessionLocal() as db:
        rows = list(db.scalars(select(OrganizationRetentionPolicy)).all())
    assert any(
        row.organization_id == identity_a.organization_id
        and row.documents_retention_years == 42
        for row in rows
    )
    assert all(row.organization_id != identity_b.organization_id for row in rows)


def test_declaring_a_policy_requires_the_management_permission():
    """Writing a retention commitment is an organizational act, not a read."""
    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")
        response = client.put(RETENTION_PATH, json={"documents_retention_years": 5})
        assert response.status_code in (401, 403), response.text


def test_a_partial_declaration_does_not_erase_what_was_already_declared():
    """A partial update must not silently drop an agreed duration."""
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        first = client.put(
            RETENTION_PATH,
            json={"documents_retention_years": 6, "gdpr_contact_email": "dpo@client.example"},
        )
        assert first.status_code == 200, first.text

        second = client.put(RETENTION_PATH, json={"audit_trail_retention_years": 11})
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["documents_retention_years"] == 6, "durée déclarée perdue"
        assert payload["gdpr_contact_email"] == "dpo@client.example"
        assert payload["audit_trail_retention_years"] == 11


def test_an_incoherent_declaration_is_refused():
    """A zero-year or negative retention is not a policy, it is a mistake."""
    with TestClient(app) as client:
        authenticate_client(client, role_code="owner")
        for invalid in ({"documents_retention_years": 0}, {"documents_retention_years": -3}, {"gdpr_contact_email": "pas-un-email"}):
            response = client.put(RETENTION_PATH, json=invalid)
            assert response.status_code == 422, f"{invalid} accepté : {response.text}"


def test_the_export_formats_are_a_fact_about_the_code_not_a_declaration():
    """Only list what the platform can actually produce."""
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="owner")
        payload = _undeclared_policy(client, identity)
        assert payload["export_formats_supported"] == ["JSON", "CSV", "AUDIT_ZIP"]
        assert "export_formats_supported" not in payload["undeclared_fields"]
