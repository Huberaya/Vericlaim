"""Real PostgreSQL proof that tenant row-level security isolates organisations.

SQLite cannot express RLS, so the rest of the suite cannot prove this control.
These tests build a disposable database from the reviewed Alembic chain, apply
the shipped runtime grants, and exercise the policies with the real runtime
role.

They are skipped unless ``VERICLAIM_TEST_POSTGRES_URL`` points at a PostgreSQL
server the tests may administer (CI provides a throwaway service container).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import psycopg
import pytest
from psycopg import sql

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
NEON_ROOT = REPOSITORY_ROOT / "infra" / "neon"
ALEMBIC_HEAD = "a7d4c9e2b510"

ADMIN_DSN = os.environ.get("VERICLAIM_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not ADMIN_DSN,
    reason="VERICLAIM_TEST_POSTGRES_URL is not configured; PostgreSQL RLS proof requires a disposable server",
)

MIGRATOR_ROLE = "vericlaim_migrator"
APP_ROLE = "vericlaim_app"
MIGRATOR_PASSWORD = "integration-migrator-password"
APP_PASSWORD = "integration-app-password"


@dataclass(frozen=True)
class Cluster:
    """Administrative handle on the disposable database used by the suite."""

    admin_dsn: str
    database: str
    migrator_dsn: str
    app_dsn: str


def _dsn_for(dsn: str, *, dbname: str | None = None, user: str | None = None, password: str | None = None) -> str:
    """Rebuild a libpq conninfo string from an administrative DSN."""

    overrides: dict[str, str] = {}
    if dbname is not None:
        overrides["dbname"] = dbname
    if user is not None:
        overrides["user"] = user
    if password is not None:
        overrides["password"] = password
    return psycopg.conninfo.make_conninfo(dsn, **overrides)


def _sqlalchemy_url(dsn: str, *, dbname: str, user: str, password: str) -> str:
    """Build a SQLAlchemy URL Alembic can parse, including Unix-socket hosts."""

    params = psycopg.conninfo.conninfo_to_dict(dsn)
    host = params.get("host", "") or ""
    port = str(params.get("port", "") or "")
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
    if host.startswith("/"):
        return f"postgresql+psycopg://{auth}@/{dbname}?host={quote(host, safe='')}"
    if host:
        return f"postgresql+psycopg://{auth}@{host}:{port}/{dbname}" if port else f"postgresql+psycopg://{auth}@{host}/{dbname}"
    return f"postgresql+psycopg://{auth}@/{dbname}"


def _connect(dsn: str, *, autocommit: bool = False) -> psycopg.Connection[object]:
    return psycopg.connect(dsn, autocommit=autocommit)


def load_verifier():
    """Import the shipped staging verifier without polluting ``sys.path``."""

    module_name = "vericlaim_verify_runtime"
    if module_name in sys.modules:
        return sys.modules[module_name]
    sys.path.insert(0, str(NEON_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(module_name, NEON_ROOT / "verify_runtime.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(NEON_ROOT))
    sys.modules[module_name] = module
    return module


def _drop_database_and_roles(dsn: str, database: str) -> None:
    with _connect(dsn, autocommit=True) as admin, admin.cursor() as cursor:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database,),
        )
        cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
        for role in (APP_ROLE, MIGRATOR_ROLE):
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if not cursor.fetchone():
                continue
            try:
                cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            except psycopg.errors.DependentObjectsStillExist:
                # Another database on the same server still references the role;
                # this suite only owns its own database.
                pass


@pytest.fixture(scope="module")
def cluster() -> Iterator[Cluster]:
    database = f"vericlaim_it_{uuid.uuid4().hex[:12]}"
    _drop_database_and_roles(ADMIN_DSN, database)

    with _connect(ADMIN_DSN, autocommit=True) as admin, admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        # Roles are cluster-wide and may survive an interrupted run. Reuse them
        # only after re-asserting the least-privilege attributes: a pre-existing
        # privileged role must never be silently adopted by this suite.
        for role, password in ((MIGRATOR_ROLE, MIGRATOR_PASSWORD), (APP_ROLE, APP_PASSWORD)):
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cursor.fetchone():
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )
            else:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )

    migrator_dsn = _dsn_for(ADMIN_DSN, dbname=database, user=MIGRATOR_ROLE, password=MIGRATOR_PASSWORD)
    app_dsn = _dsn_for(ADMIN_DSN, dbname=database, user=APP_ROLE, password=APP_PASSWORD)

    # Mirror 01_bootstrap_roles.sql: only the owner may grant on the schema.
    with _connect(_dsn_for(ADMIN_DSN, dbname=database), autocommit=True) as owner, owner.cursor() as cursor:
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        cursor.execute("GRANT USAGE, CREATE ON SCHEMA public TO vericlaim_migrator")
        cursor.execute("GRANT USAGE ON SCHEMA public TO vericlaim_app")
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO vericlaim_migrator, vericlaim_app").format(
                sql.Identifier(database)
            )
        )

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

    with _connect(migrator_dsn, autocommit=True) as migrator, migrator.cursor() as cursor:
        cursor.execute((NEON_ROOT / "02_after_migration_grants.sql").read_text(encoding="utf-8"))

    try:
        yield Cluster(
            admin_dsn=ADMIN_DSN,
            database=database,
            migrator_dsn=migrator_dsn,
            app_dsn=app_dsn,
        )
    finally:
        _drop_database_and_roles(ADMIN_DSN, database)


@dataclass(frozen=True)
class Tenants:
    organization_a: uuid.UUID
    organization_b: uuid.UUID
    supplier_a: uuid.UUID
    supplier_b: uuid.UUID


def _seed(cluster: Cluster) -> Tenants:
    organization_a, organization_b = uuid.uuid4(), uuid.uuid4()
    supplier_a, supplier_b = uuid.uuid4(), uuid.uuid4()
    product_a, product_b = uuid.uuid4(), uuid.uuid4()
    key_a, key_b = uuid.uuid4(), uuid.uuid4()
    hold_a, hold_b = uuid.uuid4(), uuid.uuid4()

    with _connect(cluster.migrator_dsn) as connection, connection.cursor() as cursor:
        for organization, supplier, product, key, hold, label in (
            (organization_a, supplier_a, product_a, key_a, hold_a, "A"),
            (organization_b, supplier_b, product_b, key_b, hold_b, "B"),
        ):
            # FORCE RLS applies to the table owner as well: seeding must install
            # the same request context the application installs.
            cursor.execute(
                "SELECT set_config('app.current_organization_id', %s, true)",
                (str(organization),),
            )
            cursor.execute(
                "INSERT INTO organizations (id, name, slug, status, data_region, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'active', 'eu', now(), now())",
                (organization, f"Organisation {label}", f"org-{organization.hex[:12]}"),
            )
            cursor.execute(
                "INSERT INTO suppliers (id, organization_id, legal_name, metadata_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, '{}', now(), now())",
                (supplier, organization, f"Fournisseur {label}"),
            )
            cursor.execute(
                "INSERT INTO products "
                "(id, organization_id, supplier_id, reference, name, lifecycle_status, metadata_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 'active', '{}', now(), now())",
                (product, organization, supplier, f"REF-{label}", f"Produit {label}"),
            )
            cursor.execute(
                "INSERT INTO api_keys (id, organization_id, name, prefix, hashed_secret, scopes, created_at) "
                "VALUES (%s, %s, %s, %s, %s, '[]', now())",
                (key, organization, f"Clé {label}", f"prefix{label}", "0" * 64),
            )
            cursor.execute(
                "INSERT INTO legal_holds (id, organization_id, case_reference, reason, created_at) "
                "VALUES (%s, %s, %s, %s, now())",
                (hold, organization, f"CASE-{label}", f"Motif {label}"),
            )
        connection.commit()

    return Tenants(
        organization_a=organization_a,
        organization_b=organization_b,
        supplier_a=supplier_a,
        supplier_b=supplier_b,
    )


def _set_tenant(connection: psycopg.Connection[object], organization: uuid.UUID | None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_organization_id', %s, true)",
            ("",) if organization is None else (str(organization),),
        )


def _count(connection: psycopg.Connection[object], table: str, organization: uuid.UUID | None = None) -> int:
    with connection.cursor() as cursor:
        if organization is None:
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        else:
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE organization_id = %s").format(sql.Identifier(table)),
                (organization,),
            )
        return int(cursor.fetchone()[0])


@pytest.fixture()
def tenants(cluster: Cluster) -> Tenants:
    return _seed(cluster)


@pytest.fixture()
def app_connection(cluster: Cluster) -> Iterator[psycopg.Connection[object]]:
    with _connect(cluster.app_dsn) as connection:
        yield connection
        connection.rollback()


def test_migrations_reach_the_expected_head(cluster: Cluster) -> None:
    with _connect(cluster.app_dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM public.alembic_version")
        assert [row[0] for row in cursor.fetchall()] == [ALEMBIC_HEAD]


def test_verifier_passes_on_a_healthy_database(cluster: Cluster, tenants: Tenants) -> None:
    verifier = load_verifier()
    with _connect(cluster.app_dsn) as connection:
        failures = verifier.collect_failures(connection, ALEMBIC_HEAD)
    assert failures == []


def test_no_tenant_rows_are_visible_without_context(cluster: Cluster, tenants: Tenants) -> None:
    with _connect(cluster.app_dsn) as connection:
        _set_tenant(connection, None)
        for table in ("suppliers", "products", "analyses", "api_keys", "legal_holds"):
            assert _count(connection, table) == 0


def test_each_tenant_only_reads_its_own_rows(cluster: Cluster, tenants: Tenants, app_connection) -> None:
    for table in ("suppliers", "products", "api_keys", "legal_holds"):
        _set_tenant(app_connection, tenants.organization_a)
        assert _count(app_connection, table) == 1
        assert _count(app_connection, table, tenants.organization_b) == 0

        _set_tenant(app_connection, tenants.organization_b)
        assert _count(app_connection, table) == 1
        assert _count(app_connection, table, tenants.organization_a) == 0


def test_cross_tenant_insert_is_refused(cluster: Cluster, tenants: Tenants, app_connection) -> None:
    _set_tenant(app_connection, tenants.organization_a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with app_connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO suppliers (id, organization_id, legal_name, metadata_json, created_at, updated_at) "
                "VALUES (%s, %s, 'Intrus', '{}', now(), now())",
                (uuid.uuid4(), tenants.organization_b),
            )
    app_connection.rollback()


def test_cross_tenant_update_and_delete_affect_no_rows(cluster: Cluster, tenants: Tenants, app_connection) -> None:
    _set_tenant(app_connection, tenants.organization_a)

    with app_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE suppliers SET legal_name = 'pirate' WHERE organization_id = %s",
            (tenants.organization_b,),
        )
        assert cursor.rowcount == 0
    app_connection.rollback()

    _set_tenant(app_connection, tenants.organization_a)
    with app_connection.cursor() as cursor:
        cursor.execute("DELETE FROM suppliers WHERE organization_id = %s", (tenants.organization_b,))
        assert cursor.rowcount == 0
    app_connection.rollback()

    # The victim row is still readable by its owner.
    _set_tenant(app_connection, tenants.organization_b)
    with app_connection.cursor() as cursor:
        cursor.execute("SELECT legal_name FROM suppliers WHERE id = %s", (tenants.supplier_b,))
        assert cursor.fetchone() == ("Fournisseur B",)


def test_runtime_role_has_least_privilege(cluster: Cluster) -> None:
    with _connect(cluster.app_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        assert cursor.fetchone() == (False, False, False, False)

        cursor.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
        assert cursor.fetchone()[0] is False

        cursor.execute(
            "SELECT has_table_privilege(current_user, 'alembic_version', 'SELECT'), "
            "has_table_privilege(current_user, 'alembic_version', 'UPDATE')"
        )
        assert cursor.fetchone() == (True, False)

        cursor.execute(
            "SELECT has_table_privilege(current_user, 'audit_events', 'UPDATE'), "
            "has_table_privilege(current_user, 'audit_events', 'DELETE'), "
            "has_table_privilege(current_user, 'audit_records', 'UPDATE'), "
            "has_table_privilege(current_user, 'audit_records', 'DELETE')"
        )
        assert cursor.fetchone() == (False, False, False, False)


def test_audit_tables_are_append_only_for_the_runtime_role(
    cluster: Cluster, tenants: Tenants, app_connection
) -> None:
    _set_tenant(app_connection, tenants.organization_a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with app_connection.cursor() as cursor:
            cursor.execute("UPDATE audit_events SET action = 'tampered'")
    app_connection.rollback()


def _mutate(cluster: Cluster, statements: tuple[str, ...]) -> None:
    """Apply DDL on the disposable database and commit it.

    Row-level security metadata is only visible to other sessions once the
    transaction commits, so mutating inside an open transaction would make the
    verifier blind to the regression under test.
    """

    with _connect(cluster.migrator_dsn, autocommit=True) as migrator, migrator.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def test_verifier_detects_a_disabled_rls_table(cluster: Cluster, tenants: Tenants) -> None:
    verifier = load_verifier()
    try:
        _mutate(cluster, ('ALTER TABLE "reports" DISABLE ROW LEVEL SECURITY',))
        with _connect(cluster.app_dsn) as app_connection:
            failures = verifier.collect_failures(app_connection, ALEMBIC_HEAD)
    finally:
        _mutate(cluster, ('ALTER TABLE "reports" ENABLE ROW LEVEL SECURITY', "ALTER TABLE \"reports\" FORCE ROW LEVEL SECURITY"))
    assert any("RLS is disabled on reports" in failure for failure in failures)


def test_verifier_detects_a_policy_with_a_permissive_predicate(cluster: Cluster, tenants: Tenants) -> None:
    verifier = load_verifier()
    tenant_predicate = "organization_id = vericlaim_current_organization_id()"
    try:
        _mutate(
            cluster,
            (
                'DROP POLICY "p_claims_tenant" ON claims',
                'CREATE POLICY "p_claims_tenant" ON claims USING (true) WITH CHECK (true)',
            ),
        )
        with _connect(cluster.app_dsn) as app_connection:
            failures = verifier.collect_failures(app_connection, ALEMBIC_HEAD)
    finally:
        _mutate(
            cluster,
            (
                'DROP POLICY IF EXISTS "p_claims_tenant" ON claims',
                f'CREATE POLICY "p_claims_tenant" ON claims USING ({tenant_predicate}) WITH CHECK ({tenant_predicate})',
            ),
        )
    assert any("does not use the tenant helper" in failure for failure in failures)


def test_verifier_detects_a_new_unprotected_tenant_table(cluster: Cluster, tenants: Tenants) -> None:
    verifier = load_verifier()
    try:
        _mutate(
            cluster,
            (
                "CREATE TABLE experimental_notes (id uuid PRIMARY KEY, organization_id uuid NOT NULL)",
                "GRANT SELECT ON experimental_notes TO vericlaim_app",
            ),
        )
        with _connect(cluster.app_dsn) as app_connection:
            failures = verifier.collect_failures(app_connection, ALEMBIC_HEAD)
    finally:
        _mutate(cluster, ("DROP TABLE IF EXISTS experimental_notes",))
    assert any("experimental_notes" in failure for failure in failures)
