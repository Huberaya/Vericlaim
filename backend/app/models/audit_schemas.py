from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class AuditEventResponse(BaseModel):
    id: UUID
    organization_id: UUID
    actor_user_id: Optional[UUID] = None
    entity_type: str
    entity_id: Optional[UUID] = None
    action: str
    occurred_at: datetime
    request_id: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    payload_sha256: str
    previous_event_hash: Optional[str] = None
    event_hash: str


class AuditChainVerificationResponse(BaseModel):
    is_valid: bool
    organization_id: UUID
    total_events: int
    head_event_hash: Optional[str] = None
    genesis_event_hash: Optional[str] = None
    first_event_at: Optional[datetime] = None
    last_event_at: Optional[datetime] = None
    tampered_event_id: Optional[str] = None
    error_detail: Optional[str] = None
    verified_at: datetime


class AuditIntegrityCertificateResponse(BaseModel):
    organization_id: UUID
    organization_name: str
    certificate_id: str
    chain_length: int
    head_event_hash: str
    merkle_digest: str
    verification_status: str
    certified_at: datetime
    issuer: str
    legal_disclaimer: str
