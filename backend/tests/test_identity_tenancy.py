from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, settings
from app.core.database import AuditRecord, SessionLocal
from app.identity.oidc import OidcClient, OidcIdentity, OidcLoginTransaction, OidcMetadata, OidcProtocolError
from app.identity.roles import ensure_system_roles
from app.identity.service import provision_oidc_user
from app.identity.security import CSRF_HEADER_NAME, AuthenticationStateError, read_login_transaction, sign_login_transaction
from app.models.domain import AuditEvent, AuthSession, Membership, MembershipStatus, Organization, Role, User, UserStatus
from tests.auth_support import authenticate_client, authenticate_client_without_membership


ENGINE_PAYLOAD = {
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


def test_engine_rejects_anonymous_and_viewer_sessions():
    from app.main import app

    with TestClient(app) as anonymous:
        response = anonymous.post("/api/v1/engine/evaluate", json=ENGINE_PAYLOAD)
        assert response.status_code == 401

    with TestClient(app) as viewer:
        authenticate_client(viewer, role_code="viewer")
        assert viewer.get("/api/v1/engine/rules").status_code == 200
        response = viewer.post("/api/v1/engine/evaluate", json=ENGINE_PAYLOAD)
        assert response.status_code == 403


def test_cookie_authenticated_mutations_require_a_matching_csrf_header():
    from app.main import app

    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")
        client.headers.pop(CSRF_HEADER_NAME, None)
        rejected = client.post("/api/v1/engine/evaluate", json=ENGINE_PAYLOAD)
        assert rejected.status_code == 403
        assert "CSRF" in rejected.json()["detail"]


def test_cors_preflight_allows_the_csrf_header_for_the_local_frontend():
    from app.main import app

    with TestClient(app) as client:
        response = client.options(
            "/api/v1/engine/evaluate",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-csrf-token, idempotency-key",
            },
        )
        assert response.status_code == 200
        assert "x-csrf-token" in response.headers["access-control-allow-headers"].lower()
        assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()
        allowed_methods = response.headers["access-control-allow-methods"].upper()
        assert "PATCH" in allowed_methods and "DELETE" in allowed_methods


def test_authenticated_analysis_is_scoped_and_hash_chained_per_organization():
    from app.main import app

    with TestClient(app) as client:
        first_identity = authenticate_client(client, role_code="owner")
        first_response = client.post("/api/v1/engine/evaluate", json=ENGINE_PAYLOAD)
        assert first_response.status_code == 200, first_response.text
        first_audit_id = first_response.json()["audit_trail"]["audit_id"]

        create_second_org = client.post(
            "/api/v1/organizations",
            json={"name": "Deuxième organisation de test"},
        )
        assert create_second_org.status_code == 201, create_second_org.text
        second_organization_id = UUID(create_second_org.json()["organization"]["id"])
        assert second_organization_id != first_identity.organization_id

        second_response = client.post("/api/v1/engine/evaluate", json=ENGINE_PAYLOAD)
        assert second_response.status_code == 200, second_response.text
        second_audit_id = second_response.json()["audit_trail"]["audit_id"]

        with SessionLocal() as db:
            first_record = db.scalar(select(AuditRecord).where(AuditRecord.audit_id == first_audit_id))
            second_record = db.scalar(select(AuditRecord).where(AuditRecord.audit_id == second_audit_id))
            assert first_record is not None and second_record is not None
            assert first_record.organization_id == first_identity.organization_id
            assert second_record.organization_id == second_organization_id
            # The first record in each organisation starts its own chain.
            assert first_record.previous_record_hash is None
            assert second_record.previous_record_hash is None
            event = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == second_organization_id,
                    AuditEvent.action == "regulatory_audit.completed",
                )
            )
            assert event is not None
            assert event.actor_user_id == first_identity.user_id


def test_organization_onboarding_and_member_rbac_are_enforced():
    from app.main import app

    with TestClient(app) as client:
        authenticate_client_without_membership(client)
        assert client.get("/api/v1/organizations/current").status_code == 409

        created = client.post("/api/v1/organizations", json={"name": "Atelier RSE", "slug": "atelier-rse"})
        assert created.status_code == 201, created.text
        assert created.json()["role"]["code"] == "owner"
        assert client.get("/api/v1/organizations/current").json()["slug"] == "atelier-rse"

        invitation = client.post(
            "/api/v1/organizations/current/members/invitations",
            json={"email": "future.analyst@example.com", "role_code": "analyst"},
        )
        assert invitation.status_code == 201, invitation.text
        assert invitation.json()["status"] == "invited"
        assert invitation.json()["delivery_status"] == "not_sent"

        members = client.get("/api/v1/organizations/current/members")
        assert members.status_code == 200
        assert {member["role"]["code"] for member in members.json()} >= {"owner", "analyst"}

    with TestClient(app) as analyst:
        authenticate_client(analyst, role_code="analyst")
        denied = analyst.post(
            "/api/v1/organizations/current/members/invitations",
            json={"email": "blocked@example.com", "role_code": "viewer"},
        )
        assert denied.status_code == 403


def test_last_owner_cannot_be_revoked_and_foreign_organization_cannot_be_selected():
    from app.main import app

    with TestClient(app) as owner_client, TestClient(app) as other_client, TestClient(app) as admin_client:
        owner = authenticate_client(owner_client, role_code="owner")
        other = authenticate_client(other_client, role_code="analyst")
        admin = authenticate_client(admin_client, role_code="admin")
        # Make the admin a member of the owner's organisation using the same
        # real session helper, then prove it cannot demote an existing owner.
        with SessionLocal() as db:
            admin_role = db.scalar(select(Role).where(Role.code == "admin"))
            admin_session = db.scalar(select(AuthSession).where(AuthSession.user_id == admin.user_id))
            assert admin_role is not None and admin_session is not None
            db.add(
                Membership(
                    organization_id=owner.organization_id,
                    user_id=admin.user_id,
                    role_id=admin_role.id,
                    status=MembershipStatus.ACTIVE,
                )
            )
            admin_session.active_organization_id = owner.organization_id
            db.commit()

        denied_owner_demotion = admin_client.patch(
            f"/api/v1/organizations/current/members/{owner.user_id}/role",
            json={"role_code": "analyst"},
        )
        assert denied_owner_demotion.status_code == 409

        revoke_self = owner_client.delete(f"/api/v1/organizations/current/members/{owner.user_id}")
        assert revoke_self.status_code == 409
        demote_self = owner_client.patch(
            f"/api/v1/organizations/current/members/{owner.user_id}/role",
            json={"role_code": "analyst"},
        )
        assert demote_self.status_code == 409

        switch_foreign = owner_client.post(
            "/api/v1/auth/active-organization",
            json={"organization_id": str(other.organization_id)},
        )
        assert switch_foreign.status_code == 404


def test_logout_revokes_opaque_session_server_side():
    from app.main import app

    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401


def test_verified_sso_identity_activates_preprovisioned_membership():
    with SessionLocal() as db:
        ensure_system_roles(db)
        analyst_role = db.scalar(select(Role).where(Role.code == "analyst"))
        assert analyst_role is not None
        organization = Organization(name="Organisation invitée", slug="organisation-invitee")
        invited = User(email="invited.sso@example.com", status=UserStatus.INVITED)
        db.add_all((organization, invited))
        db.flush()
        membership = Membership(
            organization_id=organization.id,
            user_id=invited.id,
            role_id=analyst_role.id,
            status=MembershipStatus.INVITED,
        )
        db.add(membership)
        db.flush()

        provisioned = provision_oidc_user(
            db,
            OidcIdentity(
                issuer="https://idp.example.test",
                subject="sso-subject-invited",
                email="invited.sso@example.com",
                display_name="Invited Analyst",
            ),
        )
        assert provisioned.user.id == invited.id
        assert provisioned.activated_memberships == (membership,)
        assert membership.status == MembershipStatus.ACTIVE
        assert membership.activated_at is not None
        db.rollback()


def test_oidc_callback_uses_signed_state_and_creates_an_opaque_session():
    from app.main import app

    class FakeOidcClient:
        transaction: OidcLoginTransaction | None = None

        def authorization_url(self, transaction: OidcLoginTransaction) -> str:
            self.transaction = transaction
            return f"https://idp.example.test/authorize?state={transaction.state}"

        def exchange_callback(self, *, code: str, transaction: OidcLoginTransaction) -> OidcIdentity:
            assert code == "approved-code"
            assert self.transaction == transaction
            return OidcIdentity(
                issuer="https://idp.example.test",
                subject="subject-from-provider",
                email="sso.user@example.com",
                display_name="SSO User",
            )

    previous_client = app.state.oidc_client
    fake_client = FakeOidcClient()
    app.state.oidc_client = fake_client
    try:
        with TestClient(app) as client:
            login = client.get("/api/v1/auth/login?return_to=//attacker.invalid", follow_redirects=False)
            assert login.status_code == 302
            assert fake_client.transaction is not None

            callback = client.get(
                f"/api/v1/auth/callback?code=approved-code&state={fake_client.transaction.state}",
                follow_redirects=False,
            )
            assert callback.status_code == 303
            assert callback.headers["location"] == f"{settings.frontend_url}/"
            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["user"]["email"] == "sso.user@example.com"
    finally:
        app.state.oidc_client = previous_client


def test_signed_login_state_and_oidc_algorithm_guards_fail_closed():
    transaction = OidcLoginTransaction(state="state", nonce="nonce", code_verifier="v" * 43, return_to="/")
    signed = sign_login_transaction(settings, transaction)
    assert read_login_transaction(settings, signed) == transaction
    with pytest.raises(AuthenticationStateError):
        read_login_transaction(settings, f"{signed}tampered")

    oidc_settings = Settings(
        environment="test",
        oidc_issuer="https://idp.example.com",
        oidc_client_id="client-id",
        oidc_redirect_uri="https://app.example.com/api/v1/auth/callback",
        oidc_discovery_url="https://idp.example.com/.well-known/openid-configuration",
    )
    client = OidcClient(oidc_settings)
    unsigned = jwt.encode(
        {"sub": "subject", "email": "user@example.com", "email_verified": True},
        key="",
        algorithm="none",
    )
    with pytest.raises(OidcProtocolError):
        client._validate_id_token(  # noqa: SLF001 - a security boundary requires a direct regression test.
            unsigned,
            "nonce",
            OidcMetadata(
                issuer="https://idp.example.com",
                authorization_endpoint="https://idp.example.com/authorize",
                token_endpoint="https://idp.example.com/token",
                jwks_uri="https://idp.example.com/keys",
            ),
        )


def test_oidc_client_verifies_a_real_asymmetric_id_token_signature():
    issuer = "https://idp.example.com"
    jwks_uri = f"{issuer}/keys"
    metadata = OidcMetadata(
        issuer=issuer,
        authorization_endpoint=f"{issuer}/authorize",
        token_endpoint=f"{issuer}/token",
        jwks_uri=jwks_uri,
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})

    class JwksHttpClient:
        def get(self, url: str, **_: object) -> httpx.Response:
            assert url == jwks_uri
            return httpx.Response(200, json={"keys": [public_jwk]}, request=httpx.Request("GET", url))

    oidc_settings = Settings(
        environment="test",
        oidc_issuer=issuer,
        oidc_client_id="client-id",
        oidc_redirect_uri="https://app.example.com/api/v1/auth/callback",
        oidc_discovery_url=f"{issuer}/.well-known/openid-configuration",
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": "client-id",
            "sub": "verified-subject",
            "email": "verified.user@example.com",
            "email_verified": True,
            "nonce": "expected-nonce",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    identity = OidcClient(oidc_settings, http_client=JwksHttpClient())._validate_id_token(  # noqa: SLF001
        token,
        "expected-nonce",
        metadata,
    )
    assert identity.subject == "verified-subject"
    assert identity.email == "verified.user@example.com"
