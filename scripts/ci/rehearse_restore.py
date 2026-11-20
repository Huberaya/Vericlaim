#!/usr/bin/env python3
"""C24 — répétition de restauration : sauvegarde, restauration, tête, test de fumée.

Le runbook de migration affirmait depuis C2 : « Avant toute donnée client, exécuter et consigner
un exercice de restauration Neon/PITR sur un environnement isolé. » **Aucun exercice n'existait**
(le fichier de test lui-même ne vérifiait que la présence de la phrase dans le document), et la
phrase restait donc un vœu. Ce script est l'exercice.

Il commence par mesurer deux pièges, parce qu'ils déterminent toute la procédure :

* une sauvegarde logique faite avec le rôle de migration **échoue** (« query would be affected by
  row-level security policy ») ;
* la même sauvegarde avec `--enable-row-security` **réussit** et **perd silencieusement toutes les
  lignes dépendantes d'un locataire** : la restauration rend une base qui contient les tables, les
  organisations, et **aucun document ni événement d'audit**.

Conséquence, et c'est la règle que la répétition installe : sauvegarder cette base exige un rôle
dédié porteur de `BYPASSRLS`. C'est ce que la répétition vérifie ensuite de bout en bout :

1. migration de la source jusqu'à la tête Alembic ;
2. données métier écrites **par le produit** (organisation, document, événements d'audit chaînés) ;
3. mesure des deux pièges ci-dessus ;
4. sauvegarde avec le rôle de sauvegarde ;
5. base cible éphémère ;
6. restauration ;
7. grants post-restauration rejoués (un dump logique ne transporte ni propriétaires ni privilèges) ;
8. `infra/neon/verify_runtime.py` sur la base restaurée : tête, rôle runtime, RLS, moindre privilège ;
9. test de fumée du produit : la chaîne d'audit restaurée est vérifiée par `verify_audit_chain` ;
10. comparaison des volumes source/cible **lue par un rôle qui voit tout** ;
11. retour arrière répété à blanc : base détruite puis reconstruite depuis la sauvegarde.

Code de sortie 1 si une étape échoue, transcript JSON complet (jamais de secret).

    python scripts/ci/rehearse_restore.py \\
        --source-url 'postgresql+psycopg://vericlaim_migrator:…@127.0.0.1:5432/vericlaim?sslmode=require' \\
        --backup-url 'postgresql+psycopg://vericlaim_backup:…@127.0.0.1:5432/vericlaim?sslmode=require' \\
        --target-db vericlaim_rehearsal --app-password-env VERICLAIM_APP_PASSWORD
"""

from __future__ import annotations

import argparse
import json
import os
import re
import pathlib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = REPOSITORY_ROOT / "backend"
INFRA_NEON = REPOSITORY_ROOT / "infra" / "neon"

#: Tables dont la restauration doit être complète : elles portent la valeur métier et la preuve.
BUSINESS_TABLES = (
    "organizations",
    "users",
    "documents",
    "document_versions",
    "document_segments",
    "audit_events",
)

SEED_SLUG = "rehearsal-c24"
SEED_MARKER = "sauvegarde-c24"


def with_database(url: str, database: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, ""))


def libpq(url: str) -> str:
    """libpq n'accepte pas le marqueur de pilote SQLAlchemy."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def with_app_role(url: str, password: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, f"vericlaim_app:{password}@{host}", parsed.path, parsed.query, ""))


def backup_role_problem(
    *,
    bypassrls: str,
    tables_readable: int,
    tables_total: int,
    sequences_readable: int,
    sequences_total: int,
) -> str | None:
    """Rend la raison du refus, ou `None` si le rôle de sauvegarde est utilisable.

    Fonction pure, séparée de la lecture de `pg_roles` **pour être éprouvable sans base** : une
    garde qu'on ne peut tester qu'en cassant un serveur PostgreSQL est une garde qu'on ne teste pas.
    """
    if bypassrls != "t":
        return (
            "le rôle de sauvegarde ne porte pas BYPASSRLS : sur ce schéma, une sauvegarde "
            "logique échouerait ou perdrait les lignes dépendantes d'un locataire"
        )
    if tables_readable != tables_total or sequences_readable != sequences_total:
        return (
            "le rôle de sauvegarde ne lit pas toutes les tables et séquences : "
            f"tables {tables_readable}/{tables_total}, séquences {sequences_readable}/{sequences_total}"
        )
    return None


@dataclass
class Rehearsal:
    source_url: str
    backup_url: str
    admin_url: str
    target_database: str
    app_password: str | None
    pg_bin: pathlib.Path
    workdir: pathlib.Path
    expected_head: str = ""
    steps: list[dict[str, object]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    # -- journalisation ----------------------------------------------------

    def record(self, step: str, **payload: object) -> None:
        self.steps.append({"step": step, **payload})
        print(f"### {step}")
        for key, value in payload.items():
            print(f"    {key}: {value}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"    ÉCHEC : {message}", file=sys.stderr)

    # -- accès base --------------------------------------------------------

    def psql(self, url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.pg_bin / "psql"), libpq(url), *arguments], capture_output=True, text=True
        )

    def scalar(self, url: str, query: str) -> str:
        result = self.psql(url, "-tAc", query)
        if result.returncode != 0:
            raise RuntimeError(f"psql a échoué : {result.stderr.strip()[:400]}")
        return result.stdout.strip()

    def counts(self, url: str, tables: tuple[str, ...] = BUSINESS_TABLES) -> dict[str, int]:
        return {table: int(self.scalar(url, f'select count(*) from "{table}"')) for table in tables}

    def recreate_database(self, name: str) -> None:
        # Créer une base éphémère est une opération d'administration : le rôle de migration ne
        # doit pas forcément en avoir le droit en production. La répétition l'exige, donc elle
        # le dit au lieu de le supposer (--admin-url, par défaut le rôle de migration).
        maintenance = with_database(self.admin_url, "postgres")
        dropped = self.psql(maintenance, "-v", "ON_ERROR_STOP=1", "-c",
                            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if dropped.returncode != 0:
            raise RuntimeError(f"suppression de {name} impossible : {dropped.stderr.strip()[:300]}")
        created = self.psql(maintenance, "-v", "ON_ERROR_STOP=1", "-c", f'CREATE DATABASE "{name}"')
        if created.returncode != 0:
            raise RuntimeError(f"création de {name} impossible : {created.stderr.strip()[:300]}")

    def pg_dump(self, url: str, destination: pathlib.Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(self.pg_bin / "pg_dump"),
                libpq(url),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                *extra,
                f"--file={destination}",
            ],
            capture_output=True,
            text=True,
        )

    def pg_restore(self, url: str, source: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(self.pg_bin / "pg_restore"),
                "--dbname", libpq(url),
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                str(source),
            ],
            capture_output=True,
            text=True,
        )

    def alembic(self, url: str, *arguments: str) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/root"),
                "APP_ENV": "development",
                "AUTO_CREATE_SCHEMA": "false",
                "DATABASE_URL": url,
            },
        )
        if result.returncode != 0:
            raise RuntimeError(f"alembic {' '.join(arguments)} a échoué : {result.stderr.strip()[:600]}")
        return result.stdout

    def backup_role(self) -> str:
        return urlsplit(self.backup_url).username or ""

    def grant_read_to_backup(self, database: str, *, restored: bool = False) -> None:
        """Le rôle de sauvegarde doit pouvoir lire **tables et séquences** de la base visée.

        Sur une base **restaurée**, les objets appartiennent au rôle qui a restauré
        (l'administration), pas au rôle de migration : accorder les droits depuis le rôle de
        migration échoue alors avec ``permission denied for table alembic_version`` — mesuré à
        la première exécution réelle de ce travail. La propriété est donc d'abord rendue au rôle
        de migration, comme l'exige la procédure de production : sans elle, les migrations
        suivantes ne pourraient plus s'appliquer non plus.
        """
        if restored:
            migration_role = urlsplit(self.source_url).username or ""
            # Le nom de rôle entre dans une commande SQL construite ici : on n'accepte qu'un
            # identifiant simple, jamais une chaîne quelconque venue d'une URL.
            if migration_role and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", migration_role):
                raise RuntimeError(f"nom de rôle de migration inattendu : {migration_role!r}")
            if migration_role:
                # `REASSIGN OWNED BY` est refusé ici : PostgreSQL protège les objets système du
                # rôle qui a restauré (« cannot reassign ownership of objects owned by role
                # postgres because they are required by the database system », mesuré). On ne
                # déplace donc que les **objets applicatifs** du schéma public — tables et
                # séquences —, ce qui est exactement ce qu'exige la suite de la procédure.
                outcome = self.psql(
                    with_database(self.admin_url, database),
                    "-v", "ON_ERROR_STOP=1", "-c",
                    "DO $$ DECLARE objet record; BEGIN "
                    "FOR objet IN SELECT tablename AS nom FROM pg_tables WHERE schemaname = 'public' LOOP "
                    f"EXECUTE format('ALTER TABLE public.%I OWNER TO %I', objet.nom, '{migration_role}'); "
                    "END LOOP; "
                    "FOR objet IN SELECT sequence_name AS nom FROM information_schema.sequences "
                    "WHERE sequence_schema = 'public' LOOP "
                    f"EXECUTE format('ALTER SEQUENCE public.%I OWNER TO %I', objet.nom, '{migration_role}'); "
                    "END LOOP; END $$;",
                )
                if outcome.returncode != 0:
                    raise RuntimeError(
                        "reprise de propriété par le rôle de migration impossible : "
                        f"{outcome.stderr.strip()[:300]}"
                    )
        migrator = with_database(self.source_url, database)
        statements = (
            f'GRANT USAGE ON SCHEMA public TO "{self.backup_role()}"',
            f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{self.backup_role()}"',
            f'GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO "{self.backup_role()}"',
        )
        for statement in statements:
            outcome = self.psql(migrator, "-v", "ON_ERROR_STOP=1", "-c", statement)
            if outcome.returncode != 0:
                raise RuntimeError(f"grant au rôle de sauvegarde impossible : {outcome.stderr.strip()[:300]}")

    def step_check_backup_role(self) -> None:
        """Contrôle du rôle de sauvegarde avant toute sauvegarde : la panne doit être lisible."""
        can_bypass = self.scalar(
            self.backup_url, f"select rolbypassrls from pg_roles where rolname = '{self.backup_role()}'"
        )
        tables = self.scalar(
            self.backup_url,
            "select count(*) from information_schema.tables where table_schema = 'public'",
        )
        readable = self.scalar(
            self.backup_url,
            "select count(*) from information_schema.tables t where t.table_schema = 'public' "
            f"and has_table_privilege('{self.backup_role()}', format('%I.%I', t.table_schema, t.table_name), 'SELECT')",
        )
        sequences = self.scalar(
            self.backup_url,
            "select count(*) from information_schema.sequences where sequence_schema = 'public'",
        )
        readable_sequences = self.scalar(
            self.backup_url,
            "select count(*) from information_schema.sequences s where s.sequence_schema = 'public' "
            f"and has_sequence_privilege('{self.backup_role()}', format('%I.%I', s.sequence_schema, s.sequence_name), 'SELECT')",
        )
        self.record(
            "contrôle du rôle de sauvegarde",
            role=self.backup_role(),
            bypassrls=can_bypass,
            tables_lisibles=f"{readable}/{tables}",
            sequences_lisibles=f"{readable_sequences}/{sequences}",
        )
        problem = backup_role_problem(
            bypassrls=can_bypass,
            tables_readable=int(readable),
            tables_total=int(tables),
            sequences_readable=int(readable_sequences),
            sequences_total=int(sequences),
        )
        if problem is not None:
            raise RuntimeError(problem)

    # -- étapes ------------------------------------------------------------

    def step_migrate_source(self) -> str:
        started = time.time()
        self.alembic(self.source_url, "upgrade", "head")
        head = self.scalar(self.source_url, "select version_num from alembic_version")
        self.expected_head = head
        self.record("migration de la source", tete_alembic=head, duree_s=round(time.time() - started, 2))
        return head

    def step_seed(self) -> str:
        """Écrit des données métier avec le code du produit, y compris une chaîne d'audit."""
        script = """
import json, sys
from uuid import uuid4
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.audit.service import verify_audit_chain
from app.identity.service import append_audit_event, set_db_request_context
from app.models.domain import Document, DocumentStatus, DocumentType, Organization, OrganizationStatus

url, slug, marker = sys.argv[1], sys.argv[2], sys.argv[3]
organization_id, user_id = uuid4(), uuid4()
engine = create_engine(url)

def context(db, organization):
    # `set_config(..., is_local=true)` ne vit que le temps d'une transaction : après chaque
    # validation, le contexte doit être reposé, sinon la politique RLS refuse l'écriture et
    # filtre les lectures. Défaut reproduit ici même, invisible en SQLite (aucune RLS).
    set_db_request_context(db, user_id=user_id, organization_id=organization)

with Session(engine) as db:
    organization = db.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        context(db, organization_id)
        organization = Organization(id=organization_id, name="Répétition C24", slug=slug,
                                    status=OrganizationStatus.ACTIVE, data_region="eu")
        db.add(organization)
        db.flush()
        db.commit()
    organization_id = organization.id

# La répétition doit être rejouable : les données ensemencées du tour précédent sont effacées
# d'abord, sinon les volumes comparés dépendent de l'historique des exécutions. Supprimer des
# événements d'audit est ici légitime — c'est une base de répétition, et la répétition doit
# pouvoir repartir d'un état connu ; le produit, lui, ne supprime jamais ces lignes (C20).
with Session(engine) as db:
    from sqlalchemy import delete
    from app.models.domain import AuditEvent
    context(db, organization_id)
    db.execute(delete(Document).where(Document.organization_id == organization_id))
    db.execute(delete(AuditEvent).where(AuditEvent.organization_id == organization_id))
    db.commit()

with Session(engine) as db:
    context(db, organization_id)
    db.add(Document(organization_id=organization_id, document_key=f"DOC-{uuid4().hex[:10]}",
                    title=marker, document_type=DocumentType.SUPPLIER_DECLARATION,
                    status=DocumentStatus.READY))
    db.commit()

with Session(engine) as db:
    context(db, organization_id)
    for index in range(3):
        append_audit_event(db, organization_id=organization_id, actor_user_id=None,
                           entity_type="document", entity_id=None, action="c24.rehearsal.event",
                           payload={"sequence": index, "marker": marker})
    db.commit()

with Session(engine) as db:
    context(db, organization_id)
    outcome = verify_audit_chain(db, organization_id)
    print(json.dumps({"organization_id": str(organization_id),
                      "chain_valid_before_backup": bool(outcome.is_valid),
                      "events_before_backup": int(outcome.total_events)}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, self.source_url, SEED_SLUG, SEED_MARKER],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/root"),
                "APP_ENV": "development",
                "AUTO_CREATE_SCHEMA": "false",
                "DATABASE_URL": self.source_url,
            },
        )
        if result.returncode != 0:
            raise RuntimeError(f"l'ensemencement a échoué : {result.stderr.strip()[-800:]}")
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.record("données métier écrites par le produit", **payload)
        if not payload["chain_valid_before_backup"] or payload["events_before_backup"] != 3:
            raise RuntimeError(
                "la source ensemencée ne porte pas les événements d'audit attendus : la répétition "
                "n'aurait rien à démontrer"
            )
        return payload["organization_id"]

    def step_measure_backup_traps(self) -> None:
        """Les deux pièges qui imposent un rôle de sauvegarde dédié : mesurés, pas supposés."""
        strict = self.workdir / "dump-role-migration.dump"
        outcome = self.pg_dump(self.source_url, strict)
        self.record(
            "sauvegarde avec le rôle de migration",
            reussie=outcome.returncode == 0,
            message=(outcome.stderr.strip().splitlines()[0] if outcome.stderr.strip() else ""),
        )

        permissive = self.workdir / "dump-sans-bypass.dump"
        outcome = self.pg_dump(self.source_url, permissive, "--enable-row-security")
        if outcome.returncode != 0:
            self.record(
                "sauvegarde avec --enable-row-security",
                reussie=False,
                message=outcome.stderr.strip()[:200],
            )
            return

        scratch = f"{self.target_database}_trap"
        self.recreate_database(scratch)
        # Restauration par le rôle d'administration, puis lecture par le rôle de sauvegarde : le
        # dump `--no-privileges` ne transporte aucun droit, et la lecture doit tout voir pour que
        # la comparaison ait un sens.
        restored = self.pg_restore(with_database(self.admin_url, scratch), permissive)
        if restored.returncode != 0:
            self.record(
                "sauvegarde avec --enable-row-security",
                reussie=True,
                restauration=False,
                message=restored.stderr.strip()[:200],
            )
            return
        self.grant_read_to_backup(scratch, restored=True)
        recovered = self.counts(with_database(self.backup_url, scratch))
        self.record(
            "sauvegarde avec --enable-row-security (le piège silencieux)",
            sauvegarde_reussie=True,
            restauration_reussie=True,
            lignes_restaurees=recovered,
            organisation_visible=recovered["organizations"] > 0,
            documents_perdus=recovered["documents"] == 0,
            evenements_audit_perdus=recovered["audit_events"] == 0,
            commentaire="une sauvegarde qui réussit et une restauration qui réussit, sans les données",
        )
        self.psql(with_database(self.admin_url, "postgres"), "-c",
                  f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')

    def step_backup(self) -> pathlib.Path:
        started = time.time()
        dump = self.workdir / "vericlaim.dump"
        outcome = self.pg_dump(self.backup_url, dump)
        if outcome.returncode != 0:
            raise RuntimeError(f"pg_dump a échoué avec le rôle de sauvegarde : {outcome.stderr.strip()[:400]}")
        self.record(
            "sauvegarde avec le rôle dédié",
            fichier=dump.name,
            octets=dump.stat().st_size,
            duree_s=round(time.time() - started, 2),
        )
        return dump

    def step_restore(self, dump: pathlib.Path, *, label: str = "base cible éphémère et restauration") -> pathlib.Path:
        started = time.time()
        self.recreate_database(self.target_database)
        target = with_database(self.backup_url, self.target_database)
        # Restauration par le rôle propriétaire de la base cible : depuis PostgreSQL 15, écrire
        # dans le schéma `public` d'une base qu'on ne possède pas est refusé. Le rôle de
        # sauvegarde ne sert qu'à lire la source — deux rôles, deux métiers, et un refus par
        # étape quand on les confond.
        outcome = self.pg_restore(with_database(self.admin_url, self.target_database), dump)
        if outcome.returncode != 0:
            raise RuntimeError(f"pg_restore a échoué : {outcome.stderr.strip()[:600]}")
        self.grant_read_to_backup(self.target_database, restored=True)
        self.record(label, duree_s=round(time.time() - started, 2), erreurs=0)
        return target

    def step_restore_grants(self, target: str) -> None:
        """Un dump `--no-owner --no-privileges` ne transporte ni propriétaires ni privilèges."""
        migrator_target = with_database(self.source_url, self.target_database)
        outcome = subprocess.run(
            [sys.executable, str(INFRA_NEON / "run_post_migration_grants.py")],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/root"),
                "DATABASE_URL_MIGRATOR": migrator_target,
            },
        )
        passed = outcome.returncode == 0
        self.record(
            "grants runtime rejoués sur la base restaurée",
            reussi=passed,
            detail=(outcome.stdout.strip() or outcome.stderr.strip())[-300:],
        )
        if not passed:
            self.fail("les grants post-restauration ont échoué : la base restaurée n'est pas exploitable")

    def step_verify_runtime(self) -> None:
        if not self.app_password:
            self.record("8. vérification runtime", ignore=True, raison="aucun mot de passe applicatif fourni")
            return
        app_url = with_database(
            with_app_role(self.source_url, self.app_password), self.target_database
        )
        outcome = subprocess.run(
            [sys.executable, str(INFRA_NEON / "verify_runtime.py"), "--expected-revision", self.expected_head],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/root"),
                "DATABASE_URL_APP": app_url,
            },
        )
        passed = outcome.returncode == 0
        self.record(
            "vérification runtime sur la base restaurée (RLS, moindre privilège, tête)",
            reussi=passed,
            detail=(outcome.stdout.strip() or outcome.stderr.strip())[-400:],
        )
        if not passed:
            self.fail("la base restaurée ne satisfait pas la vérification runtime")

    def step_smoke_test(self, organization_id: str, target: str) -> None:
        """Le test de fumée du produit : la chaîne d'audit restaurée doit encore vérifier."""
        script = """
import json, sys
from uuid import UUID
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from app.audit.service import verify_audit_chain
from app.identity.service import set_db_request_context
from app.models.domain import Document, Organization

url, organization_id, user_id = sys.argv[1], UUID(sys.argv[2]), UUID(sys.argv[3])
engine = create_engine(url)
with Session(engine) as db:
    set_db_request_context(db, user_id=user_id, organization_id=organization_id)
    organization = db.scalar(select(Organization).where(Organization.id == organization_id))
    documents = db.scalar(
        select(func.count()).select_from(Document).where(Document.organization_id == organization_id)
    )
    outcome = verify_audit_chain(db, organization_id)
    print(json.dumps({
        "organization_readable": organization is not None,
        "organization_name": organization.name if organization else None,
        "documents": int(documents or 0),
        "audit_chain_valid_after_restore": bool(outcome.is_valid),
        "audit_events": int(outcome.total_events),
        "chain_head": outcome.head_event_hash,
    }))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, target, organization_id, "00000000-0000-4000-8000-000000000001"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/root"),
                "APP_ENV": "development",
                "AUTO_CREATE_SCHEMA": "false",
                "DATABASE_URL": target,
            },
        )
        if result.returncode != 0:
            self.fail(f"test de fumée en échec : {result.stderr.strip()[-400:]}")
            return
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.record("test de fumée du produit sur la base restaurée", **payload)
        if not payload["organization_readable"] or payload["documents"] != 1:
            self.fail("les données métier ne sont pas lisibles après restauration")
        if not payload["audit_chain_valid_after_restore"] or payload["audit_events"] != 3:
            self.fail("la chaîne d'audit restaurée ne vérifie pas (ou ne porte pas les 3 événements)")

    def step_compare_volumes(self, target: str) -> None:
        restored_head = self.scalar(target, "select version_num from alembic_version")
        source_counts = self.counts(self.backup_url)
        target_counts = self.counts(target)
        comparison = {
            table: {
                "source": source_counts[table],
                "cible": target_counts[table],
                "identiques": source_counts[table] == target_counts[table],
            }
            for table in BUSINESS_TABLES
        }
        self.record(
            "comparaison des volumes et de la tête",
            tete_source=self.expected_head,
            tete_cible=restored_head,
            tables=comparison,
        )
        if restored_head != self.expected_head:
            self.fail(f"tête Alembic différente après restauration : {restored_head} ≠ {self.expected_head}")
        divergent = [name for name, values in comparison.items() if not values["identiques"]]
        if divergent:
            self.fail(f"volumes divergents après restauration : {', '.join(divergent)}")

    def step_rollback_drill(self, dump: pathlib.Path) -> None:
        """Retour arrière répété à blanc : détruire, puis reconstruire depuis la sauvegarde."""
        started = time.time()
        # Le retour arrière, c'est ceci : détruire la base et la reconstruire depuis la
        # sauvegarde. Pas un `alembic downgrade` — le runbook l'interdit en production.
        self.recreate_database(self.target_database)
        outcome = self.pg_restore(with_database(self.admin_url, self.target_database), dump)
        head = self.scalar(with_database(self.admin_url, self.target_database),
                           "select version_num from alembic_version")
        self.record(
            "retour arrière répété à blanc (base détruite puis reconstruite)",
            restauration_reussie=outcome.returncode == 0,
            tete_apres_retour=head,
            duree_s=round(time.time() - started, 2),
        )
        if outcome.returncode != 0 or head != self.expected_head:
            self.fail("le retour arrière par restauration n'a pas redonné la tête attendue")


def find_pg_bin(explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit)
    for name in ("pg_dump", "psql"):
        found = shutil.which(name)
        if found:
            return pathlib.Path(found).parent
    candidates = sorted(pathlib.Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)
    if candidates:
        return candidates[0]
    raise SystemExit("binaires PostgreSQL introuvables : passer --pg-bin")


def main() -> int:
    parser = argparse.ArgumentParser(description="Répétition de restauration (C24).")
    parser.add_argument("--source-url", required=True, help="DSN du rôle de migration")
    parser.add_argument("--backup-url", required=True, help="DSN du rôle de sauvegarde (BYPASSRLS)")
    parser.add_argument(
        "--admin-url",
        default=None,
        help="DSN du rôle autorisé à créer les bases éphémères (défaut : --source-url)",
    )
    parser.add_argument("--target-db", default="vericlaim_restore_rehearsal")
    parser.add_argument("--app-password-env", default="VERICLAIM_APP_PASSWORD")
    parser.add_argument("--pg-bin", default=None)
    parser.add_argument("--workdir", default="/tmp/vericlaim-restore-rehearsal")
    parser.add_argument("--json", default=None)
    arguments = parser.parse_args()

    workdir = pathlib.Path(arguments.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    rehearsal = Rehearsal(
        source_url=arguments.source_url,
        backup_url=arguments.backup_url,
        admin_url=arguments.admin_url or arguments.source_url,
        target_database=arguments.target_db,
        app_password=os.environ.get(arguments.app_password_env),
        pg_bin=find_pg_bin(arguments.pg_bin),
        workdir=workdir,
    )

    source_database = urlsplit(arguments.source_url).path.lstrip("/")

    try:
        rehearsal.step_migrate_source()
        # Les droits de lecture du rôle de sauvegarde sont accordés ici pour que la répétition
        # soit reproductible de zéro ; en exploitation ils sont posés une fois par l'exploitant
        # (voir docs/operations/restore-runbook.md) et le contrôle qui suit les vérifie.
        rehearsal.grant_read_to_backup(source_database)
        rehearsal.step_check_backup_role()
        organization_id = rehearsal.step_seed()
        rehearsal.step_measure_backup_traps()
        dump = rehearsal.step_backup()
        target = rehearsal.step_restore(dump, label="base cible éphémère et restauration")
        rehearsal.step_restore_grants(target)
        rehearsal.step_verify_runtime()
        rehearsal.step_smoke_test(organization_id, target)
        rehearsal.step_compare_volumes(target)
        rehearsal.step_rollback_drill(dump)
    except Exception as error:  # noqa: BLE001 — une répétition qui casse doit le dire
        rehearsal.fail(f"{type(error).__name__} : {error}")

    report = {
        "chantier": "C24 — répétition de restauration",
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_database": urlsplit(rehearsal.source_url).path.lstrip("/"),
        "target_database": rehearsal.target_database,
        "pg_bin": str(rehearsal.pg_bin),
        "steps": rehearsal.steps,
        "failures": rehearsal.failures,
        "verdict": "répétition réussie" if not rehearsal.failures else "répétition en échec",
    }
    if arguments.json:
        pathlib.Path(arguments.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"transcript écrit dans {arguments.json}")

    if rehearsal.failures:
        print("\nRépétition de restauration en échec :", file=sys.stderr)
        for failure in rehearsal.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nRépétition de restauration réussie.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
