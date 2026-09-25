#!/usr/bin/env python3
"""Apply reviewed post-migration runtime grants as the migration role.

This runner deliberately accepts its connection URL only from an environment
variable. It neither prints nor writes credentials, and it refuses to execute
as a privileged or unexpected PostgreSQL role.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

from postgres_utils import postgres_dsn_from_environment, require_tls

SCRIPT_PATH = Path(__file__).with_name("02_after_migration_grants.sql")
EXPECTED_ROLE = "vericlaim_migrator"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL_MIGRATOR",
        help="environment variable containing the migration PostgreSQL URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sql_text = SCRIPT_PATH.read_text(encoding="utf-8")
    if any(line.lstrip().startswith("\\") for line in sql_text.splitlines()):
        raise RuntimeError("The grants file must contain SQL only, not psql meta-commands.")

    dsn = postgres_dsn_from_environment(args.database_url_env)
    with psycopg.connect(
        dsn,
        autocommit=True,
        application_name="vericlaim-post-migration-grants",
    ) as connection:
        require_tls(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            role_name, is_superuser, bypasses_rls = cursor.fetchone()
            if role_name != EXPECTED_ROLE:
                raise RuntimeError(
                    f"Refusing post-migration grants as {role_name!r}; expected {EXPECTED_ROLE!r}."
                )
            if is_superuser or bypasses_rls:
                raise RuntimeError("Refusing post-migration grants from a privileged PostgreSQL role.")

            # The reviewed SQL contains its own BEGIN/COMMIT pair. No
            # parameters are bound, so Psycopg may safely submit the reviewed
            # multi-statement file as one simple query.
            cursor.execute(sql_text)
            while cursor.nextset() is not None:
                pass

    print("Post-migration runtime grants applied successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
