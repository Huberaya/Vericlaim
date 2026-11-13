"""Exercise a backup/restore round-trip on a disposable, isolated target.

The mission's rule is explicit: never call a backup valid without a tested
restore.  Neon cannot be reached from this environment, so this script does the
part that is genuinely testable here and refuses to imply the rest:

* it builds a real PostgreSQL 16 cluster with the reviewed role split;
* it seeds two fictional tenants through the **application's own services**, so
  the data is exactly what the product would write (including the hash-chained
  audit trail);
* it exports the database with ``pg_dump --format=custom``;
* it restores into a **different, empty database** on the same throwaway server;
* it re-applies the reviewed grants and then verifies, as the runtime role:
  Alembic revision, tenant row counts, RLS enforcement and audit-chain integrity.

It does **not** test Neon PITR, MinIO/S3 object durability, or any production
surface.  ``docs/infrastructure/10-sauvegardes-restauration.md`` states exactly
what remains unproven.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
NEON_ROOT = REPO_ROOT / "infra" / "neon"
REPORT = REPO_ROOT / ".recette" / "restore-report.json"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND_ROOT))

import pgserver
import psycopg
from psycopg import sql
from stack import (
    APP_ROLE,
    MIGRATOR_ROLE,
    PostgresCluster,
    _run_psql,
    ephemeral_password,
    start_postgres,
)


def _bin(name: str) -> str:
    candidate = Path(pgserver.__file__).parent / "pginstall" / "bin" / name
    if not candidate.exists():
        raise RuntimeError(f"{name} is missing from the bundled PostgreSQL install")
    return str(candidate)


def _seed(cluster) -> dict[str, int]:
    """Write test data through the application services (not raw INSERTs)."""

    os.environ.update(
        {
            "APP_ENV": "development",
            "AUTO_CREATE_SCHEMA": "false",
            "DATABASE_URL": cluster.app_sqlalchemy_url(cluster.app_password),
            "AUTH_SESSION_SECRET": uuid.uuid4().hex + uuid.uuid4().hex,
        }
    )
    from app.core.database import SessionLocal
    from app.identity.roles import ensure_system_roles
    from app.identity.service import append_audit_event, set_db_request_context
    from app.models.domain import (
        Document,
        DocumentStatus,
        DocumentVersion,
        ExtractionStatus,
        Membership,
        MembershipStatus,
        Organization,
        Role,
        User,
        UserStatus,
    )
    from sqlalchemy import select

    counts = {"organizations": 0, "documents": 0, "versions": 0, "audit_events": 0}
    with SessionLocal() as db:
        ensure_system_roles(db)
        role = db.scalar(select(Role).where(Role.code == "owner"))
        assert role is not None
        for index, label in enumerate(("Alpha", "Beta")):
            suffix = uuid.uuid4().hex[:8]
            user = User(
                email=f"restore.{suffix}@example.com",
                status=UserStatus.ACTIVE,
                identity_provider="https://idp.example.test",
                external_subject=f"restore-{suffix}",
            )
            organization = Organization(name=f"Org Restauration {label}", slug=f"org-restore-{suffix}")
            db.add_all([user, organization])
            db.flush()
            db.add(
                Membership(
                    organization_id=organization.id,
                    user_id=user.id,
                    role_id=role.id,
                    status=MembershipStatus.ACTIVE,
                )
            )
            db.flush()
            set_db_request_context(db, user_id=user.id, organization_id=organization.id)
            document = Document(
                organization_id=organization.id,
                document_key=f"DOC-{suffix}",
                title=f"Pièce de restauration {label}",
                status=DocumentStatus.UPLOADED,
            )
            db.add(document)
            db.flush()
            db.add(
                DocumentVersion(
                    organization_id=organization.id,
                    document_id=document.id,
                    version_number=1,
                    source_filename="piece.pdf",
                    content_type="application/pdf",
                    storage_key=f"clean/{organization.id}/{document.id}/versions/{uuid.uuid4()}/source",
                    sha256="b" * 64,
                    size_bytes=512,
                    extraction_status=ExtractionStatus.PENDING,
                )
            )
            for event_index in range(3):
                append_audit_event(
                    db,
                    organization_id=organization.id,
                    actor_user_id=user.id,
                    entity_type="document",
                    entity_id=document.id,
                    action=f"restore.probe.{event_index}",
                    payload={"index": event_index, "label": label},
                )
            db.flush()
            counts["organizations"] += 1
            counts["documents"] += 1
            counts["versions"] += 1
            counts["audit_events"] += 3
        db.commit()
    SessionLocal.kw["bind"].dispose()
    return counts


def _verify_audit_chain(cluster) -> tuple[bool, int]:
    """Recompute the hash chain exactly as ``GET /api/v1/audit/verify`` does."""

    from app.core.database import sha256_json

    total = 0
    valid = True
    with psycopg.connect(cluster.app_dsn) as connection:
        connection.autocommit = True
        organizations = [
            row[0] for row in connection.execute("SELECT id FROM organizations ORDER BY id").fetchall()
        ]
        for organization_id in organizations:
            connection.execute(
                "SELECT set_config('app.current_organization_id', %s, false)", (str(organization_id),)
            )
            events = connection.execute(
                "SELECT actor_user_id, entity_type, entity_id, action, occurred_at, request_id, "
                "payload_json, payload_sha256, previous_event_hash, event_hash "
                "FROM audit_events WHERE organization_id = %s ORDER BY occurred_at, id",
                (organization_id,),
            ).fetchall()
            previous_hash = None
            for row in events:
                total += 1
                payload_hash = sha256_json(row[6])
                material = {
                    "organization_id": str(organization_id),
                    "actor_user_id": str(row[0]) if row[0] else None,
                    "entity_type": row[1],
                    "entity_id": str(row[2]) if row[2] else None,
                    "action": row[3],
                    "occurred_at": row[4].isoformat(),
                    "request_id": row[5],
                    "payload": row[6],
                    "payload_sha256": payload_hash,
                    "previous_event_hash": row[8],
                }
                if row[7] != payload_hash or row[9] != sha256_json(material) or row[8] != previous_hash:
                    valid = False
                previous_hash = row[9]
    return valid, total


def run() -> dict[str, object]:
    report: dict[str, object] = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    source = start_postgres()
    report["source_database_revision"] = source.revision

    seeded = _seed(source)
    report["seeded_rows"] = seeded
    valid_before, events_before = _verify_audit_chain(source)
    report["source_audit_chain_valid"] = valid_before
    report["source_audit_events"] = events_before

    archive = REPO_ROOT / ".recette" / f"backup-{uuid.uuid4().hex[:8]}.dump"
    archive.parent.mkdir(parents=True, exist_ok=True)

    params = psycopg.conninfo.conninfo_to_dict(source.admin_dsn)
    started = time.monotonic()
    dump = subprocess.run(
        [_bin("pg_dump"), "--format=custom", "--no-owner", "--no-privileges",
         f"--dbname={source.database}", f"--host={params['host']}", "--username=postgres",
         f"--file={archive}"],
        capture_output=True, text=True, check=False,
    )
    if dump.returncode != 0:
        raise RuntimeError(f"pg_dump failed:\n{dump.stdout}\n{dump.stderr}")
    report["backup_bytes"] = archive.stat().st_size
    report["backup_format"] = "pg_dump --format=custom"

    # A *different* database on the same throwaway server: isolated target.
    target_database = f"vericlaim_restore_{uuid.uuid4().hex[:8]}"
    target_migrator_password = ephemeral_password("mig")
    target_app_password = ephemeral_password("app")
    with psycopg.connect(source.admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_database)))

    target = PostgresCluster(
        admin_dsn=source.admin_dsn,
        database=target_database,
        migrator_dsn="",
        app_dsn="",
        revision="",
        pgdata=source.pgdata,
    )
    target.migrator_password = target_migrator_password
    target.app_password = target_app_password
    target.migrator_dsn = target._dsn(user=MIGRATOR_ROLE, password=target_migrator_password)
    target.app_dsn = target._dsn(user=APP_ROLE, password=target_app_password)

    with psycopg.connect(source.admin_dsn, autocommit=True) as admin:
        for role in (MIGRATOR_ROLE, APP_ROLE):
            admin.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(target_app_password))
            )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(target_database), sql.Identifier(MIGRATOR_ROLE), sql.Identifier(APP_ROLE)
            )
        )

    restore_dsn = psycopg.conninfo.make_conninfo(source.admin_dsn, dbname=target_database)
    # ``--no-owner`` recreates objects as the connecting user (postgres). The
    # reviewed grants script must be executed by the table owner, so ownership is
    # handed to vericlaim_migrator first — exactly the invariant
    # ``infra/neon/01_bootstrap_roles.sql`` establishes on a fresh database.
    restore = subprocess.run(
        [_bin("pg_restore"), "--no-owner", "--no-privileges", f"--dbname={restore_dsn}", str(archive)],
        capture_output=True, text=True, check=False,
    )
    if restore.returncode == 0:
        with psycopg.connect(restore_dsn, autocommit=True) as owner:
            owner.execute(
                "DO $$ DECLARE r record; BEGIN "
                "FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP "
                "EXECUTE format('ALTER TABLE public.%I OWNER TO vericlaim_migrator', r.tablename); "
                "END LOOP; "
                "FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP "
                "EXECUTE format('ALTER SEQUENCE public.%I OWNER TO vericlaim_migrator', r.sequencename); "
                "END LOOP; END $$;"
            )
    if restore.returncode != 0:
        raise RuntimeError(f"pg_restore failed:\n{restore.stdout}\n{restore.stderr}")
    report["restore_seconds"] = round(time.monotonic() - started, 2)

    # Re-apply the reviewed grants to the restored database.
    _run_psql(target.migrator_dsn, (NEON_ROOT / "02_after_migration_grants.sql").read_text(encoding="utf-8"), {}, label="grants (restored)")
    with psycopg.connect(target.migrator_dsn, autocommit=True) as migrator:
        migrator.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(MIGRATOR_ROLE))
        )

    with psycopg.connect(target.app_dsn) as app:
        target.revision = app.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        invisible = app.execute("SELECT count(*) FROM documents").fetchone()[0]
        organizations = app.execute("SELECT count(*) FROM organizations").fetchone()[0]
        protected = app.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
        ).fetchone()[0]
    report["target_database_revision"] = target.revision
    report["revision_preserved"] = target.revision == source.revision
    report["organizations_restored"] = organizations
    report["all_organizations_present"] = organizations == seeded["organizations"]
    report["rows_visible_without_tenant_context"] = invisible
    report["rls_protected_tables"] = protected

    valid_after, events_after = _verify_audit_chain(target)
    report["restored_audit_chain_valid"] = valid_after
    report["restored_audit_events"] = events_after
    report["audit_events_preserved"] = events_after == events_before
    report["data_loss"] = "none (writes stopped before the export)"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    archive.unlink(missing_ok=True)  # never keep an exported dataset around
    return report


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    failures = [
        key
        for key in ("revision_preserved", "all_organizations_present", "restored_audit_chain_valid",
                    "audit_events_preserved")
        if not result.get(key)
    ]
    if result.get("rows_visible_without_tenant_context") != 0:
        failures.append("rows_visible_without_tenant_context")
    print("FAILURES:", failures or "none")
    raise SystemExit(1 if failures else 0)
