from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, MetaData, String, Uuid, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


# Stable names make Alembic diffs and production database diagnostics readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AuditRecord(Base):
    """Legacy engine audit envelope retained for backward API compatibility.

    The multi-tenant, actor-aware event log is ``app.models.domain.AuditEvent``.
    This table remains in the initial migration so existing `/engine/evaluate`
    behaviour and its hash chain are not broken while the new workflow is built.
    The Chantier 2 migration adds a nullable ``organization_id``: historical
    rows remain preservable while every new authenticated audit is tenant-scoped.
    """

    __tablename__ = "audit_records"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    audit_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    evidence_manifest_sha256: Mapped[str] = mapped_column(String(64))
    report_sha256: Mapped[str] = mapped_column(String(64))
    previous_record_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)


_engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    if settings.database_url.endswith(":memory:"):
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _expected_alembic_revision() -> str:
    """Return the single migration head packaged with this backend."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[2]
    config_path = os.getenv("ALEMBIC_CONFIG", str(backend_root / "alembic.ini"))
    script = ScriptDirectory.from_config(Config(config_path))
    head = script.get_current_head()
    if not head:
        raise RuntimeError("Alembic has no migration head; database startup is unsafe.")
    return head


def verify_schema_at_head() -> None:
    """Fail closed when a shared database has not received the packaged migration."""
    from alembic.runtime.migration import MigrationContext

    expected = _expected_alembic_revision()
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:
        raise RuntimeError(
            "Database schema could not be verified. Run `alembic upgrade head` "
            "with the configured DATABASE_URL before starting the API."
        ) from exc

    if current != expected:
        raise RuntimeError(
            "Database schema is not at the expected Alembic revision "
            f"({current or 'base'} != {expected}). Run `alembic upgrade head` "
            "before starting the API."
        )


def create_tables() -> None:
    """Create local/test tables or verify that a shared schema is migration-current."""
    if not settings.auto_create_schema:
        verify_schema_at_head()
        return

    # Deferred import keeps the legacy engine independently importable while
    # ensuring all architecture-foundation models are registered for local
    # development and isolated tests.
    from app.models import domain as _domain_models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Yield one transaction per HTTP request.

    Request-scoped PostgreSQL settings used by RLS are installed with
    ``set_config(..., true)`` and therefore remain active until this transaction
    ends.  Service functions must ``flush`` for integrity errors, not commit
    independently.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def append_audit_record(
    db: Session,
    *,
    organization_id: UUID,
    audit_id: str,
    source_sha256: str,
    evidence_manifest_sha256: str,
    report_sha256: str,
    summary: dict[str, Any],
    created_at_utc: datetime | None = None,
) -> tuple[str | None, str, datetime]:
    """Append a hash-linked engine audit envelope within one organisation.

    This is tamper-evident, not notarised. ``AuditEvent`` adds actor/resource
    context; stronger transaction serialization remains a dedicated audit
    workstream.
    """

    created = created_at_utc or datetime.now(timezone.utc)
    previous = db.scalar(
        select(AuditRecord)
        .where(AuditRecord.organization_id == organization_id)
        .order_by(AuditRecord.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    previous_hash = previous.record_hash if previous else None
    material = {
        "organization_id": str(organization_id),
        "audit_id": audit_id,
        "created_at_utc": created.isoformat(),
        "source_sha256": source_sha256,
        "evidence_manifest_sha256": evidence_manifest_sha256,
        "report_sha256": report_sha256,
        "previous_record_hash": previous_hash,
        "summary": summary,
    }
    record_hash = sha256_json(material)
    row = AuditRecord(
        organization_id=organization_id,
        audit_id=audit_id,
        created_at_utc=created,
        source_sha256=source_sha256,
        evidence_manifest_sha256=evidence_manifest_sha256,
        report_sha256=report_sha256,
        previous_record_hash=previous_hash,
        record_hash=record_hash,
        summary_json=summary,
    )
    db.add(row)
    db.flush()
    return previous_hash, record_hash, created
