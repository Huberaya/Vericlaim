"""Pydantic schemas and domain models for Regulatory Governance (Chantier 7)."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.models.legal_types import ClaimType, LegalForce, SanctionProfile, Severity


class Jurisdiction(str, Enum):
    FR = "FR"
    EU = "EU"
    INTERNATIONAL = "INTERNATIONAL"


class LegalStatus(str, Enum):
    IN_FORCE = "in_force"
    PENDING_TRANSPOSITION = "pending_transposition"
    PROPOSAL = "proposal"
    SUPERSEDED = "superseded"
    REPEALED = "repealed"


class ReviewStatus(str, Enum):
    APPROVED_LEGAL = "approved_legal"
    UNDER_REVIEW = "under_review"
    DRAFT = "draft"
    DEPRECATED = "deprecated"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class OfficialCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article: str
    source_title: str
    text_excerpt: str
    url: str
    effective_date: date | None = None


class SafeHarborSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe_harbor_id: str
    title: str
    evidence_kind: str
    conditions: list[str]


class RuleGovernanceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH
    legal_notes: str | None = None


class RegulatoryRuleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    title: str
    legal_reference: str
    jurisdiction: Jurisdiction
    legal_status: LegalStatus
    legal_force: LegalForce
    severity: Severity
    rule_kind: str
    claim_types: list[str]
    effective_from: date | None = None
    transposition_deadline: date | None = None
    confidence_level: ConfidenceLevel
    review_status: ReviewStatus
    has_safe_harbors: bool
    has_sanctions: bool
    incomplete_coverage_warning: str | None = None


class RegulatoryRuleDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    title: str
    legal_reference: str
    jurisdiction: Jurisdiction
    legal_status: LegalStatus
    legal_force: LegalForce
    severity: Severity
    rule_kind: str
    scope: str
    claim_types: list[str]
    official_citations: list[OfficialCitation]
    source_urls: list[str]
    required_evidence: list[str]
    safe_harbors: list[SafeHarborSchema]
    surfaces: list[str]
    effective_from: date | None = None
    effective_until: date | None = None
    transposition_deadline: date | None = None
    sanction: SanctionProfile | None = None
    priority: int
    notes: list[str]
    governance_review: RuleGovernanceReview
    incomplete_coverage_warning: str | None = None
    disclaimer: str = (
        "Le référentiel réglementaire constitue un outil d'aide à la décision et de pré-audit. "
        "Il ne se substitue pas à une analyse juridique personnalisée ni à un avis des autorités administratives."
    )


class RuleBookSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rulebook_version: str
    release_date: str
    sha256_fingerprint: str
    total_rules: int
    jurisdiction_breakdown: dict[str, int]
    legal_status_breakdown: dict[str, int]
    coverage_warnings_count: int
    governance_statement: str
    disclaimer: str


class RuleFieldDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    old_value: Any
    new_value: Any


class RuleDiffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    diff_type: str  # "added" | "modified" | "deprecated"
    title: str
    summary: str
    field_diffs: list[RuleFieldDiff] = Field(default_factory=list)


class RuleBookChangelogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    release_date: str
    title: str
    description: str
    diff_items: list[RuleDiffItem]


class RuleReviewSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus
    confidence_level: ConfidenceLevel
    legal_notes: str | None = None
