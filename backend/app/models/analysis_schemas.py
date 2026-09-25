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
    # This is intentionally an explicit not-applicable marker in C5: no
    # regulatory rules are evaluated by the persistent claim-detection flow.
    rulebook_version: str
    input_manifest_sha256: str
    result_sha256: str | None
    input_manifest: dict[str, Any] | None
    result: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ClaimCitationResponse(BaseModel):
    document_segment_id: UUID | None
    document_version_id: UUID | None
    document_id: UUID | None
    page_number: int | None
    segment_type: SegmentType | None
    segment_start_offset: int | None
    segment_end_offset: int | None
    source_sha256: str | None


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
    # Always null for C5 lexical detection; it is returned explicitly so a
    # consumer cannot mistake a detector hit for a statistical confidence.
    confidence_score: float | None
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
