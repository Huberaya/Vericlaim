"""Request/response contracts for the secure document-upload boundary."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.domain import (
    DocumentExtractionJobStatus,
    DocumentStatus,
    DocumentType,
    DocumentUploadStatus,
    ExtractionStatus,
    SegmentType,
)

_SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


class DocumentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=500)
    document_type: DocumentType = DocumentType.OTHER
    supplier_id: UUID | None = None
    product_id: UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Le titre du document doit contenir au moins deux caractères.")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            tag = value.strip().lower()
            if not tag or len(tag) > 80:
                raise ValueError("Chaque tag doit contenir entre 1 et 80 caractères.")
            if tag not in normalized:
                normalized.append(tag)
        return normalized


class DocumentVersionUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_filename: str = Field(min_length=1, max_length=500)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    expected_sha256: str | None = Field(default=None, max_length=64)

    @field_validator("expected_sha256")
    @classmethod
    def validate_expected_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("expected_sha256 doit être une empreinte SHA-256 hexadécimale.")
        return normalized


class DocumentResponse(BaseModel):
    id: UUID
    document_key: str
    title: str
    document_type: DocumentType
    status: DocumentStatus
    supplier_id: UUID | None
    product_id: UUID | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class DocumentExtractionJobResponse(BaseModel):
    status: DocumentExtractionJobStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None


class DocumentSegmentResponse(BaseModel):
    id: UUID
    sequence_number: int
    page_number: int | None
    segment_type: SegmentType
    text: str
    start_offset: int | None
    end_offset: int | None
    bounding_box: dict[str, object] | None
    source_sha256: str


class DocumentVersionResponse(BaseModel):
    id: UUID
    version_number: int
    source_filename: str
    content_type: str
    sha256: str
    size_bytes: int
    page_count: int | None
    extraction_status: ExtractionStatus
    extraction_error_code: str | None
    extraction_completed_at: datetime | None
    extraction_job: DocumentExtractionJobResponse | None = None
    created_at: datetime


class DocumentUploadResponse(BaseModel):
    id: UUID
    document_id: UUID
    status: DocumentUploadStatus
    source_filename: str
    declared_content_type: str
    declared_size_bytes: int
    expires_at: datetime
    uploaded_sha256: str | None
    scan_engine: str | None
    scan_completed_at: datetime | None
    scan_error_code: str | None
    finalized_document_version_id: UUID | None


class DocumentDetailResponse(BaseModel):
    document: DocumentResponse
    versions: list[DocumentVersionResponse]
    uploads: list[DocumentUploadResponse]


class DocumentVersionSegmentsResponse(BaseModel):
    version: DocumentVersionResponse
    segments: list[DocumentSegmentResponse]


class DocumentExtractionRetryResponse(BaseModel):
    version: DocumentVersionResponse
    extraction_job: DocumentExtractionJobResponse


class DocumentUploadInstructionResponse(BaseModel):
    upload: DocumentUploadResponse
    upload_url: str
    upload_fields: dict[str, str]
    upload_method: str = "POST"
    max_size_bytes: int


class DocumentUploadCompletionResponse(BaseModel):
    upload: DocumentUploadResponse
    document: DocumentResponse
    version: DocumentVersionResponse | None = None
    # A rejected/expired/unavailable completion still returns the tenant-safe
    # state envelope. ``detail`` is intentionally part of the contract so API
    # clients and OpenAPI do not need to parse an undocumented JSON field.
    detail: str | None = None


class DocumentDownloadResponse(BaseModel):
    url: str
    expires_at: datetime
