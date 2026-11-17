"""Shared test configuration.

SQLite compiles with foreign-key enforcement **off** by default, so the isolated
suite accepted row inserts whose parent row did not exist yet.  PostgreSQL — the
only database allowed outside development/test (ADR-002) — rejects them.  That
divergence hid a real defect: ``complete_document_upload`` set
``document_uploads.finalized_document_version_id`` before inserting the
``document_versions`` row, which is a 500 on PostgreSQL and a silent success on
SQLite.

Turning the pragma on for every test connection makes the isolated suite fail
for the same reasons production does, which is the whole point of having it.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Match PostgreSQL's referential integrity in isolated SQLite runs."""

    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
