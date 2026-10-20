"""Chantier B: the runtime fails closed rather than inventing a database.

ADR-002 makes PostgreSQL the only source of truth and ADR-003 makes the schema a
boot precondition. Both used to be documentation-only: an on-request SQLite
rescue existed under ``app.core.database``, and a failed schema check was
downgraded to a warning outside production-like environments. These tests pin
the corrected behaviour, because a restored convenience fallback would move
untrusted document metadata and the audit hash chains outside the
RLS-protected, backed-up database while answering 2xx.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.database import DatabaseUnavailableError, get_db

FALLBACK_DB_PATH = Path("/tmp/vericlaim_fallback.db")


def test_module_exposes_no_secondary_session_factory() -> None:
    """The silent SQLite fallback must stay deleted, not merely unused.

    ADR-002 and the release-gate prohibitions both state that an unreachable
    PostgreSQL fails the request visibly. A restored convenience fallback would
    re-open a path where untrusted document metadata and audit hash chains are
    written outside the RLS-protected, backed-up database.
    """
    for removed in ("_get_fallback_sessionmaker", "_fallback_engine", "_FallbackSession"):
        assert not hasattr(database, removed), f"{removed} must not exist in app.core.database"
    source = Path(database.__file__).read_text(encoding="utf-8")
    assert "/tmp/vericlaim_fallback.db" not in source
    assert "create_all" in source  # still legitimate inside create_tables() for dev/test
    assert source.count("create_all") == 1, "create_all must remain confined to create_tables()"


def test_get_db_fails_closed_and_creates_no_fallback_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable database raises, and no scratch schema appears on disk."""
    FALLBACK_DB_PATH.unlink(missing_ok=True)

    def _unreachable_session_factory():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(database, "SessionLocal", _unreachable_session_factory)

    with pytest.raises(DatabaseUnavailableError):
        next(get_db())

    assert not FALLBACK_DB_PATH.exists(), "no SQLite rescue database may be created"
    FALLBACK_DB_PATH.unlink(missing_ok=True)


def test_api_startup_refuses_to_serve_without_a_verified_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed schema check must abort startup in every environment.

    The lifespan hook used to downgrade this to a warning outside
    production-like deployments, so Compose healthchecks, the platform and the
    two workers all observed a healthy API with no usable schema behind it.
    """
    import app.main as main_module

    def _boom() -> None:
        raise RuntimeError("schema is not at the expected Alembic revision")

    monkeypatch.setattr(main_module, "create_tables", _boom)
    with pytest.raises(RuntimeError, match="Alembic revision"):
        with TestClient(main_module.app):
            pass


def test_readiness_probes_the_database_while_liveness_does_not() -> None:
    """/readyz is the gate the workers depend on, so it has to reach PostgreSQL."""
    from app.main import app

    with TestClient(app) as client:
        readiness = client.get("/readyz")
        assert readiness.status_code == 200, readiness.text
        body = readiness.json()
        assert body["status"] == "ready"
        assert body["database"] == "ok"
        # The view must state whether the two mandatory document-safety
        # boundaries are wired, without ever leaking an endpoint or a credential.
        assert set(body) == {"status", "database", "document_storage", "malware_scanner", "document_import"}
        assert client.get("/healthz").status_code == 200


def test_unreachable_database_returns_503_not_a_fabricated_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real outage maps to a tenant-safe 503 on both the route and /readyz."""
    from app.main import app

    def _unreachable_session_factory():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(database, "SessionLocal", _unreachable_session_factory)

    with TestClient(app) as client:
        documents = client.get("/api/v1/documents")
        readiness = client.get("/readyz")

    assert documents.status_code == 503
    assert documents.json() == {"detail": "Le service est temporairement indisponible; réessayez plus tard."}
    assert "sqlite" not in documents.text.lower()
    assert "/tmp" not in documents.text
    assert readiness.status_code == 503
    assert readiness.json()["database"] == "unavailable"
    # Liveness keeps answering: the process is healthy, the dependency is not.
    assert client.get("/healthz").status_code == 200
