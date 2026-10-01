"""C18 — deep readiness, permission boundary, and the missing document listing.

The plan's acceptance criterion for this chantier is a pair of failures observed on
purpose:

    « Base coupée → `/readyz` renvoie non-prêt · `viewer` → 403 sur
      `/enterprise/api-keys` »

Both are executed below, the first in a **subprocess with a deliberately unreachable
database** rather than with a monkeypatched probe: a test that replaces the probe
would prove that the test double works, not that the product does.

The third defect of C18 — `GET /api/v1/documents` answering 405 because the
collection simply had no listing route — is covered by the pagination tests at the
end of this module.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import create_tables
from app.main import app
from tests.auth_support import authenticate_client
from tests.document_support import FakeObjectStorage, FakeScanner

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: A path no process can create: the parent directory does not exist and cannot be
#: created in this sandbox. SQLite then fails on connect, which is what a database
#: outage looks like from the application's point of view.
UNREACHABLE_DATABASE = "sqlite+pysqlite:////proc/vericlaim-nonexistent/vericlaim.db"


@pytest.fixture()
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


def _boot_and_probe(database_url: str) -> dict[str, object]:
    """Boot a real app in a subprocess and report what the two probes answer."""
    script = """
import json, sys
sys.path.insert(0, %r)
from fastapi.testclient import TestClient
from app.core.database import create_tables
from app.main import app

try:
    create_tables()
except Exception as exc:
    schema_error = type(exc).__name__
else:
    schema_error = None

with TestClient(app) as client:
    payload = {
        "schema_error": schema_error,
        "healthz": client.get("/healthz").status_code,
        "healthz_body": client.get("/healthz").json(),
        "readyz": client.get("/readyz").status_code,
        "readyz_body": client.get("/readyz").json(),
    }
print(json.dumps(payload))
""" % str(BACKEND_ROOT)

    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": ".",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(pathlib.Path.home()),
            "APP_ENV": "development",
            "DATABASE_URL": database_url,
            "EMAIL_BACKEND": "outbox",
        },
    )
    assert process.returncode == 0, process.stdout + process.stderr
    for line in reversed(process.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"aucune réponse JSON dans la sortie:\n{process.stdout}\n{process.stderr}")


def test_liveness_answers_when_the_database_is_unreachable():
    """A liveness probe must never fail because of a dependency.

    If it did, an orchestrator would restart the container in a loop while the real
    problem — an unreachable database — cannot be fixed by a restart.
    """
    report = _boot_and_probe(UNREACHABLE_DATABASE)
    assert report["healthz"] == 200, report
    assert report["healthz_body"] == {"status": "alive", "service": "vericlaim-rule-engine"}, report
    # And, to prove the liveness answer is not an accident: the same instance is not ready.
    assert report["readyz"] == 503, report


def test_readiness_reports_not_ready_when_the_database_is_unreachable():
    """The plan's acceptance criterion: database cut → /readyz answers not-ready."""
    report = _boot_and_probe(UNREACHABLE_DATABASE)
    assert report["readyz"] == 503, report
    body = report["readyz_body"]
    assert body["status"] == "not_ready"
    database_check = next(check for check in body["checks"] if check["name"] == "database")
    assert database_check["status"] == "failed"
    assert database_check["required"] is True
    assert "database" in body["failed"]
    assert body["capabilities"]["database"] is False


def test_the_public_probe_does_not_leak_infrastructure_details():
    report = _boot_and_probe(UNREACHABLE_DATABASE)
    serialized = json.dumps(report["readyz_body"])
    for forbidden in ("sqlite", "postgres", "vericlaim-quarantine", "vericlaim-documents", "://", "password"):
        assert forbidden not in serialized, f"'{forbidden}' apparaît dans la réponse publique de /readyz"
    # The exception class is reported; the message, which can contain a DSN, is not.
    database_check = next(check for check in report["readyz_body"]["checks"] if check["name"] == "database")
    assert database_check["detail"].startswith("exception: ")


def test_healthz_does_not_claim_more_than_the_process(client):
    """`/healthz` used to answer {"status": "ok"} while checking nothing at all."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert "ok" not in json.dumps(response.json()), "« ok » n'est pas ce que la sonde de vivacité vérifie"


def test_readyz_is_ready_locally_and_names_the_disabled_capabilities(client):
    """A correct local instance is ready, and says what it cannot do."""
    response = client.get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["capabilities"] == {
        "database": True,
        "document_storage": False,
        "malware_scanning": False,
    }
    storage = next(check for check in body["checks"] if check["name"] == "document_storage")
    assert storage["status"] == "disabled"
    assert "documents ne peuvent pas être importés" in storage["detail"]


def test_readyz_fails_when_the_configured_storage_is_unreachable(client):
    """A configured storage that cannot be reached must fail readiness, not be skipped."""
    broken_storage = FakeObjectStorage()
    broken_storage.fail_operations.add("probe")
    configured = replace(
        settings,
        document_storage_backend="s3",
        document_scanner_mode="test",
    )
    with patch("app.main.settings", configured), patch.object(app.state, "document_storage", broken_storage):
        response = client.get("/readyz")
    assert response.status_code == 503, response.text
    body = response.json()
    storage = next(check for check in body["checks"] if check["name"] == "document_storage")
    assert storage["status"] == "failed"
    assert storage["required"] is True
    assert "document_storage" in body["failed"]
    assert body["capabilities"]["document_storage"] is False


def test_readyz_fails_when_the_antivirus_is_unreachable(client):
    configured = replace(settings, document_scanner_mode="clamav", document_clamav_host="antivirus.interne")
    scanner = FakeScanner(available=False)
    with patch("app.main.settings", configured), patch.object(app.state, "document_scanner", scanner):
        response = client.get("/readyz")
    assert response.status_code == 503, response.text
    check = next(item for item in response.json()["checks"] if item["name"] == "malware_scanner")
    assert check["status"] == "failed"


def test_a_probe_that_raises_is_a_failed_probe_not_a_passing_one(client):
    class ExplodingStorage:
        def probe(self, *, bucket: str) -> tuple[bool, str]:
            raise RuntimeError(f"endpoint secret https://minio.interne:9000 bucket={bucket}")

    configured = replace(settings, document_storage_backend="s3", document_scanner_mode="test")
    with patch("app.main.settings", configured), patch.object(app.state, "document_storage", ExplodingStorage()):
        response = client.get("/readyz")
    assert response.status_code == 503
    check = next(item for item in response.json()["checks"] if item["name"] == "document_storage")
    assert check["status"] == "failed"
    assert check["detail"] == "exception: RuntimeError"
    assert "minio.interne" not in json.dumps(response.json()), "l'exception fuite dans la réponse publique"


def test_an_adapter_without_a_probe_is_refused_rather_than_trusted(client):
    """Fail closed: an unverifiable dependency is not a verified dependency."""

    class NoProbeStorage:
        pass

    configured = replace(settings, document_storage_backend="s3", document_scanner_mode="test")
    with patch("app.main.settings", configured), patch.object(app.state, "document_storage", NoProbeStorage()):
        response = client.get("/readyz")
    assert response.status_code == 503
    check = next(item for item in response.json()["checks"] if item["name"] == "document_storage")
    assert check["detail"].startswith("adaptateur de stockage sans sonde")


# --------------------------------------------------------------------------- #
# /enterprise/api-keys must not be readable by every member
# --------------------------------------------------------------------------- #


def test_a_viewer_cannot_read_the_api_key_listing(client):
    """The plan's second acceptance criterion: viewer → 403 on /enterprise/api-keys."""
    authenticate_client(client, role_code="viewer")
    response = client.get("/api/v1/enterprise/api-keys")
    assert response.status_code == 403, response.text
    assert "api-keys" not in response.text or "permission" in response.text.lower()


def test_an_owner_can_read_the_api_key_listing(client):
    authenticate_client(client, role_code="owner")
    response = client.get("/api/v1/enterprise/api-keys")
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_an_anonymous_caller_cannot_read_the_api_key_listing(client):
    assert client.get("/api/v1/enterprise/api-keys").status_code == 401


# --------------------------------------------------------------------------- #
# GET /api/v1/documents (previously 405: the collection had no listing route)
# --------------------------------------------------------------------------- #


def test_the_document_collection_requires_a_session(client):
    response = client.get("/api/v1/documents")
    assert response.status_code == 401, (
        "la liste des documents doit exiger une session, pas répondre 405 ni 200"
    )


def test_the_document_collection_paginates_and_stays_tenant_scoped(client):
    """Three documents are listed, page by page, without leaving the tenant."""
    # The API is the only way a client can create a document, so the fixtures go
    # through it: the listing must return what the product really accepts.
    authenticate_client(client, role_code="owner")

    for index, title in enumerate(("Alpha emballage", "Beta étiquette", "Gamma carton")):
        # Idempotency-Key is an HTTP header: ASCII only, hence the index rather than
        # the (accented) title.
        created = client.post(
            "/api/v1/documents",
            json={"title": title, "document_type": "other"},
            headers={"Idempotency-Key": f"c18-liste-{index}"},
        )
        assert created.status_code == 201, created.text

    first = client.get("/api/v1/documents?limit=2")
    assert first.status_code == 200, first.text
    page = first.json()
    assert [item["title"] for item in page["items"]] == ["Alpha emballage", "Beta étiquette"]
    assert page["next_cursor"], "un curseur est attendu quand il reste des lignes"

    second = client.get(f"/api/v1/documents?limit=2&cursor={page['next_cursor']}")
    assert second.status_code == 200, second.text
    assert [item["title"] for item in second.json()["items"]] == ["Gamma carton"]
    assert second.json()["next_cursor"] is None

    # The literal route must not be swallowed by /{document_id}: a malformed cursor
    # is a client error on the collection, not a "document introuvable".
    malformed = client.get("/api/v1/documents?cursor=pas-un-curseur")
    assert malformed.status_code == 422, malformed.text


def test_the_document_listing_is_not_readable_across_tenants(client):
    """A second organization must not see the first one's documents."""
    authenticate_client(client, role_code="owner")
    created = client.post(
        "/api/v1/documents",
        json={"title": "Document du tenant A", "document_type": "other"},
        headers={"Idempotency-Key": "tenant-a-document"},
    )
    assert created.status_code == 201

    with TestClient(app) as other:
        authenticate_client(other, role_code="owner")
        listing = other.get("/api/v1/documents")
        assert listing.status_code == 200
        titles = [item["title"] for item in listing.json()["items"]]
        assert "Document du tenant A" not in titles, "fuite inter-tenant dans la liste des documents"
