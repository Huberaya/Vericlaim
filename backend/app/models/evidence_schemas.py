"""Typed HTTP schemas for the tenant-scoped persistent evidence registry and claim links."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.analysis_schemas import ClaimResponse
from app.models.catalog_schemas import _normalise_metadata, _normalise_optional, _normalise_required
from app.models.domain import EvidenceRelation, EvidenceStatus, EvidenceType


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceCreateRequest(_EvidenceModel):
    evidence_type: EvidenceType
    reference: str | None = Field(default=None, max_length=500)
    issuer: str | None = Field(default=None, max_length=255)
    issued_on: date | None = None
    expires_on: date | None = None
    product_scope: str | None = Field(default=None, max_length=5000)
    supplier_id: UUID | None = None
    product_id: UUID | None = None
    document_version_id: UUID | None = None
    certificate_id: UUID | None = None
    status: EvidenceStatus = EvidenceStatus.PENDING
    evidence_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reference")
    @classmethod
    def normalise_reference(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="reference", maximum=500)

    @field_validator("issuer")
    @classmethod
    def normalise_issuer(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="issuer", maximum=255)

    @field_validator("product_scope")
    @classmethod
    def normalise_product_scope(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="product_scope", maximum=5000)

    @field_validator("evidence_metadata")
    @classmethod
    def normalise_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _normalise_metadata(value)

    @model_validator(mode="after")
    def validate_date_range(self) -> EvidenceCreateRequest:
        if self.issued_on and self.expires_on and self.expires_on < self.issued_on:
            raise ValueError("La date d’expiration ne peut pas être antérieure à la date d’émission.")
        return self


class EvidenceUpdateRequest(_EvidenceModel):
    evidence_type: EvidenceType | None = None
    reference: str | None = Field(default=None, max_length=500)
    issuer: str | None = Field(default=None, max_length=255)
    issued_on: date | None = None
    expires_on: date | None = None
    product_scope: str | None = Field(default=None, max_length=5000)
    supplier_id: UUID | None = None
    product_id: UUID | None = None
    document_version_id: UUID | None = None
    certificate_id: UUID | None = None
    status: EvidenceStatus | None = None
    evidence_metadata: dict[str, Any] | None = None

    @field_validator("reference")
    @classmethod
    def normalise_reference(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="reference", maximum=500)

    @field_validator("issuer")
    @classmethod
    def normalise_issuer(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="issuer", maximum=255)

    @field_validator("product_scope")
    @classmethod
    def normalise_product_scope(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="product_scope", maximum=5000)

    @field_validator("evidence_metadata")
    @classmethod
    def normalise_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _normalise_metadata(value)

    @model_validator(mode="after")
    def validate_change_and_dates(self) -> EvidenceUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Au moins un champ de preuve doit être fourni.")
        if self.issued_on and self.expires_on and self.expires_on < self.issued_on:
            raise ValueError("La date d’expiration ne peut pas être antérieure à la date d’émission.")
        return self


class EvidenceResponse(_EvidenceModel):
    id: UUID
    evidence_type: EvidenceType
    status: EvidenceStatus
    reference: str | None
    issuer: str | None
    issued_on: date | None
    expires_on: date | None
    product_scope: str | None
    supplier_id: UUID | None
    product_id: UUID | None
    document_version_id: UUID | None
    certificate_id: UUID | None
    evidence_metadata: dict[str, Any]
    verified_at: datetime | None
    verified_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class EvidenceCreateResponse(_EvidenceModel):
    evidence: EvidenceResponse
    idempotent_replay: bool


class EvidenceListResponse(_EvidenceModel):
    items: list[EvidenceResponse]
    next_cursor: str | None = None


class EvidenceLinkCreateRequest(_EvidenceModel):
    claim_id: UUID | None = None
    evidence_id: UUID
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS
    #: C17 — l'état déclaré est **facultatif**, et son absence ne vaut pas « présent ».
    #
    # Défaut mesuré : ce champ valait `PRESENT` par défaut, si bien qu'un rattachement
    # envoyé sans état était enregistré comme utilisable, et que cette valeur écrasait le
    # constat du produit (« out_of_scope », « expired »). Le défaut le plus favorable
    # possible était donc appliqué en silence à toute pièce. Quand l'état est omis, c'est
    # l'examen de la pièce qui décide ; quand il est fourni, il est conservé comme
    # déclaration humaine et confronté au constat.
    coverage_status: EvidenceStatus | None = None
    validity_as_of: date | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=5000)

    @field_validator("rationale")
    @classmethod
    def normalise_rationale(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="rationale", maximum=5000)


class EvidenceLinkUpdateRequest(_EvidenceModel):
    relation: EvidenceRelation | None = None
    coverage_status: EvidenceStatus | None = None
    validity_as_of: date | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=5000)

    @field_validator("rationale")
    @classmethod
    def normalise_rationale(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="rationale", maximum=5000)

    @model_validator(mode="after")
    def validate_non_empty(self) -> EvidenceLinkUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Au moins un champ de mise à jour doit être fourni.")
        return self


class EvidenceLinkResponse(_EvidenceModel):
    id: UUID
    claim_id: UUID
    evidence_id: UUID
    relation: EvidenceRelation
    #: Ce qu'un relecteur a déclaré. `pending` = aucune décision humaine n'a été prise.
    coverage_status: EvidenceStatus
    #: C17 — ce que l'examen de la pièce constate (produit, fournisseur, famille
    #: d'allégation, dates), indépendamment de toute déclaration. Un lien `pending` peut
    #: donc porter un constat `out_of_scope` ou `expired` : les deux informations sont
    #: distinctes et le restent.
    observed_state: str | None = None
    validity_as_of: date | None
    confidence_score: float | None
    rationale: str | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    evidence: EvidenceResponse | None = None


class ClaimWithEvidenceLinksResponse(_EvidenceModel):
    claim: ClaimResponse
    evidence_links: list[EvidenceLinkResponse]
    coverage_status: EvidenceStatus
    is_sufficient: bool
    explanation: str


class EvidenceMatrixResponse(_EvidenceModel):
    analysis_id: UUID
    analysis_version_id: UUID
    version_number: int
    total_claims: int
    claims_with_evidence: int
    claims_missing_evidence: int
    claims_expired_evidence: int
    claims_out_of_scope_evidence: int
    matrix_rows: list[ClaimWithEvidenceLinksResponse]
    disclaimer: str


# ---------------------------------------------------------------------------
# C17 — État de couverture probatoire
# ---------------------------------------------------------------------------


class CoverageFindingResponse(_EvidenceModel):
    """Un fait constaté, avec les valeurs exactes qui l'ont produit."""

    code: str
    severity: str
    message: str
    evidence_id: UUID | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class EvidenceCheckResponse(_EvidenceModel):
    evidence_id: UUID
    evidence_type: EvidenceType
    validity_state: str
    scope_state: str
    type_state: str
    usable_as_of: bool
    findings: list[CoverageFindingResponse]


class EvidenceSuggestionResponse(_EvidenceModel):
    evidence_id: UUID
    evidence_type: EvidenceType
    reference: str | None
    score: int
    reasons: list[str]
    caveats: list[str]
    usable_as_of: bool


class ClaimCoverageResponse(_EvidenceModel):
    claim_id: UUID
    claim_type: str
    category: str
    claim_text: str
    as_of: date
    state: str
    state_meaning: str
    #: C17 — `None` signifie « aucune décision humaine déclarée sur ce rattachement ».
    #: Ce n'est pas un état de couverture : le produit ne le convertit pas en « partial ».
    declared_state: str | None
    observed_state: str
    is_sufficient: bool
    explanation: str
    checks: list[EvidenceCheckResponse]
    findings: list[CoverageFindingResponse]
    suggestions: list[EvidenceSuggestionResponse]
    contradicting_link_ids: list[UUID]
    linked_evidence_ids: list[UUID]


class CoverageSummaryResponse(_EvidenceModel):
    total_claims: int
    covered: int
    partial: int
    expired: int
    out_of_scope: int
    not_covering: int
    missing: int
    claims_with_declared_contradiction: int


class AnalysisCoverageResponse(_EvidenceModel):
    analysis_id: UUID
    analysis_version_id: UUID
    version_number: int
    as_of: date
    summary: CoverageSummaryResponse
    coverages: list[ClaimCoverageResponse]
    rules: dict[str, str]
    disclaimer: str
