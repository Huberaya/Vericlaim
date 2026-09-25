"""Small, dependency-light helpers for controlled Neon PostgreSQL operations."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


def postgres_dsn_from_environment(name: str) -> str:
    """Return a libpq-compatible PostgreSQL URI without ever printing it.

    The application uses SQLAlchemy's ``postgresql+psycopg`` URI scheme while
    Psycopg expects the libpq ``postgresql`` scheme. The credentials, host and
    query string are preserved; only the driver marker is removed.
    """

    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not configured.")

    parsed = urlsplit(value)
    allowed_schemes = {"postgresql", "postgres", "postgresql+psycopg"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname or not parsed.path.strip("/"):
        raise RuntimeError(f"{name} must contain a PostgreSQL connection URI.")

    scheme = "postgresql" if parsed.scheme == "postgresql+psycopg" else parsed.scheme
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))


def require_tls(connection: object) -> None:
    """Fail closed when a database operation is not protected by TLS."""

    pgconn = getattr(connection, "pgconn", None)
    if pgconn is None or not bool(pgconn.ssl_in_use):
        raise RuntimeError("Refusing database operation: PostgreSQL TLS is not active.")
