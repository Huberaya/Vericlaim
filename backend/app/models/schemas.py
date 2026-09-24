from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.legal_types import (
    AuditTrail,
    EvidenceDossier,
    LegalAssessment,
    OverallCompliance,
    Surface,
    ExposureMatrix,
)


MAX_SOURCE_CHARS = 100_000


def paris_today() -> date:
    return datetime.now(ZoneInfo("Europe/Paris")).date()


class AuditContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date = Field(default_factory=paris_today)
    jurisdiction: str = Field(default="FR", min_length=2, max_length=8)
    surface: Surface = Surface.PACKAGING
    consumer_facing: bool = True
    product_identifier: str | None = None
    product_category: str | None = None
    operation_spend_eur: Decimal | None = Field(default=None, ge=0)

    @field_validator("jurisdiction")
    @classmethod
    def normalise_jurisdiction(cls, value: str) -> str:
        return value.strip().upper()


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(default="", max_length=MAX_SOURCE_CHARS)
    context: AuditContext = Field(default_factory=AuditContext)
    evidence: EvidenceDossier = Field(default_factory=EvidenceDossier)


class EvaluationResponse(BaseModel):
    extracted_source_text: str = ""
    overall_compliance: OverallCompliance
    risk_score: int = Field(ge=0, le=100)
    legal_exposure_estimate: str
    violations_count: int = Field(ge=0)
    conditional_findings_count: int = Field(ge=0)
    detected_claims_count: int = Field(ge=0)
    evaluations: list[LegalAssessment] = Field(default_factory=list)
    exposure_matrix: ExposureMatrix
    audit_trail: AuditTrail


class RuleSummary(BaseModel):
    rule_id: str
    title: str
    legal_reference: str
    legal_force: str
    severity: str
    effective_from: date | None = None
    scope: str
    source_urls: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RuleBookResponse(BaseModel):
    rulebook_version: str
    rules: list[RuleSummary]
