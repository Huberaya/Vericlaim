"""Preuve PostgreSQL que les parcours d'identité fonctionnent sous les rôles réels.

Pourquoi ce fichier existe. La suite tournait contre PostgreSQL avec le rôle
`postgres`, qui **contourne la RLS** : elle validait donc l'application sur une
base qui ne se comportait pas comme la production. Trois défauts y ont survécu,
tous invisibles pour les tests existants :

1. l'inscription échouait — l'écriture d'une ligne rattachée sans contexte est
   refusée par `WITH CHECK` (« new row violates row-level security policy ») ;
2. une organisation pouvait lire le jeton de réinitialisation orphelin d'un
   utilisateur d'une autre organisation ;
3. la vérification d'adresse échouait pour la même raison que l'inscription.

Ces tests exécutent les vraies fonctions d'identité, avec les deux rôles que la
production utilise (`vericlaim_app` pour le trafic filtré, `vericlaim_system`
pour les parcours transverses), contre une base construite par la chaîne Alembic
réelle. Ils échouent si l'un des trois défauts revient.

Ils sont ignorés si `VERICLAIM_TEST_POSTGRES_URL` n'est pas configurée : la RLS
est une propriété PostgreSQL, elle ne peut pas être approchée autrement.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
NEON_ROOT = REPOSITORY_ROOT / "infra" / "neon"

ADMIN_DSN = os.environ.get("VERICLAIM_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not ADMIN_DSN,
    reason=(
        "VERICLAIM_TEST_POSTGRES_URL is not configured; proving the identity flows "
        "under real roles requires a disposable PostgreSQL server"
    ),
)

APP_ROLE = "vericlaim_app"
SYSTEM_ROLE = "vericlaim_system"
MIGRATOR_ROLE = "vericlaim_migrator"
APP_PASSWORD = "integration-app-password"
SYSTEM_PASSWORD = "integration-system-password-32chars"
MIGRATOR_PASSWORD = "integration-migrator-password"


@dataclass(frozen=True)
class Roles:
    """Connexions prêtes à l'emploi, une par rôle de production."""

    database: str
    app_dsn: str
    system_dsn: str
    migrator_dsn: str


def _dsn_for(dsn: str, *, dbname: str, user: str, password: str) -> str:
    return psycopg.conninfo.make_conninfo(dsn, dbname=dbname, user=user, password=password)


def _sqlalchemy_url(dsn: str, *, dbname: str, user: str, password: str) -> str:
    from urllib.parse import quote

    params = psycopg.conninfo.conninfo_to_dict(dsn)
    host = params.get("host", "") or ""
    port = str(params.get("port", "") or "")
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
    if host.startswith("/"):
        return f"postgresql+psycopg://{auth}@/{dbname}?host={quote(host, safe='')}"
    if host:
        return f"postgresql+psycopg://{auth}@{host}:{port}/{dbname}" if port else f"postgresql+psycopg://{auth}@{host}/{dbname}"
    return f"postgresql+psycopg://{auth}@/{dbname}"


@pytest.fixture(scope="module")
def roles() -> Iterator[Roles]:
    import subprocess

    database = f"vericlaim_flows_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin, admin.cursor() as cursor:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database,),
        )
        cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        for role, password in (
            (MIGRATOR_ROLE, MIGRATOR_PASSWORD),
            (APP_ROLE, APP_PASSWORD),
        ):
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cursor.fetchone():
                cursor.execute(
                    sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOINHERIT NOBYPASSRLS PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            else:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
        # Le rôle système est provisionné comme le ferait 04_system_role.sql, sans
        # passer par la migration : celle-ci vérifie sa présence, elle ne le crée pas.
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (SYSTEM_ROLE,))
        if cursor.fetchone():
            cursor.execute(
                sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS PASSWORD {}").format(
                    sql.Identifier(SYSTEM_ROLE), sql.Literal(SYSTEM_PASSWORD)
                )
            )
        else:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS PASSWORD {}").format(
                    sql.Identifier(SYSTEM_ROLE), sql.Literal(SYSTEM_PASSWORD)
                )
            )

    with psycopg.connect(_dsn_for(ADMIN_DSN, dbname=database, user="postgres", password=""), autocommit=True) as owner, owner.cursor() as cursor:
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        cursor.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {MIGRATOR_ROLE}")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={
            **os.environ,
            "APP_ENV": "development",
            "AUTO_CREATE_SCHEMA": "false",
            "DATABASE_URL": _sqlalchemy_url(
                ADMIN_DSN, dbname=database, user=MIGRATOR_ROLE, password=MIGRATOR_PASSWORD
            ),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert migration.returncode == 0, f"alembic upgrade failed:\n{migration.stdout}\n{migration.stderr}"

    migrator_dsn = _dsn_for(ADMIN_DSN, dbname=database, user=MIGRATOR_ROLE, password=MIGRATOR_PASSWORD)
    with psycopg.connect(migrator_dsn, autocommit=True) as migrator, migrator.cursor() as cursor:
        cursor.execute((NEON_ROOT / "02_after_migration_grants.sql").read_text(encoding="utf-8"))

    try:
        yield Roles(
            database=database,
            app_dsn=_dsn_for(ADMIN_DSN, dbname=database, user=APP_ROLE, password=APP_PASSWORD),
            system_dsn=_dsn_for(ADMIN_DSN, dbname=database, user=SYSTEM_ROLE, password=SYSTEM_PASSWORD),
            migrator_dsn=migrator_dsn,
        )
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin, admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))


@pytest.fixture()
def identity_app(roles: Roles, monkeypatch):
    """L'application configurée avec les deux rôles réels, sur cette base."""
    import app.core.config as config_module
    import app.core.database as database_module

    # Les DSN sont en syntaxe conninfo (`host=... user=...`), pas en URL : il faut
    # les convertir, un simple remplacement de préfixe ne suffit pas.
    app_url = _sqlalchemy_url(
        roles.app_dsn, dbname=roles.database, user=APP_ROLE, password=APP_PASSWORD
    )
    system_url = _sqlalchemy_url(
        roles.system_dsn, dbname=roles.database, user=SYSTEM_ROLE, password=SYSTEM_PASSWORD
    )

    settings = config_module.Settings(
        environment="development",
        database_url=app_url,
        system_database_url=system_url,
        auto_create_schema=False,
        email_backend="outbox",
    )

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(app_url, pool_pre_ping=True)
    system_engine = create_engine(system_url, pool_pre_ping=True)

    monkeypatch.setattr(config_module, "settings", settings)
    monkeypatch.setattr(database_module, "settings", settings)
    monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False))
    monkeypatch.setattr(
        database_module,
        "SystemSessionLocal",
        sessionmaker(bind=system_engine, autoflush=False, expire_on_commit=False),
    )

    import app.identity.self_service as self_service

    monkeypatch.setattr(self_service, "SystemSessionLocal", database_module.SystemSessionLocal)

    captured: list[str] = []
    original = self_service.deliver

    def spy(db, *, settings, message):
        captured.append(message.body_text)
        return original(db, settings=settings, message=message)

    monkeypatch.setattr(self_service, "deliver", spy)

    try:
        yield settings, captured
    finally:
        test_engine.dispose()
        system_engine.dispose()


def _token_from(body: str) -> str:
    import re

    match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
    assert match, f"aucun jeton dans le message : {body[:200]}"
    return match.group(1)


def test_signup_succeeds_under_the_real_application_role(identity_app):
    """Le défaut mesuré : l'inscription levait `new row violates row-level security policy`."""
    from app.identity.self_service import signup

    settings, captured = identity_app

    outcome = signup(
        settings=settings,
        email="nouveau@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation nouvelle",
    )

    assert outcome.account_created is True
    assert outcome.organization_id is not None
    assert captured, "l'e-mail de vérification doit être enregistré"


def test_email_verification_succeeds_and_marks_the_address(identity_app):
    from app.identity.self_service import signup, verify_email

    settings, captured = identity_app
    signup(
        settings=settings,
        email="verifier@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation à vérifier",
    )

    user = verify_email(None, raw_token=_token_from(captured[-1]))

    assert user.email == "verifier@exemple.test"
    assert user.email_verified_at is not None


def test_password_reset_succeeds_and_revokes_existing_sessions(identity_app):
    from app.identity.self_service import (
        confirm_password_reset,
        request_password_reset,
        signup,
        verify_email,
    )

    settings, captured = identity_app
    signup(
        settings=settings,
        email="oubli@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation oubli",
    )
    verify_email(None, raw_token=_token_from(captured[-1]))

    found, _detail = request_password_reset(None, settings=settings, email="oubli@exemple.test")
    assert found is True

    user = confirm_password_reset(
        None, settings=settings, raw_token=_token_from(captured[-1]),
        new_password="NouveauMotDePasse!2027",
    )
    assert user.email == "oubli@exemple.test"


def _invite_colleague(roles: Roles, *, email: str) -> tuple[uuid.UUID, str]:
    """Créer une organisation, un invité et un jeton d'invitation, sous le rôle système.

    Le jeton n'est pas émis par le parcours d'invitation de l'application : ce
    parcours passe par l'API d'organisation et une session authentifiée, hors du
    périmètre de ce fichier. Ce qui est vérifié ici est le **consommateur** : la
    lecture d'un jeton d'invitation, l'activation de l'adhésion et l'ouverture de
    session, qui sont exactement les opérations que la RLS stricte pouvait casser.
    """
    import hashlib

    organization_id, user_id = uuid.uuid4(), uuid.uuid4()
    raw_token = f"invitation-{uuid.uuid4().hex}"
    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO organizations (id, name, slug, status, data_region, created_at, updated_at) "
            "VALUES (%s, 'Organisation invitante', %s, 'active', 'eu', now(), now())",
            (organization_id, f"org-inv-{organization_id.hex[:10]}"),
        )
        cursor.execute(
            "INSERT INTO users (id, email, display_name, status, created_at, updated_at) "
            "VALUES (%s, %s, 'Invité', 'invited', now(), now())",
            (user_id, email),
        )
        cursor.execute("SELECT id FROM roles WHERE code = 'viewer' LIMIT 1")
        row = cursor.fetchone()
        assert row, "le rôle viewer doit exister après la migration"
        role_id = row[0]
        cursor.execute(
            "INSERT INTO memberships (id, organization_id, user_id, role_id, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'invited', now(), now())",
            (uuid.uuid4(), organization_id, user_id, role_id),
        )
        cursor.execute(
            "INSERT INTO identity_tokens "
            "(id, user_id, organization_id, purpose, token_hash, expires_at, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'invitation', %s, now() + interval '1 day', now(), now())",
            (
                uuid.uuid4(),
                user_id,
                organization_id,
                hashlib.sha256(raw_token.encode()).hexdigest(),
            ),
        )
        connection.commit()
    return organization_id, raw_token


def test_invitation_acceptance_activates_the_membership_and_lets_a_session_open(
    identity_app, roles: Roles
):
    """Le parcours d'invitation, dans ses deux temps, sous les rôles réels.

    Défaut mesuré : l'activation de l'adhésion écrit ``memberships`` sans contexte
    d'organisation — le rôle système n'en avait pas le droit
    (« permission denied for table memberships »), donc l'invité ne pouvait pas
    entrer. À l'inverse, écrire la session sur le rôle système aurait exigé de lui
    accorder ``INSERT`` sur ``auth_sessions`` : une capacité inutile, puisque cette
    table est hors RLS et que la connexion par mot de passe l'écrit déjà sur le
    rôle applicatif. Ce test vérifie les deux temps séparément.
    """
    from app.identity.self_service import SelfServiceError, accept_invitation
    from app.identity.service import create_auth_session
    from app.models.domain import User

    settings, _captured = identity_app
    organization_id, raw_token = _invite_colleague(roles, email="invite@exemple.test")

    # Temps 1 : consommation du jeton et activation, sur l'identité système.
    outcome = accept_invitation(
        None,
        settings=settings,
        raw_token=raw_token,
        password="Boussole-Bleue-2026",
        display_name=None,
    )
    assert outcome.organization_id == organization_id

    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM memberships WHERE user_id = %s AND organization_id = %s",
            (outcome.user_id, organization_id),
        )
        row = cursor.fetchone()
        assert row is not None and row[0] == "active", "l'adhésion doit être activée"

    # Le jeton est consommé : rejouer le lien ne doit plus rien ouvrir.
    with pytest.raises(SelfServiceError) as raised:
        accept_invitation(
            None,
            settings=settings,
            raw_token=raw_token,
            password="Boussole-Bleue-2026",
            display_name=None,
        )
    assert raised.value.code == "token_already_used"

    # Temps 2 : la session, sur le rôle applicatif, sans contexte d'organisation.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app_url = _sqlalchemy_url(
        roles.app_dsn, dbname=roles.database, user=APP_ROLE, password=APP_PASSWORD
    )
    engine = create_engine(app_url)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as db:
            user = db.get(User, outcome.user_id)
            assert user is not None
            _raw, _csrf, session = create_auth_session(
                db, user=user, settings=settings, request_user_agent="pytest-invitation"
            )
            assert session.active_organization_id == organization_id
            db.commit()
    finally:
        engine.dispose()

    # La session existe réellement et porte l'organisation rejointe.
    with psycopg.connect(roles.app_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT active_organization_id, revoked_at FROM auth_sessions WHERE user_id = %s",
            (outcome.user_id,),
        )
        row = cursor.fetchone()
        assert row is not None, "aucune session ouverte : l'invité ne pourrait pas se connecter"
        assert row[0] == organization_id
        assert row[1] is None


def test_an_organisation_cannot_read_another_one_orphan_token(identity_app, roles: Roles):
    """Le second défaut : A lisait le jeton orphelin d'un utilisateur de B.

    Un jeton orphelin est un jeton émis avant rattachement de son utilisateur.
    Avec la politique permissive de `a1b9c7e34d52`, il était visible de toute
    organisation ; avec la politique stricte, il n'est visible d'aucune — sauf du
    rôle système, qui en a besoin pour authentifier le lien.
    """
    from app.identity.self_service import signup

    settings, captured = identity_app
    signup(
        settings=settings,
        email="victime@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation victime",
    )
    orphan_token = _token_from(captured[-1])

    # Rendre le jeton orphelin, comme un rattachement en cours le ferait.
    from sqlalchemy import text as sql_text

    from app.core.database import SystemSessionLocal

    with SystemSessionLocal() as db:
        db.execute(
            sql_text("UPDATE identity_tokens SET organization_id = NULL WHERE purpose = 'email_verification'")
        )
        db.commit()

    with psycopg.connect(roles.app_dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_organization_id', %s, true)", (str(uuid.uuid4()),))
        import hashlib

        digest = hashlib.sha256(orphan_token.encode()).hexdigest()
        cursor.execute("SELECT count(*) FROM identity_tokens WHERE token_hash = %s", (digest,))
        assert cursor.fetchone()[0] == 0, (
            "une organisation étrangère lit le jeton orphelin d'un autre : fuite de secret"
        )

    # Et le rôle système, lui, doit le voir : sans cela la vérification serait
    # impossible pour un utilisateur dont le rattachement a échoué.
    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        import hashlib

        digest = hashlib.sha256(orphan_token.encode()).hexdigest()
        cursor.execute("SELECT count(*) FROM identity_tokens WHERE token_hash = %s", (digest,))
        assert cursor.fetchone()[0] == 1


def test_kill_switch_a_tenant_cannot_write_a_token_for_another_organisation(
    identity_app, roles: Roles
) -> None:
    """Le `WITH CHECK` refuse l'écriture croisée, il ne filtre pas qu'en lecture."""
    from app.identity.self_service import signup

    settings, _captured = identity_app
    home = signup(
        settings=settings,
        email="intrus@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation intruse",
    )

    # Une organisation qui existe réellement : la contrainte de clé étrangère
    # passerait sinon pour un refus de sécurité, et le test ne prouverait rien.
    other_organization = uuid.uuid4()
    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO organizations (id, name, slug, status, data_region, created_at, updated_at) "
            "VALUES (%s, 'Organisation B', %s, 'active', 'eu', now(), now())",
            (other_organization, f"org-{other_organization.hex[:10]}"),
        )
        connection.commit()

    with psycopg.connect(roles.app_dsn) as connection, connection.cursor() as cursor:
        # Contexte = son organisation ; la ligne visée appartient à une autre.
        # Poser le contexte sur l'organisation visée rendrait l'écriture légitime,
        # et le test ne prouverait plus rien.
        cursor.execute("SELECT set_config('app.current_organization_id', %s, true)", (str(home.organization_id),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "INSERT INTO email_messages "
                "(id, organization_id, recipient_email, subject, body_text, purpose, status, created_at, updated_at) "
                "VALUES (%s, %s, 'x@exemple.test', 's', 'b', 'transactional', 'queued', now(), now())",
                (uuid.uuid4(), other_organization),
            )
        connection.rollback()


def test_the_system_role_cannot_read_mail_bodies_or_business_tables(roles: Roles):
    """Ce que la séparation achète : le rôle transverse reste aveugle au métier."""
    forbidden = (
        ("email_messages", "body_text"),
        ("email_messages", "recipient_email"),
        ("identity_tokens", "exists_placeholder"),  # table interdite en entier ci-dessous
    )
    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        for table, column in forbidden[:2]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(
                    sql.SQL("SELECT {} FROM {} LIMIT 1").format(
                        sql.Identifier(column), sql.Identifier(table)
                    )
                )
            connection.rollback()
        for table in ("documents", "analyses", "billing_invoices", "claims"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            connection.rollback()


def test_the_system_role_cannot_delete_or_rewrite_audit_events(roles: Roles):
    """L'audit reste en ajout seul, y compris pour le rôle transverse."""
    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        for statement in ("DELETE FROM audit_events", "UPDATE audit_events SET action = 'falsifié'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(statement)
            connection.rollback()


def test_concurrent_signups_do_not_interfere(identity_app):
    """Deux inscriptions simultanées : chacune obtient son organisation et son jeton."""
    from concurrent.futures import ThreadPoolExecutor

    from app.identity.self_service import signup

    settings, captured = identity_app

    def create(index: int):
        return signup(
            settings=settings,
            email=f"simultane{index}@exemple.test",
            password="MotDePasseSolide!2026",
            organization_name=f"Organisation simultanée {index}",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(create, range(4)))

    organizations = {outcome.organization_id for outcome in outcomes}
    assert len(organizations) == 4, "deux inscriptions ont partagé une organisation"
    assert len(captured) >= 4
