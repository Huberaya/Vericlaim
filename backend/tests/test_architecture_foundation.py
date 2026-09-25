from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base
from app.models import domain


BACKEND_ROOT = Path(__file__).resolve().parents[1]


EXPECTED_TABLES = {
    "organizations",
    "users",
    "roles",
    "memberships",
    "suppliers",
    "products",
    "documents",
    "document_uploads",
    "document_extraction_jobs",
    "analysis_detection_jobs",
    "document_versions",
    "document_segments",
    "certificates",
    "evidence",
    "analyses",
    "analysis_documents",
    "analysis_versions",
    "claims",
    "evidence_links",
    "regulations",
    "rules",
    "rule_versions",
    "risks",
    "recommendations",
    "validations",
    "reports",
    "evidence_requests",
    "audit_events",
    "auth_sessions",
    "audit_records",
}

TENANT_OWNED_TABLES = {
    "suppliers",
    "products",
    "documents",
    "document_uploads",
    "document_extraction_jobs",
    "analysis_detection_jobs",
    "document_versions",
    "document_segments",
    "certificates",
    "evidence",
    "analyses",
    "analysis_documents",
    "analysis_versions",
    "claims",
    "evidence_links",
    "risks",
    "recommendations",
    "validations",
    "reports",
    "evidence_requests",
    "audit_events",
}


def _hash(character: str = "a") -> str:
    return character * 64


def test_schema_autocreation_is_opt_in_outside_local_and_test(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://vericlaim:secret@db/vericlaim")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "a" * 48)
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.test")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("OIDC_CLIENT_ID", "vericlaim-test-client")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example.test/api/v1/auth/callback")
    # Secure persisted-document ingestion is fail-closed in shared
    # environments: production-like settings must identify a ClamAV service.
    monkeypatch.setenv("DOCUMENT_CLAMAV_HOST", "clamav.internal.example")
    monkeypatch.setenv("DOCUMENT_STORAGE_ENDPOINT", "https://minio.internal.example")
    monkeypatch.setenv("DOCUMENT_STORAGE_PUBLIC_ENDPOINT", "https://objects.example")
    monkeypatch.setenv("DOCUMENT_QUARANTINE_BUCKET", "vericlaim-quarantine-test")
    monkeypatch.setenv("DOCUMENT_CLEAN_BUCKET", "vericlaim-clean-test")
    monkeypatch.delenv("AUTO_CREATE_SCHEMA", raising=False)
    assert get_settings().auto_create_schema is False

    monkeypatch.delenv("DOCUMENT_STORAGE_ENDPOINT")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "DOCUMENT_STORAGE_ENDPOINT" in str(exc)
    else:
        raise AssertionError("production must explicitly select a MinIO endpoint")
    monkeypatch.setenv("DOCUMENT_STORAGE_ENDPOINT", "https://minio.internal.example")

    monkeypatch.delenv("DOCUMENT_STORAGE_PUBLIC_ENDPOINT")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "DOCUMENT_STORAGE_PUBLIC_ENDPOINT" in str(exc)
    else:
        raise AssertionError("production must explicitly select a browser-reachable MinIO endpoint")
    monkeypatch.setenv("DOCUMENT_STORAGE_PUBLIC_ENDPOINT", "https://objects.example")

    monkeypatch.delenv("DOCUMENT_QUARANTINE_BUCKET")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "DOCUMENT_QUARANTINE_BUCKET" in str(exc)
    else:
        raise AssertionError("production must not rely on a default quarantine bucket")
    monkeypatch.setenv("DOCUMENT_QUARANTINE_BUCKET", "vericlaim-quarantine-test")

    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "AUTO_CREATE_SCHEMA may only" in str(exc)
    else:
        raise AssertionError("production must not allow create_all")
    monkeypatch.delenv("AUTO_CREATE_SCHEMA", raising=False)

    monkeypatch.setenv("DATABASE_URL", "sqlite:///unsafe-production.db")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "must use PostgreSQL" in str(exc)
    else:
        raise AssertionError("production must not permit SQLite without RLS")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://vericlaim:secret@db/vericlaim")

    monkeypatch.delenv("DATABASE_URL")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "DATABASE_URL must be configured" in str(exc)
    else:
        raise AssertionError("production must not fall back to SQLite")

    monkeypatch.setenv("APP_ENV", "development")
    assert get_settings().auto_create_schema is True

    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "false")
    assert get_settings().auto_create_schema is False

    monkeypatch.setenv("APP_ENV", "prod")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "APP_ENV must be one of" in str(exc)
    else:
        raise AssertionError("unknown deployment environments must fail closed")


def test_document_capabilities_have_security_bounds(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DOCUMENT_UPLOAD_TTL_SECONDS", "3601")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "DOCUMENT_UPLOAD_TTL_SECONDS" in str(exc)
    else:
        raise AssertionError("upload capability TTL must remain bounded")

    monkeypatch.setenv("DOCUMENT_UPLOAD_TTL_SECONDS", "900")
    monkeypatch.setenv("DOCUMENT_DOWNLOAD_TTL_SECONDS", "901")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "DOCUMENT_DOWNLOAD_TTL_SECONDS" in str(exc)
    else:
        raise AssertionError("download capability TTL must remain bounded")

    monkeypatch.setenv("DOCUMENT_DOWNLOAD_TTL_SECONDS", "300")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024 + 1))
    try:
        get_settings()
    except RuntimeError as exc:
        assert "MAX_UPLOAD_BYTES" in str(exc)
    else:
        raise AssertionError("document memory boundary must remain bounded")

    monkeypatch.setenv("MAX_UPLOAD_BYTES", "15728640")
    monkeypatch.setenv("ANALYSIS_DETECTION_MAX_ATTEMPTS", "11")
    try:
        get_settings()
    except RuntimeError as exc:
        assert "ANALYSIS_DETECTION_MAX_ATTEMPTS" in str(exc)
    else:
        raise AssertionError("analysis-detection retries must remain bounded")

    monkeypatch.setenv("ANALYSIS_DETECTION_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ANALYSIS_DETECTION_LEASE_SECONDS", str(15 * 60 + 1))
    try:
        get_settings()
    except RuntimeError as exc:
        assert "ANALYSIS_DETECTION_LEASE_SECONDS" in str(exc)
    else:
        raise AssertionError("analysis-detection leases must remain bounded")


def test_shared_database_startup_fails_closed_until_alembic_is_current(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'shared.db'}"
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "APP_ENV": "test",
        "AUTO_CREATE_SCHEMA": "false",
    }
    startup_code = "from app.core.database import create_tables; create_tables()"

    before_migration = subprocess.run(
        [sys.executable, "-c", startup_code],
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert before_migration.returncode != 0
    assert "Database schema is not at the expected Alembic revision" in before_migration.stderr

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    after_migration = subprocess.run(
        [sys.executable, "-c", startup_code],
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert after_migration.returncode == 0, after_migration.stderr


def test_postgresql_offline_migration_contains_tenant_rls_policies():
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://vericlaim:placeholder@db.invalid:5432/vericlaim",
        "APP_ENV": "test",
        "AUTO_CREATE_SCHEMA": "false",
    }
    rendered = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "CREATE TABLE auth_sessions" in rendered.stdout
    assert "ENABLE ROW LEVEL SECURITY" in rendered.stdout
    assert 'CREATE POLICY "p_audit_records_tenant"' in rendered.stdout
    assert 'CREATE POLICY "p_document_uploads_tenant"' in rendered.stdout
    assert 'CREATE POLICY "p_analysis_detection_jobs_tenant"' in rendered.stdout
    assert 'CREATE POLICY "p_rules_global_or_tenant"' in rendered.stdout


def test_architecture_metadata_includes_required_aggregates_and_tenant_keys():
    tables = Base.metadata.tables
    assert EXPECTED_TABLES <= set(tables)
    for table_name in TENANT_OWNED_TABLES:
        assert "organization_id" in tables[table_name].c, table_name

    # Regulatory sources and system identities are shared/global by design.
    assert "organization_id" not in tables["regulations"].c
    assert "organization_id" not in tables["users"].c
    assert tables["rules"].c.organization_id.nullable is True
    assert tables["rule_versions"].c.organization_id.nullable is True
    assert tables["audit_records"].c.organization_id.nullable is True
    assert "csrf_token_hash" in tables["auth_sessions"].c


def test_core_dossier_graph_persists_with_explicit_tenant_ownership():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    organization_id = uuid4()
    supplier_id = uuid4()
    product_id = uuid4()
    document_id = uuid4()
    document_version_id = uuid4()
    segment_id = uuid4()
    analysis_id = uuid4()
    analysis_version_id = uuid4()
    claim_id = uuid4()
    evidence_id = uuid4()
    evidence_link_id = uuid4()
    event_id = uuid4()

    with Session(engine) as session:
        organization = domain.Organization(id=organization_id, name="Acme France", slug="acme-fr")
        supplier = domain.Supplier(id=supplier_id, organization_id=organization_id, legal_name="Fournisseur SA")
        product = domain.Product(
            id=product_id,
            organization_id=organization_id,
            supplier_id=supplier_id,
            reference="SKU-001",
            name="Bouteille test",
        )
        document = domain.Document(
            id=document_id,
            organization_id=organization_id,
            supplier_id=supplier_id,
            product_id=product_id,
            document_key="DOC-001",
            title="Fiche produit",
        )
        document_version = domain.DocumentVersion(
            id=document_version_id,
            organization_id=organization_id,
            document_id=document_id,
            version_number=1,
            source_filename="fiche.pdf",
            content_type="application/pdf",
            storage_key="organizations/acme-fr/documents/DOC-001/v1.pdf",
            sha256=_hash("d"),
            size_bytes=42,
        )
        segment = domain.DocumentSegment(
            id=segment_id,
            organization_id=organization_id,
            document_version_id=document_version_id,
            sequence_number=0,
            text="Emballage recyclable.",
            source_sha256=_hash("e"),
        )
        analysis = domain.Analysis(
            id=analysis_id,
            organization_id=organization_id,
            supplier_id=supplier_id,
            product_id=product_id,
            analysis_key="ANL-001",
        )
        analysis_version = domain.AnalysisVersion(
            id=analysis_version_id,
            organization_id=organization_id,
            analysis_id=analysis_id,
            version_number=1,
            engine_version="0.1.0",
            rulebook_version="test-rulebook",
            input_manifest_sha256=_hash("i"),
        )
        claim = domain.Claim(
            id=claim_id,
            organization_id=organization_id,
            analysis_version_id=analysis_version_id,
            document_segment_id=segment_id,
            claim_type="recyclable",
            category="recycling",
            claim_text="Emballage recyclable.",
        )
        evidence = domain.Evidence(
            id=evidence_id,
            organization_id=organization_id,
            product_id=product_id,
            evidence_type=domain.EvidenceType.RECYCLING_ROUTE,
        )
        evidence_link = domain.EvidenceLink(
            id=evidence_link_id,
            organization_id=organization_id,
            claim_id=claim_id,
            evidence_id=evidence_id,
        )
        event = domain.AuditEvent(
            id=event_id,
            organization_id=organization_id,
            entity_type="analysis",
            entity_id=analysis_id,
            action="analysis.created",
            payload_sha256=_hash("p"),
            event_hash=_hash("h"),
        )

        session.add_all(
            [
                organization,
                supplier,
                product,
                document,
                document_version,
                segment,
                analysis,
                analysis_version,
                claim,
                evidence,
                evidence_link,
                event,
            ]
        )
        session.commit()

        assert session.get(domain.AnalysisVersion, analysis_version_id).organization_id == organization_id
        assert session.get(domain.Claim, claim_id).document_segment_id == segment_id
        assert session.get(domain.EvidenceLink, evidence_link_id).evidence_id == evidence_id
        assert session.get(domain.AuditEvent, event_id).event_hash == _hash("h")


def test_initial_alembic_migration_round_trip_matches_metadata(tmp_path: Path):
    database_path = tmp_path / "architecture.db"
    database_url = f"sqlite:///{database_path}"
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "APP_ENV": "test",
        "AUTO_CREATE_SCHEMA": "false",
    }

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    engine = create_engine(database_url)
    migrated_tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= migrated_tables
    with engine.connect() as connection:
        seeded_roles = list(connection.exec_driver_sql("SELECT code, permissions_json FROM roles ORDER BY code"))
    assert [row[0] for row in seeded_roles] == ["admin", "analyst", "owner", "viewer"]
    role_permissions = {code: set(json.loads(permissions)) for code, permissions in seeded_roles}
    assert {"documents:read", "documents:manage"} <= role_permissions["owner"]
    assert {"documents:read", "documents:manage"} <= role_permissions["admin"]
    assert {"documents:read", "documents:manage"} <= role_permissions["analyst"]
    assert "documents:read" in role_permissions["viewer"]
    assert "documents:manage" not in role_permissions["viewer"]

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert downgrade.returncode == 0, downgrade.stderr
    assert inspect(engine).get_table_names() == ["alembic_version"]
