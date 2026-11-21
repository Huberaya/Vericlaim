"""E-07 — l'empreinte User-Agent de session est contraignante, pas décorative.

Elle était enregistrée à la création puis jamais comparée : un cookie volé
servait de n'importe quel outil. La liaison est désormais vérifiée à chaque
résolution ; un User-Agent différent rend le jeton inutilisable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, create_tables
from app.identity.security import SESSION_COOKIE_NAME
from app.identity.service import create_auth_session, resolve_current_session
from app.models.domain import Membership, MembershipStatus, Organization, Role, User, UserStatus
from app.identity.roles import ensure_system_roles
from sqlalchemy import select


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def _fresh_session(raw_user_agent: str | None):
    from uuid import uuid4

    suffix = uuid4().hex
    with SessionLocal() as db:
        ensure_system_roles(db)
        role = db.scalar(select(Role).where(Role.code == "analyst"))
        user = User(
            email=f"ua.{suffix}@example.com",
            status=UserStatus.ACTIVE,
            identity_provider="https://idp.example.test",
            external_subject=f"subject-{suffix}",
        )
        organization = Organization(name=f"Org UA {suffix[:8]}", slug=f"org-ua-{suffix[:16]}")
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
        raw_token, _raw_csrf, _session = create_auth_session(
            db,
            user=user,
            settings=settings,
            request_user_agent=raw_user_agent,
        )
        db.commit()
    return raw_token


def test_session_resolves_only_with_its_bound_user_agent():
    raw_token = _fresh_session("pytest-agent/1.0")
    with SessionLocal() as db:
        assert resolve_current_session(
            db, raw_token=raw_token, settings=settings, request_user_agent="pytest-agent/1.0"
        ) is not None
        # Un outil qui rejoue le cookie sans rejouer l'en-tête est refusé.
        assert (
            resolve_current_session(db, raw_token=raw_token, settings=settings, request_user_agent="autre/2.0")
            is None
        )
        # L'égalité est stricte : l'absence ne remplace pas un User-Agent.
        assert resolve_current_session(db, raw_token=raw_token, settings=settings, request_user_agent=None) is None


def test_session_created_without_user_agent_needs_none_too():
    raw_token = _fresh_session(None)
    with SessionLocal() as db:
        assert resolve_current_session(db, raw_token=raw_token, settings=settings, request_user_agent=None) is not None
        assert (
            resolve_current_session(db, raw_token=raw_token, settings=settings, request_user_agent="curl/8")
            is None
        )


def test_http_session_dies_when_the_user_agent_changes():
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        from tests.auth_support import authenticate_client

        authenticate_client(client, role_code="analyst")
        assert client.get("/api/v1/auth/me").status_code == 200

        # Vol de cookie simulé : même jeton, autre client (autre User-Agent).
        stolen = TestClient(fastapi_app)
        stolen.cookies.set(SESSION_COOKIE_NAME, client.cookies.get(SESSION_COOKIE_NAME))
        assert stolen.get("/api/v1/auth/me").status_code == 401
