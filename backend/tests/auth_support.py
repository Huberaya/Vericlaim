from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.identity.roles import ensure_system_roles
from app.identity.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from app.identity.service import create_auth_session
from app.models.domain import Membership, MembershipStatus, Organization, Role, User, UserStatus


@dataclass(frozen=True)
class TestIdentity:
    user_id: UUID
    organization_id: UUID
    role_code: str
    session_token: str


def authenticate_client(client: TestClient, *, role_code: str = "analyst", email: str | None = None) -> TestIdentity:
    """Create a real opaque server session; no runtime auth bypass is used."""
    suffix = uuid4().hex
    with SessionLocal() as db:
        ensure_system_roles(db)
        role = db.scalar(select(Role).where(Role.code == role_code))
        assert role is not None
        user = User(
            email=email or f"{role_code}.{suffix}@example.com",
            display_name=f"Test {role_code}",
            status=UserStatus.ACTIVE,
            identity_provider="https://idp.example.test",
            external_subject=f"subject-{suffix}",
        )
        organization = Organization(name=f"Organisation {suffix[:8]}", slug=f"org-{suffix[:16]}")
        db.add_all((user, organization))
        db.flush()
        db.add(
            Membership(
                organization_id=organization.id,
                user_id=user.id,
                role_id=role.id,
                status=MembershipStatus.ACTIVE,
            )
        )
        db.flush()
        raw_token, raw_csrf_token, _ = create_auth_session(
            db,
            user=user,
            settings=settings,
            request_user_agent="pytest",
        )
        db.commit()

    client.cookies.set(SESSION_COOKIE_NAME, raw_token)
    client.cookies.set(CSRF_COOKIE_NAME, raw_csrf_token)
    client.headers[CSRF_HEADER_NAME] = raw_csrf_token
    return TestIdentity(
        user_id=user.id,
        organization_id=organization.id,
        role_code=role_code,
        session_token=raw_token,
    )


def authenticate_client_without_membership(client: TestClient, *, email: str | None = None) -> TestIdentity:
    """Create an authenticated SSO-provisioned user before organisation onboarding."""
    suffix = uuid4().hex
    with SessionLocal() as db:
        user = User(
            email=email or f"new-user.{suffix}@example.com",
            display_name="New user",
            status=UserStatus.ACTIVE,
            identity_provider="https://idp.example.test",
            external_subject=f"subject-{suffix}",
        )
        db.add(user)
        db.flush()
        raw_token, raw_csrf_token, _ = create_auth_session(
            db,
            user=user,
            settings=settings,
            request_user_agent="pytest",
        )
        db.commit()
    client.cookies.set(SESSION_COOKIE_NAME, raw_token)
    client.cookies.set(CSRF_COOKIE_NAME, raw_csrf_token)
    client.headers[CSRF_HEADER_NAME] = raw_csrf_token
    # This sentinel is never sent to the API; callers assert onboarding state.
    return TestIdentity(user_id=user.id, organization_id=UUID(int=0), role_code="none", session_token=raw_token)
