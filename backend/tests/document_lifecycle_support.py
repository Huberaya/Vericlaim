"""Deterministic helpers for the Chantier B document-lifecycle regressions.

Kept out of ``tests/document_support.py`` on purpose: that module belongs to the
security boundary tests and is being extended on the parallel authentication
branch, so a new file avoids editing the same lines twice.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.document_support import FakeObjectStorage, FakeScanner

# A structurally valid PDF payload: the ingestion boundary only checks the
# signature, so no parser and no external service is required.
PDF_BYTES = b"%PDF-1.7\n% lifecycle-regression\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def install_document_fakes():
    """Swap the app's storage/scanner boundaries for the deterministic fakes."""
    from app.main import app

    old_storage = app.state.document_storage
    old_scanner = app.state.document_scanner
    storage = FakeObjectStorage()
    scanner = FakeScanner()
    app.state.document_storage = storage
    app.state.document_scanner = scanner
    return app, storage, scanner, old_storage, old_scanner


def create_document_and_upload_intent(client: TestClient, *, title: str = "Pièce à recycler") -> tuple[dict, dict]:
    """Create the logical document plus its quarantine intent; transfer nothing."""
    response = client.post(
        "/api/v1/documents",
        json={"title": title, "document_type": "supplier_declaration", "tags": ["regression"]},
    )
    assert response.status_code == 201, response.text
    document = response.json()
    response = client.post(
        f"/api/v1/documents/{document['id']}/versions",
        json={"source_filename": "fiche.pdf", "content_type": "application/pdf", "size_bytes": len(PDF_BYTES)},
    )
    assert response.status_code == 201, response.text
    return document, response.json()


def promote_clean_version(client: TestClient, storage: FakeObjectStorage, instruction: dict) -> dict:
    """Upload the bytes, then run the completion endpoint to a promoted version."""
    storage.upload_latest(payload=PDF_BYTES)
    completion = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
    assert completion.status_code == 201, completion.text
    return completion.json()
