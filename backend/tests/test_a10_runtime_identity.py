"""A-10 : TLS, identité runtime, grants et état des migrations sont vérifiables.

Ces tests portent sur le vérificateur et sur la garantie TLS du code de
production. Ils tournent contre un **vrai** PostgreSQL jetable, pas contre une
doublure : un contrôle de privilèges simulé ne prouve rien des privilèges.

Deux limites explicites :

* le cluster local n'a pas de TLS configuré ; les tests de refus TLS utilisent
  donc un port mort et un faux serveur, et le succès TLS réel n'est prouvé que
  si une URL de recette est fournie (``VERICLAIM_STAGING_RUNTIME_URL``) ;
* les rôles Neon ne sont **jamais** modifiés : les tests créent leurs propres
  rôles jetables dans leur propre cluster.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools" / "runtime_check"))

from verify_runtime_identity import collect, main  # noqa: E402


def _pg_bin_dir() -> Path | None:
    try:
        import pgserver
    except ImportError:  # pragma: no cover
        return None
    candidate = Path(pgserver.__file__).parent / "pginstall" / "bin"
    return candidate if (candidate / "pg_dump").exists() else None


@pytest.fixture()
def migrated_cluster(tmp_path: Path):
    """Cluster jetable avec schéma minimal ressemblant au réel.

    Sans ``pgserver`` (cas de la CI), la fixture **saute** au lieu d'échouer :
    un test qui échoue faute d'une dépendance optionnelle ne signale pas un
    défaut du produit, il signale une erreur de conception du test.
    """

    pgserver = pytest.importorskip("pgserver", reason="cluster PostgreSQL jetable indisponible")
    psycopg = pytest.importorskip("psycopg", reason="pilote PostgreSQL absent")

    if _pg_bin_dir() is None:  # pragma: no cover
        pytest.skip("binaires PostgreSQL (pg_dump) absents")

    server = pgserver.get_server(str(tmp_path / "pgdata"))
    dsn = server.get_uri()
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32) primary key)")
        connection.execute("INSERT INTO alembic_version VALUES ('c73b12fd')")
        for table in (
            "documents",
            "document_versions",
            "document_segments",
            "document_uploads",
            "document_extraction_jobs",
            "analyses",
            "claims",
            "evidence",
            "audit_events",
        ):
            connection.execute(f"CREATE TABLE {table} (id uuid primary key)")
            connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            connection.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            connection.execute(f"CREATE POLICY tenant_isolation ON {table} USING (true)")

        connection.execute("CREATE ROLE vericlaim_app LOGIN PASSWORD 'jetable'")
        connection.execute("GRANT USAGE ON SCHEMA public TO vericlaim_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vericlaim_app")
        connection.execute("REVOKE CREATE ON SCHEMA public FROM vericlaim_app")
    return dsn


def _runtime_dsn(admin_dsn: str) -> str:
    # Remplace le rôle de la chaîne d'administration par le rôle applicatif,
    # en gardant hôte et base : seul l'utilisateur change.
    return admin_dsn.replace("postgresql://postgres:", "postgresql://vericlaim_app:jetable", 1)


def test_migrated_cluster_reports_no_rls_gap(migrated_cluster: str) -> None:
    """Un schéma conforme ne produit aucun écart de RLS ni de migration."""

    report = collect(migrated_cluster, require_tls=False)
    rls_problems = [problem for problem in report["problems"] if problem.startswith("rls_")]
    assert rls_problems == [], f"écarts RLS inattendus: {rls_problems}"
    assert report["alembic_revision"] == "c73b12fd"
    assert report["checks"] == 9


def test_runtime_role_is_rejected_when_it_is_superuser(migrated_cluster: str) -> None:
    """Le rôle propriétaire ne doit pas servir de rôle runtime.

    Un runtime superutilisateur rend le RLS décoratif : il contourne toute
    politique. C'est exactement ce que A-10 demandait de rendre visible.
    """

    report = collect(migrated_cluster, require_tls=False)
    assert report["is_superuser"] is True
    assert "runtime_role_is_superuser" in report["problems"]
    assert "runtime_role_can_create_schema_objects" in report["problems"]


def test_application_role_has_no_ddl_privilege(migrated_cluster: str) -> None:
    """Le rôle applicatif ne peut pas créer d'objet de schéma."""

    report = collect(_runtime_dsn(migrated_cluster), require_tls=False, expected_role="vericlaim_app")
    assert report["role"] == "vericlaim_app"
    assert report["is_superuser"] is False
    assert report["can_create_schema_objects"] is False
    assert "runtime_role_can_create_schema_objects" not in report["problems"]


def test_wrong_expected_role_is_reported(migrated_cluster: str) -> None:
    """Un rôle inattendu est signalé, avec son nom — jamais la chaîne de connexion."""

    report = collect(_runtime_dsn(migrated_cluster), require_tls=False, expected_role="quelqu_un_dautre")
    assert any(problem.startswith("unexpected_role:") for problem in report["problems"])


def test_missing_alembic_table_is_a_problem_not_a_crash(migrated_cluster: str) -> None:
    """Une base non migrée donne un rapport d'écart, pas une trace d'exception."""

    import psycopg

    with psycopg.connect(migrated_cluster, autocommit=True) as connection:
        connection.execute("DROP TABLE alembic_version")

    report = collect(migrated_cluster, require_tls=False)
    assert report["alembic_revision"] is None
    assert "alembic_version_missing" in report["problems"]


def test_tls_is_mandatory_by_default(monkeypatch, capsys) -> None:
    """Sans TLS, le script refuse la connexion au lieu de l'accepter en clair.

    Exiger le TLS pour ensuite tolérer une connexion claire serait une
    affirmation sans contrôle : c'est le défaut que A-10 relève.
    """

    import socket

    # Port fermé : le refus est immédiat et ne dépend pas d'un serveur TLS.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    monkeypatch.setenv("DATABASE_RUNTIME_URL", f"postgresql://u:p@127.0.0.1:{port}/db")
    code = main(["--json"])
    output = capsys.readouterr().out
    assert code == 2
    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["problems"] == ["connection_failed"]
    # Ni mot de passe ni chaîne de connexion dans le rapport d'échec.
    assert ":p@" not in output
    assert "postgresql://u:p" not in output


def test_missing_dsn_does_not_guess_a_target(monkeypatch, capsys) -> None:
    """Pas de variable => sortie 2, aucune cible inventée, aucun secret affiché."""

    monkeypatch.delenv("DATABASE_RUNTIME_URL", raising=False)
    assert main([]) == 2
    assert "absente" in capsys.readouterr().err


def test_report_never_contains_the_connection_string(migrated_cluster: str) -> None:
    """Le rapport est citable en revue : ni hôte, ni mot de passe, ni DSN."""

    import contextlib
    import io

    dsn = _runtime_dsn(migrated_cluster)
    buffer = io.StringIO()
    import verify_runtime_identity as module

    previous = os.environ.get("DATABASE_RUNTIME_URL")
    os.environ["DATABASE_RUNTIME_URL"] = dsn
    try:
        with contextlib.redirect_stdout(buffer):
            module.main(["--allow-no-tls", "--json"])
    finally:
        if previous is None:
            os.environ.pop("DATABASE_RUNTIME_URL", None)
        else:
            os.environ["DATABASE_RUNTIME_URL"] = previous

    rendered = buffer.getvalue()
    assert "jetable" not in rendered, "un mot de passe a fuité dans le rapport"
    assert dsn not in rendered
    assert "vericlaim_app" in rendered  # le nom de rôle, lui, est utile


def test_production_settings_require_tls() -> None:
    """Le code de production refuse une URL runtime non chiffrée."""

    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(
            environment="production",
            database_url="postgresql://user:secret@db.example/vericlaim",
            secret_key="x" * 48,
            _env_file=None,
        )


@pytest.mark.skipif(
    not os.environ.get("VERICLAIM_STAGING_RUNTIME_URL"),
    reason="nécessite une URL de recette réelle (fournie par l'équipe de pilotage)",
)
def test_staging_runtime_url_is_tls_and_properly_privileged() -> None:
    """Preuve sur une vraie URL — exécutée seulement quand elle est fournie.

    Tant que cette variable n'est pas disponible, la preuve TLS réelle reste
    *non exécutée* : le rapport de mission le dit, il ne le maquille pas.
    """

    report = collect(
        os.environ["VERICLAIM_STAGING_RUNTIME_URL"],
        require_tls=True,
        expected_role=os.environ.get("VERICLAIM_EXPECTED_ROLE", "vericlaim_app"),
    )
    assert report["tls_in_use"] is True
    assert report["is_superuser"] is False
    assert "tls_inactive" not in report["problems"]
