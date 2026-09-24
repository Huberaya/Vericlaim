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
    supplier_name: str | None = None
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


class UrlAuditRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    context: AuditContext | None = None
    evidence: EvidenceDossier | None = None


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


class AuditHistoryItem(BaseModel):
    audit_id: str
    created_at_utc: datetime
    supplier_name: str | None = None
    product_identifier: str | None = None
    overall_compliance: OverallCompliance
    risk_score: int
    violations_count: int
    conditional_findings_count: int
    detected_claims_count: int
    record_hash: str
    max_fixed_fine_eur: Decimal | None = None
    source_snippet: str = ""


class AuditHistoryResponse(BaseModel):
    total: int
    items: list[AuditHistoryItem]


class SupplierSubmission(BaseModel):
    supplier_name: str
    product_identifier: str | None = None
    source_text: str
    context: AuditContext | None = None
    evidence: EvidenceDossier | None = None


class SupplierCompareRequest(BaseModel):
    audit_ids: list[str] = Field(default_factory=list)
    submissions: list[SupplierSubmission] = Field(default_factory=list)


class SupplierComparisonItem(BaseModel):
    supplier_name: str
    product_identifier: str | None = None
    audit_id: str | None = None
    overall_compliance: OverallCompliance
    risk_score: int
    rank: int
    recommendation: str
    recommendation_color: str
    violations_count: int
    violations_summary: list[str] = Field(default_factory=list)
    max_known_fine_eur: Decimal | None = None
    claims_detected: list[str] = Field(default_factory=list)
    procurement_clause: str
    has_lca_declared: bool = False
    has_ecolabel_declared: bool = False


class SupplierCompareResponse(BaseModel):
    evaluated_at: datetime
    suppliers_count: int
    ranked_suppliers: list[SupplierComparisonItem]
    best_supplier: str | None
    benchmark_summary: str
