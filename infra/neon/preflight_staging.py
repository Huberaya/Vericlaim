#!/usr/bin/env python3
"""Read-only fail-closed target and role check before staging migrations."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import psycopg

from postgres_utils import postgres_dsn_from_environment, require_tls

EXPECTED_ROLE = "vericlaim_migrator"


def main() -> int:
    # These are non-secret GitHub Environment variables, reviewed separately
    # from the credential URL. Missing target identity must stop the migration.
    expected_host = os.environ.get("STAGING_DATABASE_HOST", "").strip().lower()
    expected_database = os.environ.get("STAGING_DATABASE_NAME", "").strip()
    production_host = os.environ.get("PRODUCTION_DATABASE_HOST", "").strip().lower()

    if not expected_host or not expected_database:
        raise RuntimeError(
            "Refusing staging migration: STAGING_DATABASE_HOST and "
            "STAGING_DATABASE_NAME must be configured in the staging environment."
        )
    if expected_database != "vericlaim":
        raise RuntimeError("Refusing staging migration: expected staging database name must be 'vericlaim'.")
    if production_host and expected_host == production_host:
        raise RuntimeError("Refusing staging migration: staging host matches the configured production host.")

    dsn = postgres_dsn_from_environment("DATABASE_URL_MIGRATOR")
    parsed = urlsplit(os.environ["DATABASE_URL_MIGRATOR"].strip())
    actual_host = (parsed.hostname or "").lower()
    actual_database = parsed.path.strip("/")

    if actual_host != expected_host:
        raise RuntimeError("Refusing staging migration: credential URL host does not match STAGING_DATABASE_HOST.")
    if actual_database != expected_database:
        raise RuntimeError("Refusing staging migration: credential URL database does not match STAGING_DATABASE_NAME.")
    if parsed.scheme not in {"postgresql", "postgres", "postgresql+psycopg"}:
        raise RuntimeError("Refusing staging migration: unsupported database URL scheme.")
    if parsed.query and any(
        key.lower() == "sslmode" and value.lower() in {"disable", "allow", "prefer"}
        for pair in parsed.query.split("&")
        for key, _, value in [pair.partition("=")]
    ):
        raise RuntimeError("Refusing staging migration: database URL does not require TLS.")

    with psycopg.connect(
        dsn,
        autocommit=True,
        application_name="vericlaim-staging-migration-preflight",
    ) as connection:
        require_tls(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_database(), current_user, r.rolsuper,
                       r.rolcreaterole, r.rolcreatedb, r.rolbypassrls
                FROM pg_roles AS r
                WHERE r.rolname = current_user
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Refusing staging migration: current PostgreSQL role could not be verified.")
            database, role, is_superuser, can_create_role, can_create_db, bypasses_rls = row
            if database != expected_database:
                raise RuntimeError("Refusing staging migration: connected database differs from expected staging database.")
            if role != EXPECTED_ROLE:
                raise RuntimeError("Refusing staging migration: connection is not using vericlaim_migrator.")
            if is_superuser or can_create_role or can_create_db or bypasses_rls:
                raise RuntimeError("Refusing staging migration: migration role has prohibited elevated privileges.")

    print("Staging migration preflight passed: target, TLS, and migrator role verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
