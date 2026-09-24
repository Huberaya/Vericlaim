from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(32), default="standard")
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(128))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    hashed_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditRecord(Base):
    __tablename__ = "audit_records"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    organization_id: Mapped[str] = mapped_column(String(36), default="default", index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    evidence_manifest_sha256: Mapped[str] = mapped_column(String(64))
    report_sha256: Mapped[str] = mapped_column(String(64))
    previous_record_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    supplier_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    product_identifier: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


_engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    if settings.database_url.endswith(":memory:"):
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _migrate_columns() -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        for col_name, col_type in [
            ("supplier_name", "VARCHAR(128)"),
            ("product_identifier", "VARCHAR(128)"),
            ("report_json", "JSON" if not str(engine.url).startswith("sqlite") else "TEXT"),
            ("organization_id", "VARCHAR(36) DEFAULT 'default'"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE audit_records ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass


def ensure_default_organization() -> None:
    try:
        with SessionLocal() as db:
            default_org = db.scalar(select(Organization).where(Organization.id == "default"))
            if not default_org:
                default_org = Organization(
                    id="default",
                    name="Organisation Principale (Démo)",
                    slug="default-demo",
                    tier="enterprise",
                    created_at_utc=datetime.now(timezone.utc),
                    is_active=True,
                )
                db.add(default_org)
                db.commit()
    except Exception:
        pass


def create_tables() -> None:
    global engine, SessionLocal
    try:
        Base.metadata.create_all(bind=engine)
        _migrate_columns()
        ensure_default_organization()
    except Exception as exc:
        # Fallback automatique sur SQLite si le serveur PostgreSQL n'est pas démarré (démos locales)
        if not settings.database_url.startswith("sqlite"):
            import logging
            logging.warning("Connexion à %s impossible (%s). Bascule automatique sur SQLite local.", settings.database_url, exc)
            fallback_url = "sqlite:///./vericlaim.db"
            engine = create_engine(fallback_url, connect_args={"check_same_thread": False})
            SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
            Base.metadata.create_all(bind=engine)
            _migrate_columns()
            ensure_default_organization()
        else:
            raise


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def append_audit_record(
    db: Session,
    *,
    audit_id: str,
    source_sha256: str,
    evidence_manifest_sha256: str,
    report_sha256: str,
    summary: dict[str, Any],
    created_at_utc: datetime | None = None,
    supplier_name: str | None = None,
    product_identifier: str | None = None,
    report_json: dict[str, Any] | None = None,
    organization_id: str = "default",
) -> tuple[str | None, str, datetime]:
    """Append a hash-linked audit envelope partitioned per tenant. Tamper-evident and isolated."""
    created = created_at_utc or datetime.now(timezone.utc)
    previous = db.scalar(
        select(AuditRecord)
        .where(AuditRecord.organization_id == organization_id)
        .order_by(AuditRecord.sequence.desc())
        .limit(1)
    )
    previous_hash = previous.record_hash if previous else None
    material = {
        "organization_id": organization_id,
        "audit_id": audit_id,
        "created_at_utc": created.isoformat(),
        "source_sha256": source_sha256,
        "evidence_manifest_sha256": evidence_manifest_sha256,
        "report_sha256": report_sha256,
        "previous_record_hash": previous_hash,
        "summary": summary,
        "supplier_name": supplier_name,
        "product_identifier": product_identifier,
    }
    record_hash = sha256_json(material)
    row = AuditRecord(
        audit_id=audit_id,
        organization_id=organization_id,
        created_at_utc=created,
        source_sha256=source_sha256,
        evidence_manifest_sha256=evidence_manifest_sha256,
        report_sha256=report_sha256,
        previous_record_hash=previous_hash,
        record_hash=record_hash,
        summary_json=summary,
        supplier_name=supplier_name,
        product_identifier=product_identifier,
        report_json=report_json,
    )
    db.add(row)
    db.commit()
    return previous_hash, record_hash, created
