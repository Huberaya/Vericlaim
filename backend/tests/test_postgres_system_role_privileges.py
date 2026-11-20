"""Prouver PostgreSQL : ce que le rôle système peut et ne peut pas faire.

Ce fichier ne lit pas le SQL de migration. Il interroge PostgreSQL
(``has_table_privilege``, ``has_column_privilege``) sur une base construite par
la chaîne Alembic réelle, avec les rôles que la production utilise, puis tente
les opérations.

Pourquoi c'est nécessaire. La migration ``c5f1a8e37b910`` accordait ``UPDATE``
au niveau table sur ``users`` et ``memberships``. Un ``GRANT`` de table rend
invisible la question « quelles colonnes ? » : le rôle chargé des parcours
transverses pouvait réécrire ``users.password_hash``, ``users.status``,
``memberships.organization_id`` ou ``memberships.role_id``. Aucun parcours n'en
avait besoin. Ces tests échouent si l'un de ces droits revient, y compris par un
``GRANT`` posé ailleurs que dans la migration (``02_after_migration_grants.sql``).

Les tests ne sont pas seulement négatifs : un refus total ferait passer les
vérifications négatives en cassant les parcours d'identité. Les deux doivent
tenir ensemble, et ``test_postgres_identity_flows.py`` couvre l'autre moitié.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.test_postgres_identity_flows import (  # noqa: F401 — fixture réutilisée
    SYSTEM_ROLE,
    Roles,
    identity_app,
    roles,
)

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("VERICLAIM_TEST_POSTGRES_URL", "").strip(),
    reason="proving effective privileges requires a disposable PostgreSQL server",
)


def _privilege(connection: psycopg.Connection, function: str, *args: str) -> bool:
    """``has_table_privilege`` prend 3 arguments, ``has_column_privilege`` 4."""
    placeholders = ", ".join(["%s"] * (1 + len(args)))
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {function}({placeholders})", (SYSTEM_ROLE, *args))
        return bool(cursor.fetchone()[0])


@pytest.fixture()
def system(roles: Roles):
    with psycopg.connect(roles.system_dsn) as connection:
        yield connection


# --------------------------------------------------------------------------- #
# Négatif : les colonnes que le rôle système ne doit plus pouvoir modifier
# --------------------------------------------------------------------------- #

FORBIDDEN_UPDATE_COLUMNS = (
    # Déplacer une adhésion ou en changer le rôle est une élévation de privilège.
    ("memberships", "organization_id"),
    ("memberships", "role_id"),
    ("memberships", "user_id"),
    ("memberships", "invited_by_user_id"),
    # Réécrire l'identité d'un compte permet de le faire passer pour un autre.
    ("users", "email"),
    ("users", "identity_provider"),
    ("users", "external_subject"),
    # Un jeton ne doit pas pouvoir être réémis, réorienté ou faire long feu.
    ("identity_tokens", "user_id"),
    ("identity_tokens", "purpose"),
    ("identity_tokens", "token_hash"),
    ("identity_tokens", "expires_at"),
    ("identity_tokens", "created_by_user_id"),
)


@pytest.mark.parametrize("table,column", FORBIDDEN_UPDATE_COLUMNS)
def test_the_system_role_cannot_update_identity_columns(system, table: str, column: str):
    """Lecture effective, pas lecture du SQL : PostgreSQL répond lui-même."""
    assert not _privilege(system, "has_column_privilege", table, column, "UPDATE"), (
        f"{SYSTEM_ROLE} peut modifier {table}.{column} : privilège excessif"
    )


@pytest.mark.parametrize("table", ["users", "memberships", "organizations", "roles"])
def test_the_system_role_has_no_table_wide_update(system, table: str):
    """``UPDATE`` au niveau table ouvre toutes les colonnes d'un coup."""
    assert not _privilege(system, "has_table_privilege", table, "UPDATE"), (
        f"{SYSTEM_ROLE} détient UPDATE sur la table {table} entière"
    )


def test_the_system_role_cannot_delete_or_truncate_anything(system):
    """La suppression n'a jamais été accordée ; elle ne doit pas l'être."""
    with system.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND (has_table_privilege(%s, c.oid, 'DELETE') OR has_table_privilege(%s, c.oid, 'TRUNCATE'))",
            (SYSTEM_ROLE, SYSTEM_ROLE),
        )
        assert cursor.fetchone()[0] == 0


def test_the_lockout_table_lost_its_insert_but_kept_its_counter(system):
    """Le compteur de tentatives de connexion n'a pas besoin d'``INSERT``.

    ``_register_failure`` — le seul endroit qui crée une ligne — est appelé par
    ``login_with_password``, qui reçoit la session **applicative**. La
    réinitialisation, elle, ne remet le compteur à zéro que par ``UPDATE``.
    """
    assert not _privilege(system, "has_table_privilege", "password_login_attempts", "INSERT"), (
        f"{SYSTEM_ROLE} peut encore créer un compteur d'échecs"
    )
    assert _privilege(system, "has_table_privilege", "password_login_attempts", "UPDATE")


def test_the_billing_event_insert_is_kept_and_its_reason_is_recorded(system):
    """Droit conservé, et documenté comme tel — pas oublié.

    Un webhook de prestataire n'a aucun contexte de tenant : la ligne naît sans
    organisation, puis découvre la sienne en s'appliquant. PostgreSQL refuse cet
    ``INSERT`` au rôle applicatif (mesuré), donc la capacité est réelle même si
    le code emprunte aujourd'hui le mauvais rôle. Le défaut de routage est
    consigné dans la migration ; ce test empêche de le confondre avec un excès
    de privilège et de retirer le droit en croyant bien faire.
    """
    assert _privilege(system, "has_table_privilege", "billing_provider_events", "INSERT")
    # Ce qui reste fermé : aucune lecture du corps de l'événement ni des colonnes
    # de résultat d'application, qui décrivent l'état commercial du tenant.
    for column in ("payload_json", "apply_result", "updated_at"):
        assert not _privilege(system, "has_column_privilege", "billing_provider_events", column, "SELECT"), (
            f"{SYSTEM_ROLE} lit billing_provider_events.{column}"
        )


# --------------------------------------------------------------------------- #
# Négatif, par l'opération elle-même : le refus est exercé, pas seulement lu
# --------------------------------------------------------------------------- #


def test_a_membership_cannot_be_moved_or_elevated_by_the_system_role(roles: Roles, identity_app):
    """Le scénario de la mission, exécuté et refusé par PostgreSQL.

    On crée un utilisateur, deux organisations et une adhésion réelle, puis on
    tente depuis le rôle système : déplacer l'adhésion vers l'autre organisation,
    puis en changer le rôle. Les deux doivent être refusés par la base.
    """
    from app.identity.self_service import signup

    settings, _captured = identity_app
    signup(
        settings=settings,
        email="membre@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation d'origine",
    )

    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, organization_id, role_id FROM memberships LIMIT 1")
        membership_id, home_organization, home_role = cursor.fetchone()
        assert membership_id is not None

    # Une seconde organisation et un second rôle, créés hors du rôle système.
    intruder_organization = uuid.uuid4()
    with psycopg.connect(roles.migrator_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO organizations (id, name, slug, status, data_region, created_at, updated_at) "
            "VALUES (%s, 'Organisation cible', %s, 'active', 'eu', now(), now())",
            (intruder_organization, f"cible-{intruder_organization.hex[:10]}"),
        )
        cursor.execute("SELECT id FROM roles WHERE code <> 'owner' LIMIT 1")
        other_role = cursor.fetchone()[0]
        connection.commit()

    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        # Déplacer l'adhésion d'une organisation à l'autre.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "UPDATE memberships SET organization_id = %s WHERE id = %s",
                (intruder_organization, membership_id),
            )
        connection.rollback()

        # Élever le rôle de cette adhésion.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "UPDATE memberships SET role_id = %s WHERE id = %s",
                (other_role, membership_id),
            )
        connection.rollback()

        # Rien n'a bougé : l'adhésion est intacte.
        cursor.execute(
            "SELECT organization_id, role_id FROM memberships WHERE id = %s", (membership_id,)
        )
        assert cursor.fetchone() == (home_organization, home_role)


def test_a_user_cannot_be_rewritten_arbitrarily_by_the_system_role(roles: Roles, identity_app):
    """Ni l'e-mail, ni le fournisseur d'identité, ni l'identifiant externe.

    C'est ce qui empêcherait de faire passer un compte pour un autre — et donc,
    combiné à ``BYPASSRLS``, de lire les données d'un tiers sous son identité.
    """
    from app.identity.self_service import signup

    settings, _captured = identity_app
    signup(
        settings=settings,
        email="cible@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation cible",
    )

    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, email, password_hash FROM users WHERE email = 'cible@exemple.test'")
        user_id, original_email, original_hash = cursor.fetchone()

        for statement, parameters in (
            ("UPDATE users SET email = %s WHERE id = %s", ("usurpateur@exemple.test", user_id)),
            ("UPDATE users SET identity_provider = %s WHERE id = %s", ("oidc", user_id)),
            ("UPDATE users SET external_subject = %s WHERE id = %s", ("sub-1234", user_id)),
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(statement, parameters)
            connection.rollback()

        cursor.execute("SELECT email, password_hash FROM users WHERE id = %s", (user_id,))
        assert cursor.fetchone() == (original_email, original_hash)


def test_a_token_cannot_be_redirected_to_another_account(roles: Roles, identity_app):
    """Réécrire ``identity_tokens.user_id`` détournerait un lien vers un compte.

    Le jeton d'un utilisateur serait alors consommé au nom d'un autre : c'est le
    vecteur de prise de contrôle le plus direct que ce rôle pouvait offrir.
    """
    from app.identity.self_service import signup

    settings, _captured = identity_app
    signup(
        settings=settings,
        email="jeton@exemple.test",
        password="MotDePasseSolide!2026",
        organization_name="Organisation jeton",
    )

    with psycopg.connect(roles.system_dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, user_id FROM identity_tokens LIMIT 1")
        token_id, owner = cursor.fetchone()

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "UPDATE identity_tokens SET user_id = %s WHERE id = %s", (uuid.uuid4(), token_id)
            )
        connection.rollback()

        cursor.execute("SELECT user_id FROM identity_tokens WHERE id = %s", (token_id,))
        assert cursor.fetchone()[0] == owner


# --------------------------------------------------------------------------- #
# Positif : les colonnes strictement nécessaires restent modifiables
# --------------------------------------------------------------------------- #


REQUIRED_UPDATE_COLUMNS = (
    # Vérification d'adresse, réinitialisation, acceptation d'invitation.
    ("users", "email_verified_at"),
    ("users", "password_hash"),
    ("users", "password_updated_at"),
    # Un compte invité devient actif en vérifiant son adresse.
    ("users", "status"),
    # Consommation d'un jeton, et rattachement tardif à une organisation.
    ("identity_tokens", "used_at"),
    ("identity_tokens", "organization_id"),
    # Acceptation d'invitation.
    ("memberships", "status"),
    ("memberships", "activated_at"),
    # État d'un e-mail sortant.
    ("email_messages", "status"),
    # Révocation de sessions après réinitialisation.
    ("auth_sessions", "revoked_at"),
)


@pytest.mark.parametrize("table,column", REQUIRED_UPDATE_COLUMNS)
def test_the_columns_the_flows_actually_write_are_still_granted(system, table: str, column: str):
    """Un refus total passerait les tests négatifs et casserait la production."""
    assert _privilege(system, "has_column_privilege", table, column, "UPDATE"), (
        f"{SYSTEM_ROLE} ne peut plus écrire {table}.{column} : un parcours d'identité va échouer"
    )


@pytest.mark.parametrize(
    "table",
    ["users", "organizations", "memberships", "identity_tokens", "email_messages", "audit_events"],
)
def test_the_system_role_can_still_create_the_rows_a_signup_needs(system, table: str):
    """L'inscription crée utilisateur, organisation, adhésion, jeton et audit."""
    assert _privilege(system, "has_table_privilege", table, "INSERT")


def test_the_system_role_still_reads_the_founder_role_by_code(system):
    """``_owner_role_id`` projette ``roles.id`` ; ``code`` sert de critère."""
    assert _privilege(system, "has_column_privilege", "roles", "id", "SELECT")
    assert _privilege(system, "has_column_privilege", "roles", "code", "SELECT")
    # Ce qu'il n'a pas à connaître du rôle : les droits qu'il accorde.
    assert not _privilege(system, "has_column_privilege", "roles", "permissions_json", "SELECT")


def test_rls_and_bypass_attributes_are_untouched(system):
    """Le resserrement ne touche pas à la RLS : c'est un périmètre de GRANT.

    Vérifié plutôt qu'affirmé : ``BYPASSRLS`` reste posé, les politiques des
    tables d'identité restent strictement bornées au tenant, et le rôle
    applicatif n'a pas gagné de contournement.
    """
    with system.cursor() as cursor:
        cursor.execute(
            "SELECT rolbypassrls, rolsuper, rolcreaterole FROM pg_roles WHERE rolname = %s",
            (SYSTEM_ROLE,),
        )
        bypass, superuser, createrole = cursor.fetchone()
        assert bypass is True, "BYPASSRLS a été retiré : les parcours transverses vont échouer"
        assert superuser is False and createrole is False

        cursor.execute(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = 'vericlaim_app'"
        )
        assert cursor.fetchone() == (False, False), "le rôle applicatif a gagné un contournement"

        for table in ("identity_tokens", "email_messages", "billing_provider_events"):
            cursor.execute(
                "SELECT count(*) FROM pg_policies WHERE tablename = %s AND qual LIKE %s",
                (table, "%IS NULL%"),
            )
            assert cursor.fetchone()[0] == 0, f"la politique permissive de {table} est revenue"
