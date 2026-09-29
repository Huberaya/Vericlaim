"""Typed HTTP schemas for Human Review / Validations and Supplier Evidence Requests (Chantier 6.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.catalog_schemas import _normalise_optional, _normalise_required
from app.models.domain import EvidenceRequestStatus, ValidationDecision


class _ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationCreateRequest(_ReviewModel):
    analysis_version_id: UUID
    claim_id: UUID | None = None
    decision: ValidationDecision = ValidationDecision.VALIDATED
    comment: str | None = Field(default=None, max_length=5000)
    rationale: str | None = Field(default=None, max_length=5000)

    @field_validator("comment")
    @classmethod
    def normalise_comment(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="comment", maximum=5000)

    @field_validator("rationale")
    @classmethod
    def normalise_rationale(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="rationale", maximum=5000)


class ValidationResponse(_ReviewModel):
    id: UUID
    analysis_version_id: UUID
    claim_id: UUID | None
    decision: ValidationDecision
    reviewer_user_id: UUID | None
    reviewer_display_name: str | None
    comment: str | None
    rationale: str | None
    decided_at: datetime | None
    created_at: datetime


class EvidenceRequestCreateRequest(_ReviewModel):
    supplier_id: UUID
    product_id: UUID | None = None
    claim_id: UUID | None = None
    subject: str = Field(min_length=3, max_length=500)
    message: str = Field(min_length=5, max_length=10000)
    requested_items: list[dict[str, Any]] = Field(default_factory=list)
    due_at: datetime | None = None

    @field_validator("subject")
    @classmethod
    def normalise_subject(cls, value: str) -> str:
        return _normalise_required(value, label="subject", maximum=500, minimum=3)

    @field_validator("message")
    @classmethod
    def normalise_message(cls, value: str) -> str:
        return _normalise_required(value, label="message", maximum=10000, minimum=5)


class EvidenceRequestUpdateRequest(_ReviewModel):
    status: EvidenceRequestStatus | None = None
    subject: str | None = Field(default=None, max_length=500)
    message: str | None = Field(default=None, max_length=10000)
    requested_items: list[dict[str, Any]] | None = None
    due_at: datetime | None = None

    @field_validator("subject")
    @classmethod
    def normalise_subject(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="subject", maximum=500)

    @field_validator("message")
    @classmethod
    def normalise_message(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="message", maximum=10000)

    @model_validator(mode="after")
    def validate_non_empty(self) -> EvidenceRequestUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Au moins un champ de demande de preuve doit être fourni.")
        return self


class EvidenceRequestResponse(_ReviewModel):
    id: UUID
    supplier_id: UUID
    supplier_name: str | None
    product_id: UUID | None
    product_name: str | None
    claim_id: UUID | None
    claim_text: str | None
    status: EvidenceRequestStatus
    subject: str
    message: str
    requested_items: list[dict[str, Any]]
    due_at: datetime | None
    sent_at: datetime | None
    last_reminded_at: datetime | None
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class EvidenceRequestListResponse(_ReviewModel):
    items: list[EvidenceRequestResponse]
    next_cursor: str | None = None


class TemplateGenerationRequest(_ReviewModel):
    claim_id: UUID
    supplier_id: UUID | None = None
    product_id: UUID | None = None
    target_evidence_type: str | None = None


class TemplateGenerationResponse(_ReviewModel):
    subject: str
    message: str
    requested_items: list[dict[str, Any]]
    suggested_due_days: int
