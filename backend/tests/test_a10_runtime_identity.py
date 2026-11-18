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


# ---------------------------------------------------------------------------
# Sentinelles : aucun échec ne doit jamais restituer de donnée de connexion.
#
# Ces tests sont volontairement paranoïaques. Un rapport de vérification finit
# collé dans un ticket ou un canal de discussion ; s'il transporte l'hôte, le
# port, l'utilisateur ou le mot de passe, la vérification de sécurité devient
# elle-même la fuite. On injecte des valeurs reconnaissables dans *chaque*
# partie de la chaîne de connexion, puis on inspecte **stdout et stderr**.
# ---------------------------------------------------------------------------

from verify_runtime_identity import TlsRequiredError, classify_error  # noqa: E402

SENTINEL_PASSWORD = "SENTINEL-PASSWORD-42"
SENTINEL_USER = "sentinel_user"
SENTINEL_HOST = "sentinel-host.example"
SENTINEL_DB = "sentinel_db"
SENTINEL_OPTION = "SENTINELOPT42"


def _sentinel_dsn(port: int) -> str:
    return (
        f"postgresql://{SENTINEL_USER}:{SENTINEL_PASSWORD}@{SENTINEL_HOST}:{port}/{SENTINEL_DB}"
        f"?sslmode=require&connect_timeout=2&options=-c%20application_name%3D{SENTINEL_OPTION}"
    )


def _captured_run(monkeypatch, dsn: str) -> tuple[int, str, str]:
    """Exécute le script en capturant séparément stdout et stderr."""

    import contextlib
    import io

    import verify_runtime_identity as module

    monkeypatch.setenv("DATABASE_RUNTIME_URL", dsn)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(["--json"])
    return code, out.getvalue(), err.getvalue()


def _assert_no_leak(code: int, out: str, err: str, *, expected_code: str) -> None:
    assert code == 2, f"un échec de connexion doit sortir en 2, obtenu {code}"
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["problems"] == [expected_code], payload
    assert payload.get("error_code") == expected_code

    blob = out + err
    for sentinel in (
        SENTINEL_PASSWORD,
        SENTINEL_USER,
        SENTINEL_HOST,
        SENTINEL_DB,
        SENTINEL_OPTION,
        "postgresql://",
        "sslmode",
    ):
        assert sentinel not in blob, f"fuite de {sentinel!r} dans la sortie: {blob!r}"
    # Aucun champ « detail » ne doit réapparaître : c'est par lui que le message
    # du pilote s'échappait.
    assert "detail" not in payload
    assert "traceback" not in blob.lower()
    assert err == "", f"un échec attendu ne doit rien écrire sur stderr, obtenu: {err!r}"


def test_sentinel_connection_failure_never_leaks(monkeypatch) -> None:
    """1. Hôte injoignable : le pilote cite l'hôte, la sortie ne doit pas."""

    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # Host sentinelle + port fermé : psycopg échoue à la résolution avant le
    # réseau. Le message brut contiendrait sentinel-host.example.
    code, out, err = _captured_run(monkeypatch, _sentinel_dsn(port))
    _assert_no_leak(code, out, err, expected_code="connection_failed")


def test_sentinel_connection_refused_never_leaks(monkeypatch) -> None:
    """1 bis. Connexion refusée : le pilote cite hôte ET port."""

    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Le nom doit rester résoluble, sinon on retombe sur l'échec de résolution.
    dsn = (
        f"postgresql://{SENTINEL_USER}:{SENTINEL_PASSWORD}@localhost:{port}/{SENTINEL_DB}"
        f"?sslmode=require&connect_timeout=2"
    )
    code, out, err = _captured_run(monkeypatch, dsn)
    _assert_no_leak(code, out, err, expected_code="connection_failed")
    assert str(port) not in out, "le port ne doit pas apparaître"


def test_sentinel_authentication_failure_never_leaks(tmp_path, monkeypatch) -> None:
    """2. Échec d'authentification **réel**, sur un vrai serveur PostgreSQL.

    On ne simule pas : le cluster jetable est reconfiguré en ``scram-sha-256``,
    un rôle sentinelle est créé, puis on présente un **mauvais** mot de passe.
    psycopg répond alors « password authentication failed for user
    "sentinel_user" » — c'est-à-dire le rôle **et** l'hôte dans le message brut.
    Rien de tout cela ne doit ressortir.
    """

    psycopg = pytest.importorskip("psycopg")
    pgserver = pytest.importorskip("pgserver")

    import pathlib

    data_dir = pathlib.Path(tmp_path) / "pgdata"
    server = pgserver.get_server(str(data_dir))
    admin_dsn = server.get_uri()

    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(f"CREATE ROLE {SENTINEL_USER} LOGIN PASSWORD '{SENTINEL_PASSWORD}'")

    # Le cluster de test accepte « trust » sur la socket locale : on force une
    # authentification par mot de passe pour produire un vrai refus à
    # authentification, et non un refus réseau.
    hba = data_dir / "pg_hba.conf"
    original = hba.read_text()
    rewritten = original.replace(
        "local   all             all                                     trust",
        "local   all             all                                     scram-sha-256",
    )
    assert rewritten != original, "le cluster de test n'a pas la ligne 'local ... trust' attendue"
    hba.write_text(rewritten)
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute("select pg_reload_conf()")

    wrong_password_dsn = admin_dsn.replace(
        "postgresql://postgres:", f"postgresql://{SENTINEL_USER}:WRONG-{SENTINEL_PASSWORD}"
    )

    # Preuve que l'échec est bien une authentification, pas un réseau coupé.
    with pytest.raises(psycopg.OperationalError) as excinfo:
        psycopg.connect(wrong_password_dsn, connect_timeout=3)
    raw = str(excinfo.value)
    assert "authentication" in raw.lower() or "password" in raw.lower(), raw

    code, out, err = _captured_run(monkeypatch, wrong_password_dsn)
    assert code == 2
    payload = json.loads(out)
    assert payload["problems"] == ["authentication_failed"], payload
    blob = out + err
    for sentinel in (
        SENTINEL_PASSWORD,
        SENTINEL_USER,
        "postgresql://",
        "password authentication",
        "pgdata",
        "postgres",
    ):
        assert sentinel not in blob, f"fuite de {sentinel!r}: {blob!r}"


def test_sentinel_unparseable_connection_string_never_leaks(monkeypatch) -> None:
    """3. Chaîne de connexion illisible : le parsing échoue avant tout réseau."""

    malformed = f"postgresql://{SENTINEL_USER}:{SENTINEL_PASSWORD}@{SENTINEL_HOST}/db?sslmode=totally-invalid-{SENTINEL_OPTION}"
    code, out, err = _captured_run(monkeypatch, malformed)
    assert code == 2
    payload = json.loads(out)
    # Le code dépend du point où psycopg refuse (parsing vs connexion) ; ce qui
    # compte est qu'il soit stable, connu, et sans contenu.
    assert payload["error_code"] in {"connection_string_invalid", "connection_failed"}, payload
    blob = out + err
    for sentinel in (SENTINEL_PASSWORD, SENTINEL_USER, SENTINEL_HOST, SENTINEL_OPTION, "postgresql://"):
        assert sentinel not in blob, f"fuite de {sentinel!r}: {blob!r}"
    assert "detail" not in payload


def test_sentinel_tls_refusal_never_leaks(monkeypatch) -> None:
    """TLS exigé et absent : le message du script ne reprend pas le DSN."""

    import verify_runtime_identity as module

    def refusing(dsn: str, *, require_tls: bool):
        raise module.TlsRequiredError("TLS inactif")

    monkeypatch.setattr(module, "_connect", refusing)
    code, out, err = _captured_run(monkeypatch, _sentinel_dsn(5432))
    assert code == 2
    payload = json.loads(out)
    assert payload["error_code"] == "tls_inactive", payload
    for sentinel in (SENTINEL_PASSWORD, SENTINEL_USER, SENTINEL_HOST, "postgresql://"):
        assert sentinel not in out + err


def test_sentinel_unexpected_exception_never_leaks(monkeypatch) -> None:
    """Une exception inattendue portant le DSN ne doit rien restituer.

    C'est le cas le plus dangereux : un ``RuntimeError`` construit ailleurs, dont
    le texte contient la chaîne de connexion. La version précédente l'imprimait
    telle quelle dans ``problems``.
    """

    import verify_runtime_identity as module

    def leaky(dsn: str, *, require_tls: bool):
        raise RuntimeError(f"connexion refusée pour {dsn}")

    monkeypatch.setattr(module, "_connect", leaky)
    code, out, err = _captured_run(monkeypatch, _sentinel_dsn(5432))
    assert code == 2
    payload = json.loads(out)
    assert payload["error_code"] == "unexpected_error", payload
    blob = out + err
    for sentinel in (SENTINEL_PASSWORD, SENTINEL_USER, SENTINEL_HOST, "postgresql://"):
        assert sentinel not in blob, f"fuite par exception inattendue: {blob!r}"


def test_every_error_code_is_declared() -> None:
    """Aucun code ne peut être émis sans être déclaré dans ``ERROR_CODES``.

    Sans ce garde, un futur chemin d'erreur pourrait inventer un code contenant
    un message, et le contrat « seuls des codes stables sortent » se perdrait
    silencieusement.
    """

    import verify_runtime_identity as module

    assert "connection_failed" in module.ERROR_CODES
    assert len(set(module.ERROR_CODES)) == len(module.ERROR_CODES)
    for code in module.ERROR_CODES:
        assert code.islower() and " " not in code and ":" not in code, code


def test_classify_error_uses_codes_only() -> None:
    """Le classifieur ne retourne que des codes, jamais un fragment de message."""

    import verify_runtime_identity as module

    assert classify_error(TlsRequiredError("x")) == "tls_inactive"
    assert classify_error(RuntimeError("boom")) == "unexpected_error"
    assert classify_error(ValueError("postgresql://u:p@h/d")) == "unexpected_error"
    for exc in (TlsRequiredError("x"), RuntimeError("y"), ValueError("z")):
        assert classify_error(exc) in module.ERROR_CODES
