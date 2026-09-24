from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class ClaimType(str, Enum):
    BIODEGRADABLE = "biodegradable"
    NATURE_FRIENDLY = "nature_friendly"
    GENERIC_ENVIRONMENTAL = "generic_environmental"
    CARBON_NEUTRALITY = "carbon_neutrality"
    COMPARATIVE = "comparative"
    QUANTIFIED_CLIMATE = "quantified_climate"
    RECYCLABLE = "recyclable"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Verdict(str, Enum):
    STRICTLY_PROHIBITED = "STRICTLY_PROHIBITED"
    NON_COMPLIANT = "NON_COMPLIANT"
    CONDITIONAL_REJECT = "CONDITIONAL_REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLIANT = "COMPLIANT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UPCOMING = "UPCOMING"
    ADVISORY_ONLY = "ADVISORY_ONLY"


class OverallCompliance(str, Enum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    CONDITIONAL_REJECT = "CONDITIONAL_REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UPCOMING_REQUIREMENTS = "UPCOMING_REQUIREMENTS"
    NO_CLAIMS_DETECTED = "NO_CLAIMS_DETECTED"


class LegalForce(str, Enum):
    BINDING_FR = "BINDING_FR"
    EU_DIRECTIVE_DATE_GATED = "EU_DIRECTIVE_DATE_GATED"
    PROPOSAL_ONLY = "PROPOSAL_ONLY"
    VOLUNTARY_STANDARD = "VOLUNTARY_STANDARD"
    INTERNAL_EVIDENCE_CONTROL = "INTERNAL_EVIDENCE_CONTROL"


class Surface(str, Enum):
    PRODUCT_LABEL = "product_label"
    PACKAGING = "packaging"
    ADVERTISEMENT = "advertisement"
    ONLINE_STORE = "online_store"
    SUPPLIER_CONTRACT = "supplier_contract"
    UNKNOWN = "unknown"


class EvidenceStatus(str, Enum):
    NOT_PROVIDED = "NOT_PROVIDED"
    INCOMPLETE = "INCOMPLETE"
    METADATA_COMPLETE = "METADATA_COMPLETE"
    VERIFIED = "VERIFIED"
    NOT_INDEPENDENTLY_VERIFIED = "NOT_INDEPENDENTLY_VERIFIED"
    INVALID = "INVALID"


class ExposureCategory(str, Enum):
    ADMINISTRATIVE = "ADMINISTRATIVE"
    PENAL = "PENAL"
    CIVIL = "CIVIL"
    COMMERCIAL = "COMMERCIAL"


class DetectedClaim(BaseModel):
    """A deterministic lexical fact, not a probabilistic model output."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_text: str
    trigger_text: str
    claim_type: ClaimType
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    trigger_start_offset: int = Field(ge=0)
    trigger_end_offset: int = Field(ge=0)
    affirmative: bool = True
    negation_cue: str | None = None
    has_offsetting_signal: bool = False
    has_specific_qualifier: bool = False
    numeric_value: Decimal | None = None
    numeric_unit: str | None = None
    detection_method: str = "DETERMINISTIC_LEXICON"


class EvidenceBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str | None = None
    reference: str | None = None
    file_name: str | None = None
    file_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    product_scope: str | None = None
    claim_excerpt: str | None = None
    issued_on: date | None = None
    expires_on: date | None = None


class LcaEvidence(EvidenceBase):
    kind: Literal["lca_report"] = "lca_report"
    standard: str | None = None
    functional_unit: str | None = None
    system_boundary: str | None = None
    impact_categories: list[str] = Field(default_factory=list)
    comparative: bool = False
    comparison_product: str | None = None
    same_functional_unit: bool = False
    same_system_boundary: bool = False


class EcolabelEvidence(EvidenceBase):
    kind: Literal["ecolabel_certificate"] = "ecolabel_certificate"
    scheme: Literal["EU_ECOLABEL", "EN_ISO_14024_TYPE_I", "OTHER"]
    license_number: str
    product_identifier: str | None = None
    product_category: str | None = None
    issuer: str | None = None


class RecyclingRouteEvidence(EvidenceBase):
    kind: Literal["recycling_route"] = "recycling_route"
    material_or_component: str | None = None
    territories: list[str] = Field(default_factory=list)
    collection_available: bool = False
    sorting_available: bool = False
    consumer_access: bool = False
    industrial_processing_available: bool = False
    coverage_percent: Decimal | None = Field(default=None, ge=0, le=100)


class GHGInventoryEvidence(EvidenceBase):
    kind: Literal["ghg_inventory"] = "ghg_inventory"
    standard: str | None = None
    includes_direct_emissions: bool = False
    includes_indirect_emissions: bool = False
    product_lifecycle_scope: str | None = None
    public_disclosure_url: str | None = None


class GHGReductionPlanEvidence(EvidenceBase):
    kind: Literal["ghg_reduction_plan"] = "ghg_reduction_plan"
    avoidance_prioritised: bool = False
    reduction_before_compensation: bool = False
    annual_quantified_targets: bool = False
    public_disclosure_url: str | None = None


class CarbonOffsetEvidence(EvidenceBase):
    kind: Literal["carbon_offset"] = "carbon_offset"
    standard_or_registry: str | None = None
    retirement_reference: str | None = None
    vintage_year: int | None = Field(default=None, ge=1990, le=2200)
    quantity_tco2e: Decimal | None = Field(default=None, ge=0)
    residual_emissions_reference: str | None = None


class OtherEvidence(EvidenceBase):
    kind: Literal["other"] = "other"
    description: str | None = None


EvidenceItem = Annotated[
    Union[
        LcaEvidence,
        EcolabelEvidence,
        RecyclingRouteEvidence,
        GHGInventoryEvidence,
        GHGReductionPlanEvidence,
        CarbonOffsetEvidence,
        OtherEvidence,
    ],
    Field(discriminator="kind"),
]


class EvidenceDossier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EvidenceItem] = Field(default_factory=list)
    legal_person: bool = True
    average_annual_turnover_eur: Decimal | None = Field(default=None, ge=0)
    advertising_spend_eur: Decimal | None = Field(default=None, ge=0)


class EvidenceCheck(BaseModel):
    check_name: str
    status: EvidenceStatus
    evidence_ids: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    detail: str
    independently_verified: bool = False


class ReasoningStep(BaseModel):
    step: int = Field(ge=1)
    code: str
    finding: str
    legal_significance: str


class Remediation(BaseModel):
    buyer_explanation: str
    recommended_rewrite: str
    supplier_contract_clause: str
    required_actions: list[str] = Field(default_factory=list)


class SanctionProfile(BaseModel):
    mechanism: str
    authority: str
    legal_basis: str
    max_natural_person_eur: Decimal | None = None
    max_legal_person_eur: Decimal | None = None
    may_scale_to_advertising_spend: bool = False
    amount_is_automatic: bool = False
    notes: str


class ExposureItem(BaseModel):
    category: ExposureCategory
    title: str
    legal_basis: str | None = None
    max_natural_person_eur: Decimal | None = None
    max_legal_person_eur: Decimal | None = None
    calculated_amount_eur: Decimal | None = None
    may_scale_to_advertising_spend: bool = False
    conditional: bool = True
    note: str


class ExposureMatrix(BaseModel):
    items: list[ExposureItem] = Field(default_factory=list)
    max_known_fixed_fine_eur: Decimal | None = None
    max_known_fixed_fine_for: str = "legal_person"
    amount_is_cumulative: bool = False
    civil_risk: list[str] = Field(default_factory=list)
    calculation_notes: list[str] = Field(default_factory=list)


class LegalAssessment(BaseModel):
    """One auditable decision for one detected claim and one formal rule."""

    claim_id: str
    claim_text: str
    claim_type: ClaimType
    start_offset: int
    end_offset: int
    trigger_text: str
    rule_id: str
    rule_title: str
    law_reference: str
    source_urls: list[str] = Field(default_factory=list)
    legal_force: LegalForce
    severity: Severity
    verdict: Verdict
    is_legal_violation: bool = False
    safe_harbor_applicable: bool = False
    safe_harbor_reason: str | None = None
    required_evidence: list[str] = Field(default_factory=list)
    evidence_checks: list[EvidenceCheck] = Field(default_factory=list)
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
    remediation: Remediation
    sanction: SanctionProfile | None = None
    legal_caveat: str | None = None


class AuditTrail(BaseModel):
    audit_id: str
    engine_version: str
    rulebook_version: str
    evaluated_at_utc: datetime
    as_of_date: date
    source_sha256: str
    document_sha256: str | None = None
    evidence_manifest_sha256: str
    report_sha256: str
    previous_record_hash: str | None = None
    record_hash: str
    extraction_method: str = "INLINE_TEXT"
    limitations: list[str] = Field(default_factory=list)
