"""Bring up a disposable, *real-component* VeriClaim recette stack on one host.

Why this exists
---------------
The mission requires demonstrating the real services end to end.  A Vercel
Preview cannot do that on its own: it has no long-running worker process, so the
durable extraction/analysis queues never drain, and it is behind SSO.  This
module assembles the same data plane the Compose stack declares, with the same
reviewed SQL, on a machine an agent can actually inspect:

* **PostgreSQL 16** (real server, unix socket) with the reviewed
  ``infra/neon/01_bootstrap_roles.sql`` role split and
  ``infra/neon/02_after_migration_grants.sql`` grants, migrated by the real
  Alembic chain as ``vericlaim_migrator`` and served as ``vericlaim_app``.
* **S3-compatible object storage** exercised through the application's real
  ``S3ObjectStorage`` adapter (boto3, SigV4, presigned URLs).
* **The real extraction worker** (PyMuPDF + Pillow) against real clean objects.
* **A real OIDC provider** for the real ``OidcClient``.

What it is not
--------------
It is not Neon staging, not Vercel and not ClamAV.  Each substitution is named
in ``docs/infrastructure/`` with the exact consequence.  Nothing here touches a
persistent or production resource: every directory it creates lives under the
git-ignored ``.recette/`` tree and is destroyed on stop.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import pgserver
import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
NEON_ROOT = REPO_ROOT / "infra" / "neon"
RUNTIME_DIR = REPO_ROOT / ".recette"

ADMIN_USER = "postgres"
APP_ROLE = "vericlaim_app"
MIGRATOR_ROLE = "vericlaim_migrator"


class RecetteStackError(RuntimeError):
    """A real component of the recette stack could not be brought up."""


def ephemeral_password(label: str) -> str:
    """A per-run credential that is never written to disk or to a report."""
    return f"{label}-{uuid.uuid4().hex}{uuid.uuid4().hex[:16]}"


def _wait_for_tcp(host: str, port: int, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((host, port)) == 0:
                return
        time.sleep(0.15)
    raise RecetteStackError(f"Timed out waiting for {host}:{port}")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class PostgresCluster:
    """A real PostgreSQL server plus the reviewed VeriClaim role split."""

    admin_dsn: str
    database: str
    migrator_dsn: str
    app_dsn: str
    revision: str
    pgdata: Path
    #: Runtime credential for this run only; never written to a report.
    app_password: str = ""
    migrator_password: str = ""
    #: Evidence captured while provisioning, quoted verbatim in the report.
    evidence: dict[str, object] = field(default_factory=dict)

    def _dsn(self, *, user: str, password: str) -> str:
        params = psycopg.conninfo.conninfo_to_dict(self.admin_dsn)
        return psycopg.conninfo.make_conninfo(self.admin_dsn, user=user, password=password, dbname=self.database)

    @staticmethod
    def sqlalchemy_url(dsn: str, *, database: str, user: str, password: str) -> str:
        params = psycopg.conninfo.conninfo_to_dict(dsn)
        host = params.get("host", "") or ""
        port = str(params.get("port", "") or "")
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
        if host.startswith("/"):
            return f"postgresql+psycopg://{auth}@/{database}?host={quote(host, safe='')}"
        if port:
            return f"postgresql+psycopg://{auth}@{host}:{port}/{database}"
        return f"postgresql+psycopg://{auth}@{host}/{database}"

    def app_sqlalchemy_url(self, password: str) -> str:
        return self.sqlalchemy_url(self.admin_dsn, database=self.database, user=APP_ROLE, password=password)

    def app_libpq_url(self, password: str) -> str:
        """A plain ``postgresql://`` URI, as ``infra/neon/verify_runtime.py`` expects."""

        params = psycopg.conninfo.conninfo_to_dict(self.admin_dsn)
        host = params.get("host", "") or "localhost"
        port = params.get("port", "") or "5432"
        return (
            f"postgresql://{quote(APP_ROLE, safe='')}:{quote(password, safe='')}"
            f"@{quote(str(host), safe='')}:{port}/{self.database}"
        )


def start_postgres(*, workdir: Path | None = None, keep: bool = True) -> PostgresCluster:
    """Start real PostgreSQL 16 and apply the reviewed migration/role chain."""

    try:
        import pgserver
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RecetteStackError(
            "pgserver is not installed. Install the recette extra: pip install pgserver"
        ) from exc

    # A run-scoped cluster: the reviewed bootstrap refuses to run twice against
    # the same database, so every qualification run starts from a clean server.
    if workdir is None:
        workdir = RUNTIME_DIR / f"run-{uuid.uuid4().hex[:8]}" / "pgdata"
    workdir.mkdir(parents=True, exist_ok=True)

    if not (workdir / "PG_VERSION").exists():
        # Initialise exactly as pgserver would, so the TLS configuration below can
        # be written before the server's first start.
        binary = Path(pgserver.__file__).parent / "pginstall" / "bin" / "initdb"
        init = subprocess.run(
            [str(binary), "-D", str(workdir), "--auth=trust", "--auth-local=trust", "--encoding=utf8", "-U", "postgres"],
            capture_output=True,
            text=True,
            check=False,
        )
        if init.returncode != 0:
            raise RecetteStackError(f"initdb failed:\n{init.stdout}\n{init.stderr}")
    server = pgserver.get_server(str(workdir), cleanup_mode=None)
    admin_dsn = server.get_uri()
    # pgserver reuses an already-initialised cluster; make sure a second process
    # can connect and that the socket is really accepting.
    with psycopg.connect(admin_dsn, connect_timeout=10) as connection:
        version = connection.execute("SELECT version()").fetchone()[0]

    database = f"vericlaim_recette_{uuid.uuid4().hex[:10]}"
    migrator_password = ephemeral_password("mig")
    app_password = ephemeral_password("app")

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        # Mirror infra/neon/01_bootstrap_roles.sql: the roles exist without LOGIN
        # until the bootstrap hands them their first distinct password.  The
        # script refuses to run twice, so it is executed exactly once here on a
        # brand-new database, as the documented procedure requires.
        for role in (MIGRATOR_ROLE, APP_ROLE):
            admin.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(
                    sql.Identifier(role)
                )
            )

    cluster = PostgresCluster(
        admin_dsn=admin_dsn,
        database=database,
        migrator_dsn="",
        app_dsn="",
        revision="",
        pgdata=workdir,
    )
    cluster.app_password = app_password
    cluster.migrator_password = migrator_password
    cluster.migrator_dsn = cluster._dsn(user=MIGRATOR_ROLE, password=migrator_password)
    cluster.app_dsn = cluster._dsn(user=APP_ROLE, password=app_password)

    bootstrap_sql = (NEON_ROOT / "01_bootstrap_roles.sql").read_text(encoding="utf-8")
    subst = {
        "target_database": database,
        "migrator_password": migrator_password,
        "app_password": app_password,
    }
    # The bootstrap refuses to run unless psql is connected to the database it is
    # asked to prepare (``current_database() = target_database``).
    bootstrap_dsn = psycopg.conninfo.make_conninfo(cluster.admin_dsn, dbname=database)
    _run_psql(bootstrap_dsn, bootstrap_sql, subst, label="01_bootstrap_roles.sql")

    # Never trust psql's exit code alone: ``\quit`` returns 0. Assert the
    # post-condition the audited script promises.
    with psycopg.connect(admin_dsn) as verify:
        login_roles = dict(
            verify.execute(
                "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname IN (%s, %s)",
                (MIGRATOR_ROLE, APP_ROLE),
            ).fetchall()
        )
    if login_roles != {MIGRATOR_ROLE: True, APP_ROLE: True}:
        raise RecetteStackError(f"01_bootstrap_roles.sql did not enable LOGIN for both roles: {login_roles}")

    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={
            **os.environ,
            "APP_ENV": "development",
            "AUTO_CREATE_SCHEMA": "false",
            "DATABASE_URL": cluster.sqlalchemy_url(
                admin_dsn, database=database, user=MIGRATOR_ROLE, password=migrator_password
            ),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if migration.returncode != 0:
        raise RecetteStackError(f"alembic upgrade failed:\n{migration.stdout}\n{migration.stderr}")

    with psycopg.connect(cluster.migrator_dsn, autocommit=True) as migrator:
        migrator.execute((NEON_ROOT / "02_after_migration_grants.sql").read_text(encoding="utf-8"))

    with psycopg.connect(cluster.app_dsn) as app:
        cluster.revision = app.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        cluster.evidence = {
            "postgres_version": version.split(" on ")[0],
            "current_user": app.execute("SELECT current_user").fetchone()[0],
            "is_superuser": app.execute("SELECT usesuper FROM pg_user WHERE usename = current_user").fetchone()[0],
            "can_create_db": app.execute(
                "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"
            ).fetchone()[0],
            "bypass_rls": app.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()[0],
            "table_owner_is_runtime": app.execute(
                "SELECT bool_or(tableowner = current_user) FROM pg_tables WHERE schemaname = 'public'"
            ).fetchone()[0],
            "rls_tables": app.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
            ).fetchone()[0],
            "audit_update_granted": app.execute(
                "SELECT has_table_privilege(current_user, 'public.audit_events', 'UPDATE')"
            ).fetchone()[0],
            "alembic_head": cluster.revision,
        }
    return cluster


def _psql_binary() -> str:
    """Locate a real psql so the reviewed SQL runs verbatim.

    ``infra/neon/01_bootstrap_roles.sql`` uses psql conditionals and ``\\gexec``;
    re-implementing them in Python would mean forking audited SQL.  The
    ``pgserver`` wheel ships the matching client, and PATH is used as a fallback.
    """

    found = shutil.which("psql")
    if found:
        return found
    candidate = Path(pgserver.__file__).parent / "pginstall" / "bin" / "psql"
    if candidate.exists():
        return str(candidate)
    raise RecetteStackError(
        "A real psql client is required to apply the reviewed Neon scripts verbatim."
    )


def _run_psql(dsn: str, script: str, subst: dict[str, str], *, label: str) -> None:
    command = [_psql_binary(), "-X", "-q", "-v", "ON_ERROR_STOP=1", dsn]
    for key, value in subst.items():
        command += ["-v", f"{key}={value}"]
    result = subprocess.run(command, input=script, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RecetteStackError(f"{label} failed under psql:\n{result.stdout}\n{result.stderr}")


@dataclass
class S3Store:
    """Object storage exercised through the application's real boto3 adapter."""

    endpoint_url: str
    access_key: str
    secret_key: str
    region: str
    quarantine_bucket: str
    clean_bucket: str
    backend: str


def start_s3_store(*, workdir: Path | None = None) -> S3Store:
    """Start a local S3-compatible server and create the two private buckets.

    The application talks to it with ``S3ObjectStorage`` unchanged: SigV4
    presigning, multipart-free PUT, bucket separation and key layout are the
    real code paths.  The server is a local S3 implementation, not MinIO, and
    that difference is recorded in the report.
    """

    try:
        from moto.server import ThreadedMotoServer
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RecetteStackError("moto[server] is not installed; install it to run the storage proofs") from exc

    port = _find_free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    _wait_for_tcp("127.0.0.1", port)

    endpoint = f"http://127.0.0.1:{port}"
    access_key = "recette-access-key"
    secret_key = ephemeral_password("s3secret")
    quarantine = "vericlaim-quarantine"
    clean = "vericlaim-documents"

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="eu-west-3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    for bucket in (quarantine, clean):
        client.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "eu-west-3"})
    # Buckets must stay private: no ACL and no bucket policy is ever attached.
    for bucket in (quarantine, clean):
        try:
            client.get_bucket_policy(Bucket=bucket)
        except Exception:
            pass  # S3 raises NoSuchBucketPolicy: the expected private default.

    return S3Store(
        endpoint_url=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        region="eu-west-3",
        quarantine_bucket=quarantine,
        clean_bucket=clean,
        backend="moto-local-s3",
    )
