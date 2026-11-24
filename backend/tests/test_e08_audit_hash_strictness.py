"""E-08 — le scellement canonique est seul accepté après la bascule.

Le format historique (jointure `:`) reste lisible pour les événements
antérieurs au 21 novembre 2026 ; tout événement postérieur qui porterait ce
format invalide la chaîne — plus qu'une préimage valide par événement.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.audit.service import (
    CANONICAL_HASH_ENFORCED_AFTER,
    compute_event_hash,
    compute_payload_sha256,
    normalize_iso_datetime,
    verify_audit_chain,
)
from app.core.database import SessionLocal, create_tables
from app.identity.service import append_audit_event
from app.models.domain import AuditEvent
from fastapi.testclient import TestClient

from app.main import app
from tests.auth_support import authenticate_client


@pytest.fixture(autouse=True)
def setup_database():
    create_tables()


def _chain_setup():
    """Une organisation authentifiée dont on peut écrire la chaîne en direct."""
    client = TestClient(app)
    identity = authenticate_client(client, role_code="analyst")
    return identity


def test_recent_events_in_legacy_format_invalidate_the_chain():
    identity = _chain_setup()
    with SessionLocal() as db:
        payload_hash = compute_payload_sha256({})
        recent = datetime.now(timezone.utc)
        legacy_hash = compute_event_hash(
            identity.organization_id, "smuggled", None, "recent-legacy",
            normalize_iso_datetime(recent), payload_hash, None,
        )
        db.add(
            AuditEvent(
                organization_id=identity.organization_id,
                entity_type="smuggled",
                action="recent-legacy",
                occurred_at=recent,
                payload_json={},
                payload_sha256=payload_hash,
                event_hash=legacy_hash,
            )
        )
        db.commit()
        verification = verify_audit_chain(db, identity.organization_id)
        assert not verification.is_valid
        assert verification.tampered_event_id is not None
        db.rollback()


def test_canonical_events_after_the_cutoff_stay_valid():
    identity = _chain_setup()
    with SessionLocal() as db:
        append_audit_event(
            db,
            organization_id=identity.organization_id,
            actor_user_id=identity.user_id,
            entity_type="verification",
            entity_id=None,
            action="e08.canonical",
            payload={"traité": "oui"},
        )
        db.commit()
        verification = verify_audit_chain(db, identity.organization_id)
        assert verification.is_valid, verification.error_detail


def test_cutoff_constant_is_dated():
    """La bascule est explicite : un futur changement de règle doit la voir."""
    assert CANONICAL_HASH_ENFORCED_AFTER == datetime(2026, 11, 21, tzinfo=timezone.utc)
