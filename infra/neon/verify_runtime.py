#!/usr/bin/env python3
"""Verify runtime role, migration head and tenant RLS without modifying data."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import psycopg
from psycopg import sql

from postgres_utils import postgres_dsn_from_environment, require_tls

EXPECTED_RUNTIME_ROLE = "vericlaim_app"
EXPECTED_RLS_TABLES: tuple[str, ...] = (
    "analysis_detection_jobs",
    "analysis_documents",
    "analysis_versions",
    "analyses",
    "api_keys",
    "audit_events",
    "audit_records",
    "certificates",
    "claims",
    "document_extraction_jobs",
    "document_segments",
    "document_uploads",
    "document_versions",
    "documents",
    "evidence_links",
    "evidence_requests",
    "evidence",
    "legal_holds",
    "products",
    "recommendations",
    "reports",
    "risks",
    "rule_versions",
    "rules",
    "suppliers",
    "validations",
)
TENANT_VISIBILITY_TABLES: tuple[str, ...] = ("suppliers", "products", "documents", "analyses")
# Tables that carry an organization_id but are intentionally NOT row-secured:
# a request must resolve its opaque session and active membership before any
# tenant context exists. They are service-only and filtered in the identity
# layer, so listing them here makes a *new* unprotected table a hard failure.
SERVICE_ONLY_TENANT_TABLES: frozenset[str] = frozenset({"memberships"})
TENANT_POLICY_HELPER = "vericlaim_current_organization_id()"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL_APP",
        help="environment variable containing the runtime PostgreSQL URL",
    )
    parser.add_argument(
        "--expected-revision",
        required=True,
        help="Alembic revision expected from the checked-out source",
    )
    return parser.parse_args()


def _required(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _read_rls_state(cursor: psycopg.Cursor[object]) -> dict[str, tuple[bool, bool, int]]:
    cursor.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, count(p.polname)
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        LEFT JOIN pg_policy AS p ON p.polrelid = c.oid
        WHERE n.nspname = 'public'
          AND c.relname = ANY(%s)
        GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
        """,
        (list(EXPECTED_RLS_TABLES),),
    )
    return {name: (enabled, forced, policy_count) for name, enabled, forced, policy_count in cursor.fetchall()}


def _read_tenant_column_tables(cursor: psycopg.Cursor[object]) -> list[str]:
    """Every public table exposing organization_id, protected or not."""
    cursor.execute(
        """
        SELECT c.relname
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND EXISTS (
            SELECT 1 FROM information_schema.columns AS col
            WHERE col.table_schema = 'public'
              AND col.table_name = c.relname
              AND col.column_name = 'organization_id'
          )
        ORDER BY c.relname
        """
    )
    return [row[0] for row in cursor.fetchall()]


def _read_policies(cursor: psycopg.Cursor[object]) -> dict[str, list[tuple[str, str | None, str | None]]]:
    """Policy name, USING expression and WITH CHECK expression per table."""
    cursor.execute(
        """
        SELECT tablename, policyname, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname
        """
    )
    policies: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for table_name, policy_name, qual, with_check in cursor.fetchall():
        policies.setdefault(table_name, []).append((policy_name, qual, with_check))
    return policies


def _read_rows_without_tenant(cursor: psycopg.Cursor[object], table_names: Sequence[str]) -> dict[str, int]:
    cursor.execute("SELECT set_config('app.current_organization_id', '', true)")
    counts: dict[str, int] = {}
    for table_name in table_names:
        cursor.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table_name)))
        counts[table_name] = int(cursor.fetchone()[0])
    return counts


def collect_failures(connection: psycopg.Connection[object], expected_revision: str) -> list[str]:
    """Read-only audit of the runtime role, migration head and tenant RLS.

    Kept independent from TLS/DSN handling so the exact same assertions can be
    replayed by the PostgreSQL integration tests against a disposable cluster.
    """
    failures: list[str] = []
    with connection.cursor() as cursor:
        cursor.execute("BEGIN READ ONLY")
        try:
            cursor.execute(
                "SELECT current_user, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            role_name, is_superuser, can_create_role, can_create_db, bypasses_rls = cursor.fetchone()
            _required(role_name == EXPECTED_RUNTIME_ROLE, "runtime connection does not use vericlaim_app", failures)
            _required(not is_superuser, "runtime role is a PostgreSQL superuser", failures)
            _required(not can_create_role, "runtime role can create PostgreSQL roles", failures)
            _required(not can_create_db, "runtime role can create databases", failures)
            _required(not bypasses_rls, "runtime role bypasses RLS", failures)

            cursor.execute("SELECT version_num FROM public.alembic_version")
            revisions = [row[0] for row in cursor.fetchall()]
            _required(revisions == [expected_revision], "Alembic revision does not match checked-out source", failures)

            rls_state = _read_rls_state(cursor)
            for table_name in EXPECTED_RLS_TABLES:
                state = rls_state.get(table_name)
                _required(state is not None, f"missing RLS table: {table_name}", failures)
                if state is not None:
                    enabled, forced, policy_count = state
                    _required(enabled, f"RLS is disabled on {table_name}", failures)
                    _required(forced, f"RLS is not forced on {table_name}", failures)
                    _required(policy_count > 0, f"no RLS policy found on {table_name}", failures)

            cursor.execute(
                """
                SELECT
                  has_schema_privilege(current_user, 'public', 'CREATE'),
                  has_table_privilege(current_user, 'public.alembic_version', 'SELECT'),
                  has_table_privilege(current_user, 'public.alembic_version', 'UPDATE'),
                  has_table_privilege(current_user, 'public.audit_events', 'UPDATE'),
                  has_table_privilege(current_user, 'public.audit_events', 'DELETE'),
                  has_table_privilege(current_user, 'public.audit_records', 'UPDATE'),
                  has_table_privilege(current_user, 'public.audit_records', 'DELETE')
                """
            )
            (
                schema_create,
                migration_select,
                migration_update,
                audit_event_update,
                audit_event_delete,
                audit_record_update,
                audit_record_delete,
            ) = cursor.fetchone()
            _required(not schema_create, "runtime role can create objects in public", failures)
            _required(migration_select, "runtime role cannot read migration revision", failures)
            _required(not migration_update, "runtime role can alter migration revision", failures)
            _required(not audit_event_update and not audit_event_delete, "audit_events is not append-only", failures)
            _required(not audit_record_update and not audit_record_delete, "audit_records is not append-only", failures)

            for table_name, count in _read_rows_without_tenant(cursor, TENANT_VISIBILITY_TABLES).items():
                _required(count == 0, f"tenant rows visible without context in {table_name}", failures)

            # Drift guards: a table added later with organization_id but no
            # RLS, or a policy whose predicate no longer applies the tenant
            # helper, must fail closed instead of passing silently.
            policies = _read_policies(cursor)
            for table_name in _read_tenant_column_tables(cursor):
                if table_name in SERVICE_ONLY_TENANT_TABLES:
                    continue
                table_policies = policies.get(table_name, [])
                _required(
                    bool(table_policies),
                    f"table with organization_id has no RLS policy: {table_name}",
                    failures,
                )
                for policy_name, qual, with_check in table_policies:
                    _required(
                        bool(qual) and TENANT_POLICY_HELPER in (qual or ""),
                        f"policy {policy_name} on {table_name} does not use the tenant helper",
                        failures,
                    )
                    _required(
                        bool(with_check) and TENANT_POLICY_HELPER in (with_check or ""),
                        f"policy {policy_name} on {table_name} has no tenant WITH CHECK",
                        failures,
                    )
        finally:
            cursor.execute("ROLLBACK")
    return failures


def main() -> int:
    args = parse_args()
    dsn = postgres_dsn_from_environment(args.database_url_env)

    with psycopg.connect(
        dsn,
        autocommit=True,
        application_name="vericlaim-runtime-security-verification",
    ) as connection:
        require_tls(connection)
        failures = collect_failures(connection, args.expected_revision)

    if failures:
        print("Runtime security verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Runtime security verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
