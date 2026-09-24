from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


class AuditRecord(Base):
    __tablename__ = "audit_records"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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


def create_tables() -> None:
    global engine, SessionLocal
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        # Fallback automatique sur SQLite si le serveur PostgreSQL n'est pas démarré (démos locales)
        if not settings.database_url.startswith("sqlite"):
            import logging
            logging.warning("Connexion à %s impossible (%s). Bascule automatique sur SQLite local.", settings.database_url, exc)
            fallback_url = "sqlite:///./vericlaim.db"
            engine = create_engine(fallback_url, connect_args={"check_same_thread": False})
            SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
            Base.metadata.create_all(bind=engine)
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
) -> tuple[str | None, str, datetime]:
    """Append a hash-linked audit envelope. This is tamper-evident, not notarised."""
    created = created_at_utc or datetime.now(timezone.utc)
    previous = db.scalar(select(AuditRecord).order_by(AuditRecord.sequence.desc()).limit(1))
    previous_hash = previous.record_hash if previous else None
    material = {
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
    db.commit()
    return previous_hash, record_hash, created
