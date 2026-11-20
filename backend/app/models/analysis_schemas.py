"""Request and response contracts for persistent deterministic analyses."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.domain import (
    AnalysisDetectionJobStatus,
    AnalysisStatus,
    ClaimSource,
    ClaimStatus,
    SegmentType,
)


class AnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_ids: list[UUID] = Field(min_length=1, max_length=50)
    supplier_id: UUID | None = None
    product_id: UUID | None = None
    analysis_key: str | None = Field(default=None, max_length=128)

    @field_validator("document_version_ids")
    @classmethod
    def unique_document_versions(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("Une version documentaire ne peut être présente qu’une fois.")
        return values

    @field_validator("analysis_key")
    @classmethod
    def normalize_analysis_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("analysis_key contient un caractère de contrôle.")
        return normalized


class AnalysisDetectionJobResponse(BaseModel):
    status: AnalysisDetectionJobStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None


class AnalysisResponse(BaseModel):
    id: UUID
    analysis_key: str
    status: AnalysisStatus
    supplier_id: UUID | None
    product_id: UUID | None
    requested_by_user_id: UUID | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AnalysisVersionResponse(BaseModel):
    id: UUID
    analysis_id: UUID
    version_number: int
    status: AnalysisStatus
    engine_version: str
    # Real Rule Book fingerprint for pipeline v2 versions. Versions produced
    # before v2 keep the explicit not-applicable marker.
    rulebook_version: str
    input_manifest_sha256: str
    result_sha256: str | None
    # NULL means no regulatory conclusion exists for this version. It is never a
    # synonym for "compliant".
    overall_compliance: str | None
    risk_score: int | None
    input_manifest: dict[str, Any] | None
    result: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ClaimTableCellResponse(BaseModel):
    """C23 — la cellule lue, quand l'allégation tombe dans un tableau.

    Ce n'est pas une conclusion : c'est la position et le rôle de la case où le
    déclencheur a été trouvé, la ligne lue (critère / valeur / unité) et l'entrée
    normalisée correspondante. Rien n'y est complété par déduction.
    """

    row_index: int | None = None
    column_index: int | None
    role: str | None
    text: str | None
    start_offset: int | None
    end_offset: int | None


class ClaimTableEntryResponse(BaseModel):
    criterion: str | None
    value: str | None
    numeric_value: str | None
    unit: str | None
    row_index: int | None


class ClaimTableCitationResponse(BaseModel):
    page_number: int | None
    columns: list[str]
    has_header: bool
    cell: ClaimTableCellResponse
    row_entry: ClaimTableEntryResponse | None


class ClaimCitationResponse(BaseModel):
    document_segment_id: UUID | None
    document_version_id: UUID | None
    document_id: UUID | None
    page_number: int | None
    segment_type: SegmentType | None
    segment_start_offset: int | None
    segment_end_offset: int | None
    source_sha256: str | None
    # C23 — présent seulement quand la citation tombe dans un tableau lu.
    table_citation: ClaimTableCitationResponse | None = None


class ClaimResponse(BaseModel):
    id: UUID
    analysis_version_id: UUID
    document_segment_id: UUID | None
    claim_type: str
    category: str
    claim_text: str
    normalized_text: str | None
    language: str | None
    start_offset: int | None
    end_offset: int | None
    source: ClaimSource
    # Deterministic rubric score (confidence-rubric-v1), never a statistical
    # confidence. Published with the factors that produced it, so a consumer
    # cannot mistake a detector hit for a probability.
    confidence_score: float | None
    confidence_level: str | None = None
    confidence_factors: list[dict[str, Any]] = []
    confidence_basis: str | None = None
    confidence_rubric_version: str | None = None
    review_reasons: list[str] = []
    status: ClaimStatus
    detector_version: str | None
    attributes: dict[str, Any]
    citation: ClaimCitationResponse
    created_at: datetime


class AnalysisDetailResponse(BaseModel):
    analysis: AnalysisResponse
    versions: list[AnalysisVersionResponse]
    latest_version: AnalysisVersionResponse | None
    disclaimer: str


class AnalysisVersionDetailResponse(BaseModel):
    analysis: AnalysisResponse
    version: AnalysisVersionResponse
    detection_job: AnalysisDetectionJobResponse | None
    claims: list[ClaimResponse]
    disclaimer: str


class AnalysisEnqueueResponse(BaseModel):
    analysis: AnalysisResponse
    version: AnalysisVersionResponse
    detection_job: AnalysisDetectionJobResponse
    idempotent_replay: bool
    disclaimer: str
