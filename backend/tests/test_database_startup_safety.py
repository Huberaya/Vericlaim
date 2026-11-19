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
from app.core import database
from app.core.database import DatabaseUnavailableError, get_db
from fastapi.testclient import TestClient

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
        # Contrat A-10 : une vue par dépendance (`checks`) et une synthèse
        # (`capabilities`). Le champ `database` de l'ancienne vue est remplacé par
        # le contrôle nommé « database », qui porte en plus la raison d'un échec.
        database_check = next(check for check in body["checks"] if check["name"] == "database")
        assert database_check["status"] == "ok"
        assert body["capabilities"]["database"] is True
        # La vue doit dire si les deux frontières documentaires sont câblées, sans
        # jamais laisser fuir un endpoint ni un identifiant.
        assert set(body) == {"status", "environment", "checks", "capabilities", "failed"}
        names = {check["name"] for check in body["checks"]}
        assert names == {"database", "migrations", "document_storage", "malware_scanner"}
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
    # Le message doit dire *pourquoi* le service est indisponible, et ne jamais
    # contenir de DSN ni de chemin : la cause est la base, le détail technique
    # reste côté serveur. `code` donne un identifiant stable aux clients.
    body = documents.json()
    assert body["code"] == "database_unavailable"
    assert "injoignable" in body["detail"]
    assert "://" not in body["detail"]
    assert "sqlite" not in documents.text.lower()
    assert "/tmp" not in documents.text
    assert readiness.status_code == 503
    # Contrat A-10 : la vue liste une entrée par dépendance plutôt qu'un champ
    # unique. Le contrôle nommé porte le statut *et* la raison.
    ready_body = readiness.json()
    database_check = next(c for c in ready_body["checks"] if c["name"] == "database")
    assert database_check["status"] == "failed", ready_body
    assert ready_body["capabilities"]["database"] is False
    # ``failed`` énumère les contrôles en échec (pas un booléen).
    assert "database" in ready_body["failed"]
    # Liveness keeps answering: the process is healthy, the dependency is not.
    assert client.get("/healthz").status_code == 200
