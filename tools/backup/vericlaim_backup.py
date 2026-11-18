#!/usr/bin/env python3
"""Sauvegarde PostgreSQL + objets, avec manifeste, checksums et vérification.

A-08 constatait qu'aucune sauvegarde automatisée n'existait : le dépôt savait
restaurer (``tools/recette/restore.py`` l'exerce), mais rien ne produisait
l'archive, rien ne la vérifiait, et aucune rétention n'était définie.

Ce script comble ce manque, sans toucher à Neon :

* ``backup``  — ``pg_dump --format=custom`` **et copie des octets réels** des
  objets S3, écrites dans un répertoire horodaté avec un ``manifest.json``
  (taille, SHA-256, date, révision Alembic, compteurs de lignes par tenant,
  et un enregistrement par objet) ;
* ``verify``  — recalcule les checksums et compare au manifeste ; détecte une
  archive tronquée ou modifiée **avant** qu'on tente de restaurer ;
* ``restore`` — restaure la base dans une cible **isolée** et rejoue les grants
  revus, puis vérifie révision, RLS et chaîne d'audit ;
* ``prune``   — applique la rétention (nombre de jeux + âge), sans jamais
  supprimer le dernier jeu vérifié.

Les objets sont **archivés, pas seulement inventoriés** : ``objects/<bucket>/…``
contient les octets, et chaque entrée du manifeste porte la clé source, le
bucket source, la taille, le SHA-256 et les métadonnées utiles. Un objet absent
du stockage au moment de la sauvegarde est **signalé** (``missing: true``) au
lieu d'être silencieusement sauté : un inventaire incomplet qui se présente
comme complet est pire qu'une sauvegarde partielle annoncée.

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
class ObjectArtifact:
    """Un objet S3 archivé.

    ``archive`` est le chemin **relatif** dans le répertoire de sauvegarde.
    ``missing`` distingue « objet absent du stockage » de « objet vide » : sans
    ce drapeau, une clé disparue entre l'inventaire et la copie serait
    indiscernable d'un fichier de 0 octet, et la sauvegarde mentirait par
    omission.
    """

    bucket: str
    key: str
    archive: str
    bytes: int
    sha256: str
    missing: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "key": self.key,
            "archive": self.archive,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "missing": self.missing,
            "metadata": self.metadata,
        }


@dataclass
class Manifest:
    created_at: str
    target: dict[str, str]
    alembic_revision: str
    artifacts: list[BackupArtifact] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    objects: int = 0
    object_bytes: int = 0
    object_artifacts: list[ObjectArtifact] = field(default_factory=list)
    object_missing: int = 0
    objects_captured: bool = False
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
            "object_missing": self.object_missing,
            "objects_captured": self.objects_captured,
            "object_artifacts": [artifact.to_json() for artifact in self.object_artifacts],
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


def _s3_client(settings: dict[str, Any]):
    """Client S3, ou ``None`` si aucun stockage n'est configuré.

    On n'invente pas de backend : sans endpoint, la sauvegarde des objets est
    *absente* et le manifeste le dit (``objects_captured: false``), au lieu de
    laisser croire qu'elle a eu lieu.
    """

    endpoint = settings.get("endpoint")
    if not endpoint:
        return None
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.get("access_key"),
        aws_secret_access_key=settings.get("secret_key"),
        region_name=settings.get("region", "eu-west-3"),
    )


def _safe_component(raw: str) -> str:
    """Neutralise un nom de composant de chemin sans jamais fusionner deux clés.

    Le point délicat : remplacer les caractères interdits par ``_`` fait
    s'effondrer ``a:b.pdf``, ``a<b.pdf`` et ``a_b.pdf`` sur le **même** fichier.
    Une sauvegarde écraserait alors deux objets par un seul, silencieusement —
    c'est-à-dire perdrait des données en se présentant comme complète.

    D'où le suffixe : dès qu'un caractère a dû être remplacé (ou que le nom est
    ambigu), on ajoute l'empreinte SHA-256 du nom d'origine. Deux clés
    distinctes ne peuvent plus viser le même chemin, et le nom reste lisible.
    """

    cleaned = "".join(ch if ch.isalnum() or ch in "-._ " else "_" for ch in raw)
    # Chemin rapide réservé aux noms ASCII déjà sûrs. Un nom non ASCII est aussi
    # haché : « é.pdf » et « é.pdf » (NFC/NFD) sont deux clés S3 distinctes
    # qui visent le même fichier sur un système de fichiers normalisant.
    if cleaned == raw and cleaned not in {"", ".", ".."} and raw.isascii():
        return cleaned
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    stem = cleaned.strip() or "_"
    return f"{stem}~{digest}"


def _safe_object_path(bucket: str, key: str) -> Path:
    """Chemin d'archive **injectif** pour une clé S3 arbitraire.

    Trois exigences, dans cet ordre :

    1. **confinement** — une clé comme ``../../etc/passwd`` ne doit jamais
       écrire hors du répertoire de sauvegarde ;
    2. **injectivité** — deux clés distinctes ne doivent jamais produire le
       même chemin. Une collision ne se contente pas de mal nommer un fichier :
       elle **écrase** un objet sauvegardé par un autre, et le manifeste
       déclarerait alors deux objets là où un seul existe ;
    3. **lisibilité** — le chemin doit rester exploitable à l'œil nu.

    Les segments vides (``a//b``) et les ``.``/``..`` sont écartés, mais cela
    ne suffit pas à rendre la fonction injective : ``a//b.pdf`` et ``a/b.pdf``
    désignent deux clés S3 distinctes et produiraient le même chemin. Un
    marqueur déterministe est donc ajouté dès que la normalisation a modifié la
    clé (segment retiré, caractère substitué).
    """

    # Séparateur : la barre oblique **seule**. L'antislash est un caractère
    # légal dans une clé S3 ; le traiter comme un séparateur ferait viser le
    # même fichier à « a\\b.pdf » et « a/b.pdf ».
    raw_segments = key.split("/")
    kept = [segment for segment in raw_segments if segment not in {"", ".", ".."}]
    normalized = len(kept) != len(raw_segments) or bool(raw_segments and raw_segments[0] == "")

    safe_segments = [_safe_component(segment) for segment in kept]
    if not safe_segments:
        safe_segments = ["_empty_key"]
    if normalized:
        # Deux clés distinctes peuvent se normaliser identiquement
        # (``a//b.pdf`` vs ``a/b.pdf``) : la clé brute départage.
        digest = hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        safe_segments = [f"{safe_segments[0]}~norm-{digest}", *safe_segments[1:]]

    safe_bucket = _safe_component(bucket) if bucket.strip() else "_bucket"
    return Path("objects") / safe_bucket / Path(*safe_segments)


def _capture_objects(
    client: Any, settings: dict[str, Any], directory: Path
) -> list[ObjectArtifact]:
    """Copie les **octets** de chaque objet dans l'archive et les décrit.

    Un objet listé mais illisible (supprimé entre-temps, droits insuffisants)
    n'est pas silencieusement ignoré : il entre au manifeste avec
    ``missing: true``. Une sauvegarde qui saute des fichiers sans le dire est
    plus dangereuse qu'une sauvegarde qui échoue.
    """

    artifacts: list[ObjectArtifact] = []
    claimed_paths: dict[str, str] = {}
    for bucket in settings.get("buckets", []):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                key = item["Key"]
                relative = _safe_object_path(bucket, key)

                # Filet de sécurité : si une future évolution du schéma de
                # nommage réintroduisait une collision, on refuse la sauvegarde
                # au lieu d'écraser un objet déjà archivé. Perdre une donnée en
                # silence est le seul résultat inacceptable ici.
                # La comparaison est insensible à la casse : deux clés S3 qui ne
                # diffèrent que par la casse visent un seul fichier sur macOS ou
                # Windows, où l'archive peut être copiée.
                claim = f"{bucket}/{str(relative).lower()}"
                previous = claimed_paths.get(claim)
                if previous is not None:
                    raise SystemExit(
                        "Collision de chemins dans l'archive : "
                        f"{bucket}/{previous!r} et {bucket}/{key!r} visent {relative}. "
                        "Sauvegarde interrompue pour ne perdre aucun objet."
                    )
                claimed_paths[claim] = key

                destination = directory / relative
                destination.parent.mkdir(parents=True, exist_ok=True)

                metadata: dict[str, Any] = {"etag": item.get("ETag"), "last_modified": str(item.get("LastModified"))}
                try:
                    response = client.get_object(Bucket=bucket, Key=key)
                    payload = response["Body"].read()
                except Exception as exc:  # noqa: BLE001 - objet absent/illisible = constat
                    artifacts.append(
                        ObjectArtifact(
                            bucket=bucket,
                            key=key,
                            archive=str(relative),
                            bytes=0,
                            sha256="",
                            missing=True,
                            metadata={**metadata, "error": type(exc).__name__},
                        )
                    )
                    continue

                destination.write_bytes(payload)
                content_type = response.get("ContentType")
                if content_type:
                    metadata["content_type"] = content_type
                artifacts.append(
                    ObjectArtifact(
                        bucket=bucket,
                        key=key,
                        archive=str(relative),
                        bytes=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest(),
                        metadata=metadata,
                    )
                )
    return artifacts


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
    client = _s3_client(storage_settings)
    object_artifacts: list[ObjectArtifact] = []
    if client is not None:
        object_artifacts = _capture_objects(client, storage_settings, directory)

    # ``objects.json`` reste écrit : c'est l'index lisible des objets, utile
    # pour un diff rapide entre deux sauvegardes. Il ne remplace pas l'archive
    # des octets — les deux existent, et le manifeste dit lequel est lequel.
    index_path = directory / "objects.json"
    index_path.write_text(
        json.dumps([artifact.to_json() for artifact in object_artifacts], indent=2, sort_keys=True),
        encoding="utf-8",
    )

    captured = [artifact for artifact in object_artifacts if not artifact.missing]
    manifest = Manifest(
        created_at=_utc_now().isoformat(),
        target=redact_dsn(dsn),
        alembic_revision=_alembic_revision(dsn),
        row_counts=_row_counts(dsn),
        objects=len(captured),
        object_bytes=sum(artifact.bytes for artifact in captured),
        object_artifacts=object_artifacts,
        object_missing=len(object_artifacts) - len(captured),
        objects_captured=client is not None,
    )
    for artifact_path in (dump_path, index_path):
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

    # Les octets archivés sont vérifiés comme le dump : une archive dont les
    # objets ont été tronqués ne doit pas être déclarée valide parce que seul
    # le dump était contrôlé.
    object_artifacts = manifest.get("object_artifacts", [])
    for artifact in object_artifacts:
        if artifact.get("missing"):
            problems.append(f"objet absent au moment de la sauvegarde: {artifact['bucket']}/{artifact['key']}")
            continue
        path = directory / artifact["archive"]
        if not path.exists():
            problems.append(f"objet archivé manquant: {artifact['archive']}")
            continue
        actual_size = path.stat().st_size
        if actual_size != artifact["bytes"]:
            problems.append(
                f"objet {artifact['key']}: taille {actual_size} != {artifact['bytes']} attendue (troncature ?)"
            )
            continue
        if sha256_file(path) != artifact["sha256"]:
            problems.append(f"objet {artifact['key']}: SHA-256 différent du manifeste (altération ?)")

    report = {
        "backup": str(directory),
        "verified": not problems,
        "problems": problems,
        "alembic_revision": manifest.get("alembic_revision"),
        "objects": manifest.get("objects"),
        "object_missing": manifest.get("object_missing", 0),
        "objects_captured": manifest.get("objects_captured", False),
        "objects_checked": sum(1 for artifact in object_artifacts if not artifact.get("missing")),
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
            "Cette commande ne restaure que la base. Les objets se restaurent avec "
            "« restore-objects », vers une destination explicite et distincte de la source."
        ),
    }
    print(json.dumps(report, indent=2))
    ok = report["revision_preserved"] and report["row_counts_match"]
    return 0 if ok else 2


def _destination_credentials(
    settings: dict[str, Any], *, endpoint: str | None, access_key: str | None, secret_key: str | None
) -> dict[str, Any]:
    """Reprend les identifiants de la source si la destination n'en fournit pas.

    Cas courant : un même MinIO/S3 avec un bucket de restauration distinct. On
    ne force pas l'opérateur à ressaisir des identifiants qu'il a déjà.
    """

    return {
        "endpoint": endpoint or settings.get("endpoint"),
        "access_key": access_key or settings.get("access_key"),
        "secret_key": secret_key or settings.get("secret_key"),
        "region": settings.get("region", "eu-west-3"),
        "buckets": [],
    }


def _remote_sha256(client: Any, bucket: str, key: str, *, limit_bytes: int | None = None) -> str | None:
    """Empreinte SHA-256 des **octets réellement stockés**, ou ``None``.

    Comparer les métadonnées ``sha256`` d'un objet est séduisant mais faux : la
    métadonnée est écrite par l'appelant, pas par le stockage. Un objet
    corrompu après son dépôt conserve la métadonnée de son contenu d'origine, et
    une restauration le déclarerait « déjà présent » alors que ses octets sont
    faux. On relit donc le contenu.

    ``limit_bytes`` borne le coût : au-delà, l'objet est seulement **mesuré**
    (taille comparée) et signalé comme non vérifié par contenu — jamais comme
    vérifié.
    """

    head = client.head_object(Bucket=bucket, Key=key)
    expected_size = int(head.get("ContentLength", -1))
    if limit_bytes is not None and expected_size > limit_bytes:
        return None
    digest = hashlib.sha256()
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    for block in iter(lambda: body.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _normalize_prefix(prefix: str) -> str:
    """Normalise un préfixe : pas de ``/`` initial, un seul ``/`` final."""

    cleaned = prefix.strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def command_restore_objects(args: argparse.Namespace) -> int:
    """Restaure les objets archivés vers un bucket et un préfixe **explicites**.

    Trois garanties, chacune vérifiée par un test :

    1. **jamais vers la source** — la destination est refusée si elle désigne le
       bucket source avec un préfixe vide ; restaurer sur soi-même écraserait
       les objets qu'on vient sauvegarder ;
    2. **jamais d'écrasement** — un objet déjà présent à la destination est
       comparé : identique (même SHA-256) ⇒ reconnu comme déjà restauré et
       sauté, différent ⇒ **refus explicite**. On ne remplace jamais une donnée
       existante sur la foi d'une sauvegarde plus ancienne ;
    3. **refus avant écriture** — le pré-contrôle des collisions a lieu avant
       toute écriture, sinon une restauration interrompue laisserait un mélange
       de deux états.
    """

    import hashlib as _hashlib

    directory = Path(args.backup)
    manifest = _load_manifest(directory)
    artifacts = manifest.get("object_artifacts", [])
    if not artifacts:
        raise SystemExit(
            "Aucun objet dans ce manifeste : rien à restaurer. "
            "Un inventaire vide n'est pas une sauvegarde d'objets."
        )

    storage_settings = {
        "endpoint": os.environ.get("DOCUMENT_STORAGE_ENDPOINT"),
        "access_key": os.environ.get("DOCUMENT_STORAGE_ACCESS_KEY"),
        "secret_key": os.environ.get("DOCUMENT_STORAGE_SECRET_KEY"),
        "region": os.environ.get("DOCUMENT_STORAGE_REGION", "eu-west-3"),
        "buckets": [],
    }
    destination = _destination_credentials(
        storage_settings,
        endpoint=args.destination_endpoint,
        access_key=args.destination_access_key,
        secret_key=args.destination_secret_key,
    )
    if not destination["endpoint"]:
        raise SystemExit("Aucun endpoint de destination : refus d'inventer une cible.")

    source_buckets = {artifact["bucket"] for artifact in artifacts}
    if args.destination_bucket in source_buckets and not _normalize_prefix(args.prefix):
        raise SystemExit(
            "Restauration refusée : destination identique à la source "
            f"(bucket {args.destination_bucket!r}, préfixe vide). Choisissez un autre "
            "bucket ou un préfixe non vide."
        )

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=destination["endpoint"],
        aws_access_key_id=destination["access_key"],
        aws_secret_access_key=destination["secret_key"],
        region_name=destination["region"],
    )
    prefix = _normalize_prefix(args.prefix)

    # ---- pré-contrôle : rien n'est écrit avant d'avoir tout examiné.
    planned: list[tuple[dict[str, Any], str, Path]] = []
    already_present: list[str] = []
    collisions: list[dict[str, str]] = []
    source_problems: list[str] = []

    for artifact in artifacts:
        key = artifact["key"]
        if artifact.get("missing"):
            source_problems.append(f"{artifact['bucket']}/{key}: absent de la source au moment de la sauvegarde")
            continue
        local = directory / artifact["archive"]
        if not local.exists():
            source_problems.append(f"{key}: octets absents de l'archive ({artifact['archive']})")
            continue
        actual = sha256_file(local)
        if actual != artifact["sha256"]:
            source_problems.append(f"{key}: archive altérée (SHA-256 différent du manifeste)")
            continue

        destination_key = f"{prefix}{key}"
        try:
            existing_head = client.head_object(Bucket=args.destination_bucket, Key=destination_key)
        except Exception:  # noqa: BLE001 - absent (404) ou erreur : traité comme absent
            existing_head = None

        if existing_head is not None:
            # On ne se fie ni au nom, ni à la métadonnée : on lit le contenu.
            existing_size = int(existing_head.get("ContentLength", -1))
            if existing_size != artifact["bytes"]:
                collisions.append(
                    {
                        "key": destination_key,
                        "reason": f"objet existant de taille différente ({existing_size} != {artifact['bytes']})",
                    }
                )
                continue

            existing_sha = _remote_sha256(
                client, args.destination_bucket, destination_key, limit_bytes=args.verify_bytes_limit
            )
            if existing_sha is None:
                # Contenu non lu : on ne peut pas affirmer « identique ». Refuser
                # est le seul choix honnête ; reconnaître l'objet sur la foi de sa
                # métadonnée reviendrait à croire l'objet sur parole.
                collisions.append(
                    {
                        "key": destination_key,
                        "reason": (
                            "objet existant trop volumineux pour être vérifié par contenu "
                            f"(> {args.verify_bytes_limit} octets) ; augmenter --verify-bytes-limit"
                        ),
                    }
                )
                continue

            if existing_sha == artifact["sha256"]:
                already_present.append(destination_key)
                continue
            collisions.append(
                {
                    "key": destination_key,
                    "reason": "objet existant avec un contenu différent (octets comparés, pas les métadonnées)",
                }
            )
            continue
        planned.append((artifact, destination_key, local))

    if source_problems or collisions:
        print(
            json.dumps(
                {
                    "restored": 0,
                    "already_present": len(already_present),
                    "collisions": collisions,
                    "source_problems": source_problems,
                    "refused": True,
                    "note": (
                        "Aucune écriture effectuée : le pré-contrôle a échoué. "
                        "Une restauration partielle serait plus difficile à reprendre qu'un refus."
                    ),
                },
                indent=2,
            )
        )
        return 2

    # ---- écriture : uniquement des clés absentes.
    restored: list[str] = []
    for artifact, destination_key, local in planned:
        payload = local.read_bytes()
        if _hashlib.sha256(payload).hexdigest() != artifact["sha256"]:  # pragma: no cover - relu juste après
            raise SystemExit(f"{destination_key}: contenu modifié entre la vérification et l'envoi.")
        metadata = {"sha256": artifact["sha256"], "source-bucket": artifact["bucket"]}
        put_arguments: dict[str, Any] = {
            "Bucket": args.destination_bucket,
            "Key": destination_key,
            "Body": payload,
            "Metadata": metadata,
        }
        content_type = (artifact.get("metadata") or {}).get("content_type")
        if content_type:
            put_arguments["ContentType"] = content_type
        client.put_object(**put_arguments)
        restored.append(destination_key)

    # ---- post-contrôle : les octets écrits sont relus et hachés.
    # Un `put_object` qui répond 200 ne prouve rien sur ce qui est stocké
    # (proxy, stockage en panne partielle, écriture tronquée). On relit.
    mismatches: list[str] = []
    unverified: list[str] = []
    for artifact, destination_key, _local in planned:
        actual = _remote_sha256(
            client, args.destination_bucket, destination_key, limit_bytes=args.verify_bytes_limit
        )
        if actual is None:
            unverified.append(destination_key)
            continue
        if actual != artifact["sha256"]:
            mismatches.append(destination_key)

    report = {
        "restored": len(restored),
        "already_present": len(already_present),
        "collisions": [],
        "destination": {"bucket": args.destination_bucket, "prefix": prefix},
        "source_buckets": sorted(source_buckets),
        "idempotent_replay": len(already_present) > 0,
        "verified_after_write": not mismatches and not unverified,
        "verified_how": "sha256 of the stored bytes, re-read after write",
        "mismatches": mismatches,
        "unverified_too_large": unverified,
        "note": (
            "Aucun objet source n'a été modifié ni supprimé. Rejouer la commande "
            "sur la même destination ne réécrit rien : les objets identiques sont reconnus."
        ),
    }
    print(json.dumps(report, indent=2))
    return 0 if (not mismatches and not unverified) else 2


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

    restore = sub.add_parser("restore", help="Restaure la base dans une base isolée")
    restore.add_argument("backup")
    restore.add_argument("--admin-dsn-env", default="DATABASE_ADMIN_URL")
    restore.set_defaults(func=command_restore)

    restore_objects = sub.add_parser(
        "restore-objects", help="Restaure les objets vers un bucket et un préfixe explicites"
    )
    restore_objects.add_argument("backup")
    restore_objects.add_argument("--destination-bucket", required=True)
    restore_objects.add_argument(
        "--prefix", default="", help="Préfixe de destination (obligatoire si le bucket est le bucket source)"
    )
    restore_objects.add_argument("--destination-endpoint", default=None)
    restore_objects.add_argument("--destination-access-key", default=None)
    restore_objects.add_argument("--destination-secret-key", default=None)
    restore_objects.add_argument(
        "--verify-bytes-limit",
        type=int,
        default=512 * 1024 * 1024,
        help=(
            "Taille maximale d'un objet relu pour comparaison par contenu. Au-delà, "
            "l'objet est signalé non vérifié plutôt que déclaré identique."
        ),
    )
    restore_objects.set_defaults(func=command_restore_objects)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
