from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SQL = REPOSITORY_ROOT / "infra" / "neon" / "01_bootstrap_roles.sql"


def test_neon_bootstrap_requires_the_connected_database_to_match_target() -> None:
    sql = BOOTSTRAP_SQL.read_text(encoding="utf-8")

    assert "current_database() = :'target_database'" in sql
    assert "\\if :vericlaim_target_database_matches" in sql
    assert "current database does not match target_database" in sql


def test_neon_bootstrap_only_activates_preprovisioned_nologin_roles_once() -> None:
    sql = BOOTSTRAP_SQL.read_text(encoding="utf-8")

    assert "already has LOGIN; refusing to rotate an existing credential" in sql
    for role in ("vericlaim_migrator", "vericlaim_app"):
        activation = re.compile(
            rf"ALTER ROLE {role} LOGIN NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L.*?"
            rf"WHERE EXISTS \(.*?rolname = '{role}'.*?AND NOT rolcanlogin.*?\)\s*\\gexec",
            re.DOTALL,
        )
        assert activation.search(sql), role


def test_neon_bootstrap_keeps_roles_non_privileged_after_activation() -> None:
    sql = BOOTSTRAP_SQL.read_text(encoding="utf-8")

    for privilege in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "rolinherit"):
        assert privilege in sql
    assert "least-privilege bootstrap requirements" in sql
