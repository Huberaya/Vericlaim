from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_schemas import (
    AuditChainVerificationResponse,
    AuditEventResponse,
    AuditIntegrityCertificateResponse,
)
from app.models.domain import AuditEvent, Organization


def normalize_iso_datetime(dt: datetime) -> str:
    """Normalise un datetime en chaîne ISO-8601 UTC déterministe."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_payload_sha256(payload: Dict[str, Any]) -> str:
    """Calcule l'empreinte SHA-256 canonique d'un payload JSON."""
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_event_hash(
    organization_id: UUID,
    entity_type: str,
    entity_id: Optional[UUID],
    action: str,
    occurred_at_iso: str,
    payload_sha256: str,
    previous_event_hash: Optional[str],
) -> str:
    """Calcule le hash cryptographique de scellement d'un événement d'audit."""
    raw_signature = ":".join([
        str(organization_id),
        entity_type,
        str(entity_id or ""),
        action,
        occurred_at_iso,
        payload_sha256,
        previous_event_hash or "GENESIS",
    ])
    return hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()


def record_audit_event(
    db: Session,
    organization_id: UUID,
    entity_type: str,
    action: str,
    payload: Dict[str, Any],
    actor_user_id: Optional[UUID] = None,
    entity_id: Optional[UUID] = None,
    request_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> AuditEvent:
    """Enregistre un événement métier de manière append-only et hash-chaînée par organisation."""
    event_time = occurred_at or datetime.now(timezone.utc)
    occurred_at_iso = normalize_iso_datetime(event_time)

    # 1. Hachage du payload canonique
    payload_sha256 = compute_payload_sha256(payload)

    # 2. Récupération du dernier événement de la chaîne pour cette organisation
    latest_event = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )

    previous_event_hash = latest_event.event_hash if latest_event else None

    # 3. Calcul de l'empreinte cryptographique de scellement
    event_hash = compute_event_hash(
        organization_id=organization_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        occurred_at_iso=occurred_at_iso,
        payload_sha256=payload_sha256,
        previous_event_hash=previous_event_hash,
    )

    audit_entry = AuditEvent(
        id=uuid4(),
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        occurred_at=event_time,
        request_id=request_id,
        payload_json=payload,
        payload_sha256=payload_sha256,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
    )

    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)
    return audit_entry


def list_audit_events(
    db: Session,
    organization_id: UUID,
    entity_type: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 50,
) -> List[AuditEvent]:
    """Liste les événements d'audit d'une organisation par ordre chronologique inverse."""
    query = (
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if action:
        query = query.where(AuditEvent.action == action)

    return list(db.scalars(query).all())


def verify_audit_chain(
    db: Session,
    organization_id: UUID,
) -> AuditChainVerificationResponse:
    """Vérifie mathématiquement l'intégralité de la chaîne de hachage d'une organisation."""
    now = datetime.now(timezone.utc)
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id)
            .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
        ).all()
    )

    if not events:
        return AuditChainVerificationResponse(
            is_valid=True,
            organization_id=organization_id,
            total_events=0,
            head_event_hash=None,
            genesis_event_hash=None,
            first_event_at=None,
            last_event_at=None,
            tampered_event_id=None,
            error_detail=None,
            verified_at=now,
        )

    previous_hash: Optional[str] = None

    for idx, ev in enumerate(events):
        # 1. Vérification du hash du payload
        expected_payload_hash = compute_payload_sha256(ev.payload_json)
        if ev.payload_sha256 != expected_payload_hash:
            return AuditChainVerificationResponse(
                is_valid=False,
                organization_id=organization_id,
                total_events=len(events),
                tampered_event_id=str(ev.id),
                error_detail=f"Altération détectée du payload JSON à l'événement #{idx + 1} (hash attendu: {expected_payload_hash}, trouvé: {ev.payload_sha256})",
                verified_at=now,
            )

        # 2. Vérification du chaînage avec l'événement précédent
        if ev.previous_event_hash != previous_hash:
            return AuditChainVerificationResponse(
                is_valid=False,
                organization_id=organization_id,
                total_events=len(events),
                tampered_event_id=str(ev.id),
                error_detail=f"Rupture de chaîne détectée à l'événement #{idx + 1} (previous_hash attendu: {previous_hash}, trouvé: {ev.previous_event_hash})",
                verified_at=now,
            )

        # 3. Vérification de l'empreinte de signature de l'événement
        occurred_iso = normalize_iso_datetime(ev.occurred_at)
        expected_event_hash = compute_event_hash(
            organization_id=organization_id,
            entity_type=ev.entity_type,
            entity_id=ev.entity_id,
            action=ev.action,
            occurred_at_iso=occurred_iso,
            payload_sha256=ev.payload_sha256,
            previous_event_hash=ev.previous_event_hash,
        )
        if ev.event_hash != expected_event_hash:
            return AuditChainVerificationResponse(
                is_valid=False,
                organization_id=organization_id,
                total_events=len(events),
                tampered_event_id=str(ev.id),
                error_detail=f"Empreinte cryptographique invalide à l'événement #{idx + 1}",
                verified_at=now,
            )

        previous_hash = ev.event_hash

    return AuditChainVerificationResponse(
        is_valid=True,
        organization_id=organization_id,
        total_events=len(events),
        head_event_hash=events[-1].event_hash,
        genesis_event_hash=events[0].event_hash,
        first_event_at=events[0].occurred_at,
        last_event_at=events[-1].occurred_at,
        tampered_event_id=None,
        error_detail=None,
        verified_at=now,
    )


def generate_cryptographic_audit_certificate(
    db: Session,
    organization_id: UUID,
    organization_name: str,
) -> AuditIntegrityCertificateResponse:
    """Émet un certificat formel attestant de l'intégrité de la piste d'audit."""
    verification = verify_audit_chain(db, organization_id)
    now = datetime.now(timezone.utc)

    if not verification.is_valid:
        raise ValueError(f"Impossible d'émettre un certificat : intégrité compromise ({verification.error_detail})")

    head_hash = verification.head_event_hash or "GENESIS_EMPTY"
    merkle_digest = hashlib.sha256(
        f"{organization_id}:{verification.total_events}:{head_hash}:{now.isoformat()}".encode("utf-8")
    ).hexdigest()

    return AuditIntegrityCertificateResponse(
        organization_id=organization_id,
        organization_name=organization_name,
        certificate_id=f"CERT-AUDIT-{uuid4().hex[:12].upper()}",
        chain_length=verification.total_events,
        head_event_hash=head_hash,
        merkle_digest=merkle_digest,
        verification_status="SEALED_AND_VERIFIED",
        certified_at=now,
        issuer="VeriClaim Cryptographic Ledger Engine v1.0",
        legal_disclaimer="Ce certificat atteste de la non-altération mathématique des journaux d'audit enregistrés sous isolation PostgreSQL RLS.",
    )
