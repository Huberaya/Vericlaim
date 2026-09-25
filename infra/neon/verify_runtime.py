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


def _read_rows_without_tenant(cursor: psycopg.Cursor[object], table_names: Sequence[str]) -> dict[str, int]:
    cursor.execute("SELECT set_config('app.current_organization_id', '', true)")
    counts: dict[str, int] = {}
    for table_name in table_names:
        cursor.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table_name)))
        counts[table_name] = int(cursor.fetchone()[0])
    return counts


def main() -> int:
    args = parse_args()
    dsn = postgres_dsn_from_environment(args.database_url_env)
    failures: list[str] = []

    with psycopg.connect(
        dsn,
        autocommit=True,
        application_name="vericlaim-runtime-security-verification",
    ) as connection:
        require_tls(connection)
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
                _required(revisions == [args.expected_revision], "Alembic revision does not match checked-out source", failures)

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
            finally:
                cursor.execute("ROLLBACK")

    if failures:
        print("Runtime security verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Runtime security verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
