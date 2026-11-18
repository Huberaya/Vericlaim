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

import hashlib
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
    command_restore_objects,
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


# ---------------------------------------------------------------------------
# Objets S3 : capture des octets, vérification et restauration effective.
#
# Moto sert de **serveur S3 réel** (processus séparé, protocole HTTP, boto3
# standard), pas d'un mock en mémoire. Cela prouve le contrat : écriture,
# lecture, ETag, métadonnées, absences.
#
# Ce que cela ne prouve PAS : la durabilité, la réplication, le versioning ou
# le chiffrement d'un stockage managé réel. Aucune restauration vers un bucket
# de production n'a été effectuée, et ce fichier ne prétend pas le contraire.
# ---------------------------------------------------------------------------

S3_REGION = "us-east-1"


@pytest.fixture()
def s3_server(tmp_path: Path):
    """Serveur moto en processus, avec trois buckets propres à ce test."""

    moto = pytest.importorskip("moto", reason="moto requis pour le contrat S3")
    boto3 = pytest.importorskip("boto3")

    import socket
    import subprocess
    import time

    assert moto.__version__  # le module est bien installé

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(
        [sys.executable, "-m", "moto.server", "-H", "127.0.0.1", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    endpoint = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:  # pragma: no cover - environnement défaillant
        process.terminate()
        pytest.skip("serveur moto indisponible")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name=S3_REGION,
    )
    for bucket in ("vericlaim-quarantine", "vericlaim-clean", "vericlaim-restore"):
        client.create_bucket(Bucket=bucket)

    try:
        yield type("S3", (), {"endpoint": endpoint, "client": client, "process": process})()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()


def _storage_env(monkeypatch, endpoint: str) -> None:
    monkeypatch.setenv("DOCUMENT_STORAGE_ENDPOINT", endpoint)
    monkeypatch.setenv("DOCUMENT_STORAGE_ACCESS_KEY", "test")
    monkeypatch.setenv("DOCUMENT_STORAGE_SECRET_KEY", "test")
    monkeypatch.setenv("DOCUMENT_STORAGE_REGION", S3_REGION)
    monkeypatch.setenv("DOCUMENT_QUARANTINE_BUCKET", "vericlaim-quarantine")
    monkeypatch.setenv("DOCUMENT_CLEAN_BUCKET", "vericlaim-clean")


def _pg_binaries_on_path(monkeypatch) -> None:
    """pg_dump/pg_restore fournis par pgserver (CI incluse)."""

    bin_dir = _pg_bin_dir()
    if bin_dir is None:  # pragma: no cover
        pytest.skip("binaires PostgreSQL absents")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _backup_with_objects(tmp_path: Path, live_postgres: str, monkeypatch, capsys) -> Path:
    monkeypatch.setenv("DATABASE_ADMIN_URL", live_postgres)
    _pg_binaries_on_path(monkeypatch)
    command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="DATABASE_ADMIN_URL"))
    produced = json.loads(capsys.readouterr().out)
    return Path(produced["backup"])


def _restore_args(directory: Path, *, bucket="vericlaim-restore", prefix="restored/", endpoint=None):
    return _args(
        backup=str(directory),
        destination_bucket=bucket,
        prefix=prefix,
        destination_endpoint=endpoint,
        destination_access_key="test",
        destination_secret_key="test",
    )


def test_one_object_is_archived_with_its_bytes(tmp_path, live_postgres, s3_server, monkeypatch, capsys) -> None:
    """Un objet : ses OCTETS sont dans l'archive, pas seulement son nom."""

    _storage_env(monkeypatch, s3_server.endpoint)
    payload = b"%PDF-1.7 contenu-probant-d-un-seul-objet"
    s3_server.client.put_object(Bucket="vericlaim-quarantine", Key="uploads/one.pdf", Body=payload)

    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)
    manifest = json.loads((directory / "manifest.json").read_text())

    assert manifest["objects_captured"] is True
    assert manifest["objects"] == 1
    assert manifest["object_bytes"] == len(payload)
    recorded = manifest["object_artifacts"][0]
    assert recorded["bucket"] == "vericlaim-quarantine"
    assert recorded["key"] == "uploads/one.pdf"
    assert recorded["bytes"] == len(payload)
    assert recorded["sha256"] == hashlib.sha256(payload).hexdigest()
    assert recorded["missing"] is False

    archived = directory / recorded["archive"]
    assert archived.exists(), "les octets doivent être présents dans l'archive"
    assert archived.read_bytes() == payload, "le contenu archivé doit être identique à la source"

    assert command_verify(_args(backup=str(directory))) == 0


def test_several_objects_across_buckets_are_archived(tmp_path, live_postgres, s3_server, monkeypatch, capsys) -> None:
    """Plusieurs objets, deux buckets : tous capturés, aucun confondu."""

    _storage_env(monkeypatch, s3_server.endpoint)
    fixtures = {
        ("vericlaim-quarantine", "q/a.pdf"): b"AAA",
        ("vericlaim-quarantine", "q/b.pdf"): b"BBBB",
        ("vericlaim-clean", "c/deep/path/c.bin"): b"\x00\x01\x02" * 500,
    }
    for (bucket, key), body in fixtures.items():
        s3_server.client.put_object(Bucket=bucket, Key=key, Body=body)

    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)
    manifest = json.loads((directory / "manifest.json").read_text())

    assert manifest["objects"] == len(fixtures)
    assert manifest["object_bytes"] == sum(len(body) for body in fixtures.values())
    captured = {(item["bucket"], item["key"]): item for item in manifest["object_artifacts"]}
    assert set(captured) == set(fixtures)
    for (bucket, key), body in fixtures.items():
        item = captured[(bucket, key)]
        assert item["sha256"] == hashlib.sha256(body).hexdigest()
        assert (directory / item["archive"]).read_bytes() == body
    # Les deux buckets ne se mélangent pas dans l'archive.
    assert len({item["archive"] for item in manifest["object_artifacts"]}) == 3


def test_missing_source_object_is_reported_not_skipped(tmp_path, live_postgres, s3_server, monkeypatch) -> None:
    """Objet listé mais illisible : signalé `missing`, jamais silencieusement omis.

    C'est le cas réel d'une clé supprimée entre le listing et la copie. Une
    sauvegarde qui l'ignorerait se présenterait comme complète : l'omission
    silencieuse est plus dangereuse que l'échec.
    """

    import vericlaim_backup as module

    settings = {
        "endpoint": s3_server.endpoint,
        "access_key": "test",
        "secret_key": "test",
        "region": S3_REGION,
        "buckets": ["vericlaim-clean"],
    }
    s3_server.client.put_object(Bucket="vericlaim-clean", Key="c/disappears.pdf", Body=b"present")

    class VanishingClient:
        """Liste l'objet (comme un listing réel) puis refuse de le lire."""

        def __init__(self, client):
            self._client = client

        def get_paginator(self, name):
            return self._client.get_paginator(name)

        def get_object(self, **_kwargs):
            raise KeyError("NoSuchKey")

    artifacts = module._capture_objects(VanishingClient(s3_server.client), settings, tmp_path)

    assert len(artifacts) == 1, "l'objet ne doit pas être retiré de la sauvegarde"
    assert artifacts[0].missing is True
    assert artifacts[0].bytes == 0
    assert artifacts[0].metadata["error"] == "KeyError"
    assert not (tmp_path / artifacts[0].archive).exists()


def test_capture_refuses_to_escape_the_backup_directory(tmp_path, s3_server) -> None:
    """Une clé S3 hostile ne peut pas écrire hors du répertoire de sauvegarde."""

    import vericlaim_backup as module

    settings = {
        "endpoint": s3_server.endpoint,
        "access_key": "test",
        "secret_key": "test",
        "region": S3_REGION,
        "buckets": ["vericlaim-clean"],
    }
    hostile_key = "../../../etc/passwd"
    s3_server.client.put_object(Bucket="vericlaim-clean", Key=hostile_key, Body=b"x")
    artifacts = module._capture_objects(s3_server.client, settings, tmp_path)

    assert len(artifacts) == 1
    archived = (tmp_path / artifacts[0].archive).resolve()
    assert tmp_path.resolve() in archived.parents, f"écriture hors périmètre: {archived}"
    assert ".." not in Path(artifacts[0].archive).parts


def test_verify_detects_a_corrupted_object_archive(tmp_path, live_postgres, s3_server, monkeypatch, capsys) -> None:
    """Archive d'objet corrompue : `verify` refuse, même si le dump est intact."""

    _storage_env(monkeypatch, s3_server.endpoint)
    s3_server.client.put_object(Bucket="vericlaim-clean", Key="c/corruptible.pdf", Body=b"contenu original")
    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)
    manifest = json.loads((directory / "manifest.json").read_text())
    archived = directory / manifest["object_artifacts"][0]["archive"]

    assert command_verify(_args(backup=str(directory))) == 0

    archived.write_bytes(b"contenu falsifie")
    capsys.readouterr()
    assert command_verify(_args(backup=str(directory))) == 2, "une altération d'objet doit être détectée"
    report = json.loads(capsys.readouterr().out)
    assert any("SHA-256" in problem for problem in report["problems"]), report["problems"]


def test_verify_detects_a_wrong_object_checksum(tmp_path, live_postgres, s3_server, monkeypatch, capsys) -> None:
    """Checksum erroné dans le manifeste : détecté, pas ignoré."""

    _storage_env(monkeypatch, s3_server.endpoint)
    s3_server.client.put_object(Bucket="vericlaim-clean", Key="c/checksum.pdf", Body=b"corps")
    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)

    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["object_artifacts"][0]["sha256"] = "0" * 64  # empreinte volontairement fausse
    manifest_path.write_text(json.dumps(manifest))

    assert command_verify(_args(backup=str(directory))) == 2
    report = json.loads(capsys.readouterr().out)
    assert any("SHA-256" in problem for problem in report["problems"])


def test_restore_writes_objects_to_a_distinct_destination(
    tmp_path, live_postgres, s3_server, monkeypatch, capsys
) -> None:
    """Restauration effective vers un bucket et un préfixe distincts."""

    _storage_env(monkeypatch, s3_server.endpoint)
    bodies = {"q/alpha.pdf": b"%PDF alpha", "c/beta.pdf": b"%PDF beta"}
    s3_server.client.put_object(Bucket="vericlaim-quarantine", Key="q/alpha.pdf", Body=bodies["q/alpha.pdf"])
    s3_server.client.put_object(Bucket="vericlaim-clean", Key="c/beta.pdf", Body=bodies["c/beta.pdf"])
    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)

    assert command_restore_objects(
        _restore_args(directory, bucket="vericlaim-restore", prefix="restored/")
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["restored"] == 2
    assert report["verified_after_write"] is True
    assert report["collisions"] == []

    written = {
        item["Key"]: item for item in s3_server.client.list_objects_v2(Bucket="vericlaim-restore").get("Contents", [])
    }
    assert set(written) == {"restored/q/alpha.pdf", "restored/c/beta.pdf"}
    for key, body in bodies.items():
        restored = s3_server.client.get_object(Bucket="vericlaim-restore", Key=f"restored/{key}")["Body"].read()
        assert restored == body, "les octets restaurés doivent être identiques aux originaux"


def test_replaying_restore_is_idempotent(tmp_path, live_postgres, s3_server, monkeypatch, capsys) -> None:
    """Rejouer la restauration ne réécrit rien : identique ⇒ reconnu."""

    _storage_env(monkeypatch, s3_server.endpoint)
    s3_server.client.put_object(Bucket="vericlaim-clean", Key="c/idem.pdf", Body=b"idempotent")
    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)

    assert command_restore_objects(_restore_args(directory, prefix="replay/")) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["restored"] == 1

    assert command_restore_objects(_restore_args(directory, prefix="replay/")) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["restored"] == 0, "un rejeu ne doit rien réécrire"
    assert second["already_present"] == 1
    assert second["idempotent_replay"] is True


def test_collision_with_a_different_existing_object_is_refused(
    tmp_path, live_postgres, s3_server, monkeypatch, capsys
) -> None:
    """Objet différent à la même destination : refus explicite, rien d'écrasé."""

    _storage_env(monkeypatch, s3_server.endpoint)
    s3_server.client.put_object(Bucket="vericlaim-clean", Key="c/clash.pdf", Body=b"version sauvegardee")
    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)

    # Destination occupée par un contenu différent, sans empreinte comparable.
    s3_server.client.put_object(Bucket="vericlaim-restore", Key="clash/c/clash.pdf", Body=b"version recente")
    capsys.readouterr()

    assert command_restore_objects(_restore_args(directory, prefix="clash/")) == 2, "une collision doit être refusée"
    report = json.loads(capsys.readouterr().out)
    assert report["refused"] is True
    assert report["restored"] == 0
    assert len(report["collisions"]) == 1

    survivor = s3_server.client.get_object(Bucket="vericlaim-restore", Key="clash/c/clash.pdf")["Body"].read()
    assert survivor == b"version recente", "l'objet existant ne doit jamais être écrasé"


def test_restore_refuses_to_target_the_source(tmp_path, live_postgres, s3_server, monkeypatch, capsys) -> None:
    """Restaurer sur la source (même bucket, préfixe vide) est refusé."""

    _storage_env(monkeypatch, s3_server.endpoint)
    s3_server.client.put_object(Bucket="vericlaim-quarantine", Key="q/source.pdf", Body=b"source intacte")
    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)

    with pytest.raises(SystemExit) as excinfo:
        command_restore_objects(
            _restore_args(directory, bucket="vericlaim-quarantine", prefix="", endpoint=s3_server.endpoint)
        )
    assert "identique à la source" in str(excinfo.value)

    # Un préfixe non vide dans le bucket source reste possible : ce n'est plus
    # la source des objets, seulement un bucket qui la contient.
    assert command_restore_objects(
        _restore_args(directory, bucket="vericlaim-quarantine", prefix="copie/", endpoint=s3_server.endpoint)
    ) == 0


def test_sources_are_never_modified_nor_deleted(tmp_path, live_postgres, s3_server, monkeypatch, capsys) -> None:
    """Après sauvegarde ET restauration, les buckets source sont inchangés."""

    _storage_env(monkeypatch, s3_server.endpoint)
    originals = {"q/one.pdf": b"un", "q/two.pdf": b"deux"}
    for key, body in originals.items():
        s3_server.client.put_object(Bucket="vericlaim-quarantine", Key=key, Body=body)
    before = {
        item["Key"]: s3_server.client.head_object(Bucket="vericlaim-quarantine", Key=item["Key"])["ETag"]
        for item in s3_server.client.list_objects_v2(Bucket="vericlaim-quarantine")["Contents"]
    }
    directory = _backup_with_objects(tmp_path, live_postgres, monkeypatch, capsys)

    assert command_restore_objects(_restore_args(directory, prefix="out/")) == 0
    capsys.readouterr()

    listing = s3_server.client.list_objects_v2(Bucket="vericlaim-quarantine").get("Contents", [])
    after = {item["Key"]: item["ETag"] for item in listing}
    assert after == before, "la source doit être strictement inchangée (ni ajout, ni suppression, ni réécriture)"
    for key, body in originals.items():
        assert s3_server.client.get_object(Bucket="vericlaim-quarantine", Key=key)["Body"].read() == body


def test_restore_objects_refuses_a_manifest_without_captured_objects(
    tmp_path, live_postgres, monkeypatch, capsys
) -> None:
    """Un inventaire vide n'est pas une sauvegarde d'objets : refus explicite."""

    monkeypatch.delenv("DOCUMENT_STORAGE_ENDPOINT", raising=False)
    monkeypatch.setenv("DATABASE_ADMIN_URL", live_postgres)
    _pg_binaries_on_path(monkeypatch)
    command_backup(_args(directory=str(tmp_path / "backups"), dsn_env="DATABASE_ADMIN_URL"))
    produced = json.loads(capsys.readouterr().out)
    directory = Path(produced["backup"])
    assert produced["manifest"]["objects_captured"] is False

    with pytest.raises(SystemExit) as excinfo:
        command_restore_objects(_restore_args(directory, endpoint="http://127.0.0.1:9"))
    assert "Aucun objet" in str(excinfo.value)
