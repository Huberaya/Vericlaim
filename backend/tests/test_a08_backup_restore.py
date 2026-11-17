"""A-08 : la sauvegarde est produite, vérifiée, restaurée et rétentionnée.

Le dépôt savait déjà restaurer (`tools/recette/restore.py`) ; il ne savait pas
produire une archive, la vérifier, ni décider ce qu'on garde. Ce module exerce
le cycle complet contre un **vrai** PostgreSQL 16 jetable fourni par
``pgserver``, puis vérifie les garanties qui comptent :

* le manifeste décrit exactement ce qui a été produit (taille + SHA-256) ;
* une archive altérée ou tronquée est refusée **avant** toute restauration ;
* la restauration cible une base neuve et préserve révision et compteurs ;
* la rétention ne vide jamais le magasin ;
* aucun identifiant ni chaîne de connexion n'apparaît dans les rapports.

Ce qui n'est pas prouvé ici, et que le script ne prétend pas faire : les
sauvegardes Neon (PITR, rétention côté fournisseur) et le rechargement des
objets S3 (seul leur *inventaire* est enregistré).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools" / "backup"))

from vericlaim_backup import (  # noqa: E402
    command_backup,
    command_prune,
    command_restore,
    command_verify,
    redact_dsn,
    sha256_file,
)


def _pg_bin_dir() -> Path | None:
    """Binaires PostgreSQL fournis par pgserver, ou None si absents."""

    try:
        import pgserver
    except ImportError:
        return None
    candidate = Path(pgserver.__file__).parent / "pginstall" / "bin"
    return candidate if (candidate / "pg_dump").exists() else None


@pytest.fixture()
def live_postgres(tmp_path: Path):
    """Cluster jetable réel, avec la table attendue par le script."""

    try:
        import pgserver
    except ImportError:  # pragma: no cover - dépendance optionnelle
        pytest.skip("pgserver absent")
    bin_dir = _pg_bin_dir()
    if bin_dir is None:  # pragma: no cover - dépendance optionnelle
        pytest.skip("binaires pg_dump/pg_restore absents")

    server = pgserver.get_server(str(tmp_path / "pgdata"))
    dsn = server.get_uri()
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32) primary key)")
        connection.execute("INSERT INTO alembic_version VALUES ('c73b12fd')")
        connection.execute("CREATE TABLE organizations (id uuid primary key)")
        connection.execute("CREATE TABLE users (id uuid primary key)")
        connection.execute("CREATE TABLE documents (id uuid primary key)")
        connection.execute("CREATE TABLE document_versions (id uuid primary key)")
        connection.execute("CREATE TABLE audit_events (id uuid primary key)")

    previous_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
    try:
        yield dsn
    finally:
        os.environ["PATH"] = previous_path


def _args(**kwargs):
    return type("Args", (), kwargs)()


def test_backup_produces_a_manifest_that_matches_the_archive(live_postgres, tmp_path, monkeypatch, capsys):
    """Le manifeste décrit ce qui existe réellement, taille et SHA-256."""

    monkeypatch.setenv("DATABASE_ADMIN_URL", live_postgres)
    code = command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="DATABASE_ADMIN_URL"))
    assert code == 0

    produced = json.loads(capsys.readouterr().out)
    directory = Path(produced["backup"])
    manifest = json.loads((directory / "manifest.json").read_text())

    assert manifest["alembic_revision"] == "c73b12fd"
    assert manifest["target"]["dbname"] != "?" or manifest["target"]["host"] != "?"
    # Aucune chaîne de connexion complète ni mot de passe dans le manifeste.
    serialized = json.dumps(manifest)
    assert "password" not in serialized.lower()
    assert live_postgres not in serialized

    dump = directory / "database.dump"
    assert dump.exists() and dump.stat().st_size > 0
    recorded = {item["name"]: item for item in manifest["artifacts"]}
    assert recorded["database.dump"]["sha256"] == sha256_file(dump)
    assert recorded["database.dump"]["bytes"] == dump.stat().st_size
    assert "objects.json" in recorded
    # Sans configuration S3, l'inventaire est vide — et le manifeste le dit.
    assert manifest["objects"] == 0


def test_verify_detects_a_truncated_archive(live_postgres, tmp_path, monkeypatch, capsys):
    """Une archive tronquée est refusée : c'est le contrôle qui manquait."""

    monkeypatch.setenv("DATABASE_ADMIN_URL", live_postgres)
    command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="DATABASE_ADMIN_URL"))
    directory = Path(json.loads(capsys.readouterr().out)["backup"])
    dump = directory / "database.dump"

    assert command_verify(_args(backup=str(directory))) == 0

    original = dump.read_bytes()
    dump.write_bytes(original[: len(original) // 2])
    capsys.readouterr()
    assert command_verify(_args(backup=str(directory))) == 2, "une troncature doit être détectée"
    report = json.loads(capsys.readouterr().out)
    assert report["verified"] is False
    assert any("taille" in problem for problem in report["problems"])


def test_verify_detects_a_modified_archive(live_postgres, tmp_path, monkeypatch, capsys):
    """Une altération de même taille est détectée par le SHA-256."""

    monkeypatch.setenv("DATABASE_ADMIN_URL", live_postgres)
    command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="DATABASE_ADMIN_URL"))
    directory = Path(json.loads(capsys.readouterr().out)["backup"])
    dump = directory / "database.dump"

    payload = bytearray(dump.read_bytes())
    payload[0] ^= 0xFF  # même longueur, contenu différent
    dump.write_bytes(bytes(payload))

    assert command_verify(_args(backup=str(directory))) == 2
    report = json.loads(capsys.readouterr().out)
    assert any("SHA-256" in problem for problem in report["problems"])


def test_restore_targets_a_new_database_and_preserves_the_revision(
    live_postgres, tmp_path, monkeypatch, capsys
):
    """La restauration ne touche pas la source et préserve révision + compteurs."""

    import psycopg

    monkeypatch.setenv("DATABASE_ADMIN_URL", live_postgres)
    with psycopg.connect(live_postgres, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO organizations (id) VALUES ('00000000-0000-0000-0000-000000000001')"
        )

    command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="DATABASE_ADMIN_URL"))
    directory = Path(json.loads(capsys.readouterr().out)["backup"])

    assert command_restore(_args(backup=str(directory), admin_dsn_env="DATABASE_ADMIN_URL")) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["restored_database"].startswith("vericlaim_restore_")
    assert report["restored_database"] != "vericlaim_restore_", "aucune base inventée"
    assert report["revision_preserved"] is True
    assert report["row_counts_match"] is True
    assert report["objects_in_manifest"] == 0

    # La base source n'a pas été modifiée par la restauration.
    with psycopg.connect(live_postgres) as connection:
        count = connection.execute("SELECT count(*) FROM organizations").fetchone()[0]
    assert count == 1


def test_restore_refuses_without_a_target(live_postgres, tmp_path, monkeypatch, capsys):
    """Sans variable de cible isolée, aucune restauration n'est tentée."""

    monkeypatch.setenv("DATABASE_ADMIN_URL", live_postgres)
    command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="DATABASE_ADMIN_URL"))
    directory = Path(json.loads(capsys.readouterr().out)["backup"])

    monkeypatch.delenv("MISSING_TARGET_URL", raising=False)
    with pytest.raises(SystemExit):
        command_restore(_args(backup=str(directory), admin_dsn_env="MISSING_TARGET_URL"))


def test_backup_refuses_without_a_dsn(tmp_path, monkeypatch):
    """Pas de cible => pas de sauvegarde : le script n'invente rien."""

    monkeypatch.delenv("ABSENT_URL", raising=False)
    with pytest.raises(SystemExit):
        command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="ABSENT_URL"))


def test_retention_never_empties_the_store(tmp_path, capsys):
    """La rétention garde toujours les N derniers jeux, même tous anciens.

    Une politique qui supprime tout est une perte de données déguisée en
    hygiène : c'est précisément le cas que ce test verrouille.
    """

    backups = tmp_path / "backups"
    backups.mkdir()
    names = [
        "backup-20200101T000000Z",
        "backup-20200102T000000Z",
        "backup-20200103T000000Z",
        "backup-20200104T000000Z",
    ]
    for name in names:
        (backups / name).mkdir()
        (backups / name / "manifest.json").write_text("{}")

    assert command_prune(_args(directory=str(backups), keep_last=2, max_age_days=0)) == 0
    remaining = sorted(path.name for path in backups.glob("backup-*"))
    assert remaining == names[-2:], f"la rétention a supprimé trop: {remaining}"


def test_redact_dsn_never_exposes_credentials() -> None:
    """Le rapport ne doit contenir ni mot de passe ni chaîne complète."""

    dsn = "postgresql://vericlaim_app:super-secret@db.example:5432/vericlaim?sslmode=require"
    redacted = redact_dsn(dsn)
    assert redacted["host"] == "db.example"
    assert redacted["dbname"] == "vericlaim"
    assert redacted["sslmode"] == "require"
    assert "super-secret" not in json.dumps(redacted)
    assert "vericlaim_app" in redacted["user"]
