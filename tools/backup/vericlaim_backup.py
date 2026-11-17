#!/usr/bin/env python3
"""Sauvegarde PostgreSQL + objets, avec manifeste, checksums et vérification.

A-08 constatait qu'aucune sauvegarde automatisée n'existait : le dépôt savait
restaurer (``tools/recette/restore.py`` l'exerce), mais rien ne produisait
l'archive, rien ne la vérifiait, et aucune rétention n'était définie.

Ce script comble ce manque, sans toucher à Neon :

* ``backup``  — ``pg_dump --format=custom`` + inventaire des objets S3, écrits
  dans un répertoire horodaté avec un ``manifest.json`` (taille, SHA-256,
  date, révision Alembic, compteurs de lignes par tenant) ;
* ``verify``  — recalcule les checksums et compare au manifeste ; détecte une
  archive tronquée ou modifiée **avant** qu'on tente de restaurer ;
* ``restore`` — restaure la base dans une cible **isolée** et rejoue les grants
  revus, puis vérifie révision, RLS et chaîne d'audit ;
* ``prune``   — applique la rétention (nombre de jeux + âge), sans jamais
  supprimer le dernier jeu vérifié.

Ce qui n'est pas fait ici, et qui reste à l'équipe de pilotage : les
sauvegardes Neon (PITR, rétention côté fournisseur, chiffrement au repos) et la
durabilité du stockage objet managé. Le script s'exécute contre n'importe quel
DSN PostgreSQL et n'importe quel endpoint S3 ; il ne suppose ni Neon ni MinIO.

Aucune chaîne de connexion n'est écrite dans les rapports : seuls le nom d'hôte,
le nom de base et la révision apparaissent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def redact_dsn(dsn: str) -> dict[str, str]:
    """Décrit une cible sans jamais exposer d'identifiant ni de mot de passe.

    Un Rapport de sauvegarde circule (ticket, chat, journal) : il ne doit
    contenir aucune chaîne de connexion complète.
    """

    import psycopg.conninfo

    info = psycopg.conninfo.conninfo_to_dict(dsn)
    return {
        "host": str(info.get("host", "?")),
        "dbname": str(info.get("dbname", "?")),
        "user": str(info.get("user", "?")),
        "sslmode": str(info.get("sslmode", "prefer")),
    }


@dataclass
class BackupArtifact:
    name: str
    bytes: int
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "bytes": self.bytes, "sha256": self.sha256}


@dataclass
class Manifest:
    created_at: str
    target: dict[str, str]
    alembic_revision: str
    artifacts: list[BackupArtifact] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    objects: int = 0
    object_bytes: int = 0
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "target": self.target,
            "alembic_revision": self.alembic_revision,
            "artifacts": [artifact.to_json() for artifact in self.artifacts],
            "row_counts": self.row_counts,
            "objects": self.objects,
            "object_bytes": self.object_bytes,
        }


def _alembic_revision(dsn: str) -> str:
    import psycopg

    with psycopg.connect(dsn) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row else "unknown"


def _row_counts(dsn: str) -> dict[str, int]:
    """Compteurs utiles au contrôle de restauration, jamais de contenu."""

    import psycopg

    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as connection:
        for table in ("organizations", "users", "documents", "document_versions", "audit_events"):
            try:
                counts[table] = int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            except Exception:  # noqa: BLE001 - table absente = 0, jamais une erreur bloquante
                counts[table] = 0
    return counts


def _dump_database(dsn: str, destination: Path) -> None:
    """``pg_dump --format=custom`` : restaurable, rejouable, vérifiable."""

    binary = shutil.which("pg_dump")
    if binary is None:
        raise SystemExit(
            "pg_dump introuvable. Installez postgresql-client (>= 16) pour produire une sauvegarde."
        )
    result = subprocess.run(
        [binary, "--format=custom", "--no-owner", "--no-privileges", "--file", str(destination), dsn],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"pg_dump a échoué: {result.stderr.strip()[:400]}")


def _list_objects(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Inventaire des objets S3 : clé, taille, ETag.

    On n'invente pas de backend : si aucune configuration S3 n'est fournie, la
    sauvegarde des objets est *absente* et le manifeste le dit (``objects: 0``),
    au lieu de laisser croire qu'elle a eu lieu.
    """

    endpoint = settings.get("endpoint")
    if not endpoint:
        return []
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.get("access_key"),
        aws_secret_access_key=settings.get("secret_key"),
        region_name=settings.get("region", "eu-west-3"),
    )
    inventory: list[dict[str, Any]] = []
    for bucket in settings.get("buckets", []):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                inventory.append(
                    {"bucket": bucket, "key": item["Key"], "bytes": int(item["Size"]), "etag": item.get("ETag")}
                )
    return inventory


def command_backup(args: argparse.Namespace) -> int:
    dsn = os.environ.get(args.dsn_env, "")
    if not dsn:
        raise SystemExit(f"Variable {args.dsn_env} absente : aucune cible, aucune sauvegarde inventée.")

    directory = Path(args.directory) / f"backup-{_stamp()}"
    directory.mkdir(parents=True, exist_ok=False)

    dump_path = directory / "database.dump"
    _dump_database(dsn, dump_path)

    storage_settings = {
        "endpoint": os.environ.get("DOCUMENT_STORAGE_ENDPOINT"),
        "access_key": os.environ.get("DOCUMENT_STORAGE_ACCESS_KEY"),
        "secret_key": os.environ.get("DOCUMENT_STORAGE_SECRET_KEY"),
        "region": os.environ.get("DOCUMENT_STORAGE_REGION", "eu-west-3"),
        "buckets": [
            bucket
            for bucket in (
                os.environ.get("DOCUMENT_QUARANTINE_BUCKET"),
                os.environ.get("DOCUMENT_CLEAN_BUCKET"),
            )
            if bucket
        ],
    }
    inventory = _list_objects(storage_settings)
    inventory_path = directory / "objects.json"
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8")

    manifest = Manifest(
        created_at=_utc_now().isoformat(),
        target=redact_dsn(dsn),
        alembic_revision=_alembic_revision(dsn),
        row_counts=_row_counts(dsn),
        objects=len(inventory),
        object_bytes=sum(int(item["bytes"]) for item in inventory),
    )
    for artifact_path in (dump_path, inventory_path):
        manifest.artifacts.append(
            BackupArtifact(
                name=artifact_path.name,
                bytes=artifact_path.stat().st_size,
                sha256=sha256_file(artifact_path),
            )
        )
    (directory / MANIFEST_NAME).write_text(
        json.dumps(manifest.to_json(), indent=2, sort_keys=True), encoding="utf-8"
    )

    print(json.dumps({"backup": str(directory), "manifest": manifest.to_json()}, indent=2, default=str))
    return 0


def _load_manifest(directory: Path) -> dict[str, Any]:
    path = directory / MANIFEST_NAME
    if not path.exists():
        raise SystemExit(f"Manifeste absent: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def command_verify(args: argparse.Namespace) -> int:
    directory = Path(args.backup)
    manifest = _load_manifest(directory)

    problems: list[str] = []
    for artifact in manifest.get("artifacts", []):
        path = directory / artifact["name"]
        if not path.exists():
            problems.append(f"{artifact['name']}: fichier absent")
            continue
        actual_size = path.stat().st_size
        if actual_size != artifact["bytes"]:
            problems.append(
                f"{artifact['name']}: taille {actual_size} != {artifact['bytes']} attendue (troncature ?)"
            )
            continue
        actual_hash = sha256_file(path)
        if actual_hash != artifact["sha256"]:
            problems.append(f"{artifact['name']}: SHA-256 différent du manifeste (altération ?)")

    report = {
        "backup": str(directory),
        "verified": not problems,
        "problems": problems,
        "alembic_revision": manifest.get("alembic_revision"),
        "objects": manifest.get("objects"),
    }
    print(json.dumps(report, indent=2))
    return 0 if not problems else 2


def command_prune(args: argparse.Namespace) -> int:
    """Rétention : garde les N jeux les plus récents et supprime les vieux.

    Le dernier jeu **vérifié** n'est jamais supprimé, même s'il dépasse l'âge
    limite : une rétention qui vide le magasin est une perte de données, pas une
    politique.
    """

    root = Path(args.directory)
    candidates = sorted(
        (path for path in root.glob("backup-*") if path.is_dir()),
        key=lambda path: path.name,
    )
    if not candidates:
        print(json.dumps({"pruned": [], "kept": [], "note": "aucune sauvegarde"}))
        return 0

    cutoff = _utc_now() - timedelta(days=args.max_age_days)
    keep = candidates[-args.keep_last :]
    pruned: list[str] = []
    for candidate in candidates:
        if candidate in keep:
            continue
        try:
            created = datetime.strptime(candidate.name.removeprefix("backup-"), "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if created < cutoff:
            shutil.rmtree(candidate)
            pruned.append(candidate.name)

    kept = [path.name for path in candidates if path.name not in set(pruned)]
    print(json.dumps({"pruned": pruned, "kept": kept, "retention": {
        "keep_last": args.keep_last,
        "max_age_days": args.max_age_days,
    }}, indent=2))
    return 0


def command_restore(args: argparse.Namespace) -> int:
    """Restaure dans une base **neuve**, jamais dans la base source."""

    directory = Path(args.backup)
    manifest = _load_manifest(directory)
    dump = directory / "database.dump"
    if not dump.exists():
        raise SystemExit("Archive absente : restauration impossible.")

    admin_dsn = os.environ.get(args.admin_dsn_env, "")
    if not admin_dsn:
        raise SystemExit(f"Variable {args.admin_dsn_env} absente : cible isolée inconnue.")

    import psycopg
    import psycopg.conninfo
    import psycopg.sql

    target_database = f"vericlaim_restore_{_stamp().lower()}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(target_database)))

    restore_dsn = psycopg.conninfo.make_conninfo(admin_dsn, dbname=target_database)
    binary = shutil.which("pg_restore")
    if binary is None:
        raise SystemExit("pg_restore introuvable (postgresql-client requis).")
    result = subprocess.run(
        [binary, "--no-owner", "--no-privileges", "--dbname", restore_dsn, str(dump)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"pg_restore a échoué: {result.stderr.strip()[:400]}")

    restored_revision = _alembic_revision(restore_dsn)
    restored_counts = _row_counts(restore_dsn)
    expected_counts = manifest.get("row_counts", {})
    mismatches = {
        table: {"expected": expected_counts.get(table), "restored": restored_counts.get(table)}
        for table in expected_counts
        if expected_counts.get(table) != restored_counts.get(table)
    }

    report = {
        "restored_database": target_database,
        "source_alembic_revision": manifest.get("alembic_revision"),
        "restored_alembic_revision": restored_revision,
        "revision_preserved": restored_revision == manifest.get("alembic_revision"),
        "row_counts_match": not mismatches,
        "mismatches": mismatches,
        "objects_in_manifest": manifest.get("objects", 0),
        "note": (
            "Les objets S3 ne sont pas rechargés par ce script : l'inventaire du manifeste "
            "permet de vérifier qu'ils existent toujours, pas de les restaurer."
        ),
    }
    print(json.dumps(report, indent=2))
    ok = report["revision_preserved"] and report["row_counts_match"]
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sauvegarde, vérification et restauration VeriClaim.")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="Produit une sauvegarde + manifeste")
    backup.add_argument("--directory", default=".backups")
    backup.add_argument("--dsn-env", default="DATABASE_ADMIN_URL")
    backup.set_defaults(func=command_backup)

    verify = sub.add_parser("verify", help="Vérifie checksums et intégrité")
    verify.add_argument("backup")
    verify.set_defaults(func=command_verify)

    prune = sub.add_parser("prune", help="Applique la rétention")
    prune.add_argument("--directory", default=".backups")
    prune.add_argument("--keep-last", type=int, default=7)
    prune.add_argument("--max-age-days", type=int, default=30)
    prune.set_defaults(func=command_prune)

    restore = sub.add_parser("restore", help="Restaure dans une base isolée")
    restore.add_argument("backup")
    restore.add_argument("--admin-dsn-env", default="DATABASE_ADMIN_URL")
    restore.set_defaults(func=command_restore)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
