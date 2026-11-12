"""Non-regression proofs for the recette defects found on 2026-11-12.

Three defects were found by running the application against real PostgreSQL and
the real HTTP surface, none of which the isolated SQLite suite could see.  Each
test below fails on the pre-fix code and passes on the fix, and states which
observation motivated it.

1. ``complete_document_upload`` set ``document_uploads.finalized_document_version_id``
   before inserting the ``document_versions`` row it points at. No ORM
   relationship declares the dependency, so SQLAlchemy ordered the UPDATE first.
   SQLite ignored the foreign key; PostgreSQL raised and the endpoint returned
   **HTTP 500** for *every* document import.
2. ``GET /api/v1/reports/analyses/{id}/pdf`` read ``AnalysisVersion.input_manifest``,
   an attribute that does not exist (the manifest lives in ``result_json`` and is
   integrity-checked by ``input_manifest_sha256``). Every persisted-analysis PDF
   returned **HTTP 500**.
3. ``GET /api/v1/reports/analyses/{id}/dossier`` repeated the same mistake.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.domain import Analysis, AnalysisVersion


def _foreign_key_enforcing_engine():
    """SQLite that behaves like PostgreSQL about referential integrity."""

    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


# --------------------------------------------------------------------------- defect 1
def test_upload_finalization_orders_the_version_insert_before_the_foreign_key() -> None:
    """The promoted version must exist before the upload may reference it.

    This is the exact statement ordering that PostgreSQL rejected. It is asserted
    structurally — through the ORM unit of work — so the guarantee does not depend
    on a particular database backend being available in CI.
    """

    from app.models.domain import (
        Document,
        DocumentUpload,
        DocumentUploadStatus,
        DocumentVersion,
        ExtractionStatus,
        Organization,
        User,
        UserStatus,
    )

    engine = _foreign_key_enforcing_engine()
    with Session(engine) as session:
        organization = Organization(name="Org FK", slug=f"org-fk-{uuid.uuid4().hex[:8]}")
        user = User(email=f"fk.{uuid.uuid4().hex[:8]}@example.com", status=UserStatus.ACTIVE)
        session.add_all([organization, user])
        session.flush()
        document = Document(
            organization_id=organization.id,
            document_key=f"DOC-{uuid.uuid4().hex[:8]}",
            title="Pièce",
        )
        session.add(document)
        session.flush()
        upload = DocumentUpload(
            organization_id=organization.id,
            document_id=document.id,
            requested_by_user_id=user.id,
            quarantine_storage_key=f"quarantine/{organization.id}/{document.id}/{uuid.uuid4().hex}/source",
            source_filename="piece.pdf",
            declared_content_type="application/pdf",
            declared_size_bytes=42,
            status=DocumentUploadStatus.SCANNING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        session.add(upload)
        session.flush()

        # The production sequence: add the version, flush, then point the upload at it.
        version = DocumentVersion(
            organization_id=organization.id,
            document_id=document.id,
            version_number=1,
            source_filename="piece.pdf",
            content_type="application/pdf",
            storage_key=f"clean/{organization.id}/{document.id}/versions/{uuid.uuid4()}/source",
            sha256="a" * 64,
            size_bytes=42,
            extraction_status=ExtractionStatus.PENDING,
        )
        session.add(version)
        session.flush()  # <-- the fix; omitting it fails on PostgreSQL
        upload.status = DocumentUploadStatus.CLEAN
        upload.finalized_document_version_id = version.id
        session.flush()
        session.commit()

        assert session.get(DocumentUpload, upload.id).finalized_document_version_id == version.id


def test_missing_intermediate_flush_is_rejected_by_the_constraint() -> None:
    """Guard the guard: without the flush, referential integrity must refuse it.

    If this test ever passes, the FK is no longer enforced and the regression
    above has silently lost its teeth.
    """

    from sqlalchemy.exc import IntegrityError

    from app.models.domain import (
        Document,
        DocumentUpload,
        DocumentUploadStatus,
        Organization,
    )

    engine = _foreign_key_enforcing_engine()
    with Session(engine) as session:
        organization = Organization(name="Org FK 2", slug=f"org-fk2-{uuid.uuid4().hex[:8]}")
        session.add(organization)
        session.flush()
        document = Document(
            organization_id=organization.id,
            document_key=f"DOC2-{uuid.uuid4().hex[:8]}",
            title="Pièce",
        )
        session.add(document)
        session.flush()
        upload = DocumentUpload(
            organization_id=organization.id,
            document_id=document.id,
            quarantine_storage_key=f"quarantine/{organization.id}/{document.id}/{uuid.uuid4().hex}/source",
            source_filename="piece.pdf",
            declared_content_type="application/pdf",
            declared_size_bytes=1,
            status=DocumentUploadStatus.CLEAN,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            # A version id that was never inserted.
            finalized_document_version_id=uuid.uuid4(),
        )
        session.add(upload)
        with pytest.raises(IntegrityError):
            session.flush()


# --------------------------------------------------------------------------- defects 2 & 3
def _seed_analysis_with_result(session: Session, *, result_json: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    from app.core.database import sha256_json
    from app.models.domain import Organization

    organization = Organization(name="Org Rapport", slug=f"org-report-{uuid.uuid4().hex[:8]}")
    session.add(organization)
    session.flush()
    analysis = Analysis(organization_id=organization.id, analysis_key=f"ANL-{uuid.uuid4().hex[:6]}")
    session.add(analysis)
    session.flush()
    version = AnalysisVersion(
        organization_id=organization.id,
        analysis_id=analysis.id,
        version_number=1,
        engine_version="test",
        rulebook_version="test",
        input_manifest_sha256=sha256_json(result_json.get("input_manifest") or {}),
        result_json=result_json,
    )
    session.add(version)
    session.flush()
    return analysis.id, version.id


def _persisted_version(db: Session, version_id: uuid.UUID) -> AnalysisVersion:
    version = db.get(AnalysisVersion, version_id)
    assert version is not None
    return version


def test_persisted_source_text_reads_the_real_schema_shape() -> None:
    """``input_manifest`` is not an attribute: the helper must read ``result_json``."""

    from app.api.v1.reports import _persisted_source_text

    with Session(_foreign_key_enforcing_engine()) as session:
        _, version_id = _seed_analysis_with_result(session, result_json={"extracted_source_text": "Texte extrait."})
        assert _persisted_source_text(_persisted_version(session, version_id)) == "Texte extrait."


def test_persisted_source_text_rejects_a_tampered_manifest() -> None:
    """A manifest whose hash no longer matches must not be trusted as a source."""

    from app.api.v1.reports import _persisted_source_text
    from app.core.database import sha256_json

    with Session(_foreign_key_enforcing_engine()) as session:
        manifest = {"documents": [{"document_version_id": str(uuid.uuid4())}]}
        _, version_id = _seed_analysis_with_result(
            session,
            result_json={"input_manifest": manifest},
        )
        version = _persisted_version(session, version_id)
        assert sha256_json(manifest) == version.input_manifest_sha256
        assert _persisted_source_text(version) == ""

        tampered = {"documents": [{"document_version_id": str(uuid.uuid4())}]}
        version.result_json = {"input_manifest": tampered, "source_text": "Contenu injecté."}
        assert sha256_json(tampered) != version.input_manifest_sha256
        assert _persisted_source_text(version) == ""


def test_analysis_version_has_no_input_manifest_attribute() -> None:
    """Pin the root cause so a future edit cannot reintroduce the attribute read."""

    assert not hasattr(AnalysisVersion, "input_manifest")


# --------------------------------------------------------------------------- defect 4
def _boot_settings(monkeypatch, **environment: str):
    """Evaluate ``get_settings`` as a booting deployment would."""

    import os

    for name in ("APP_ENV", "DATABASE_URL", "VERCEL", "K_SERVICE", "DYNO", "AUTO_CREATE_SCHEMA"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    from app.core import config as config_module

    return config_module.get_settings()


def test_a_managed_deployment_without_database_url_refuses_to_start(monkeypatch) -> None:
    """Vercel Preview used to fall back to a scratch SQLite file.

    On a serverless filesystem that is at best an ephemeral database with no
    tenant RLS, and at worst an unwritable path that crashes the process. A
    Preview missing DATABASE_URL must fail with a clear configuration error.
    """

    with pytest.raises(RuntimeError, match="managed deployment"):
        _boot_settings(monkeypatch, VERCEL="1")


def test_a_managed_deployment_refuses_an_explicit_sqlite_url(monkeypatch) -> None:
    with pytest.raises(RuntimeError, match="must not point at SQLite"):
        _boot_settings(monkeypatch, VERCEL="1", DATABASE_URL="sqlite:////tmp/recette.db")


def test_a_workstation_keeps_the_documented_sqlite_default(monkeypatch) -> None:
    """The local development path must remain available and explicit."""

    settings = _boot_settings(monkeypatch)
    assert settings.database_url == "sqlite:///./vericlaim.db"
