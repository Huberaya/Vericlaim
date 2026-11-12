"""Pydantic schemas and domain models for the B2B Pilot Pack (Chantier 8)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class PilotOverviewKPIs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_suppliers: int
    total_products: int
    total_documents: int
    total_analyses: int
    total_claims_detected: int
    claims_validated: int
    claims_contested: int
    claims_pending_review: int
    claims_with_evidence_links: int
    claims_missing_evidence: int
    claims_expired_evidence: int | None
    pending_evidence_requests: int
    overdue_evidence_requests: int
    human_validation_rate_percent: float | None
    critical_risk_claims_count: int | None


class SupplierRiskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    supplier_name: str
    country_code: str | None
    products_count: int
    claims_count: int
    missing_evidence_count: int
    pending_requests_count: int
    review_priority: str  # Heuristic based on missing links, not a legal risk verdict.


class PilotOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID
    organization_name: str
    kpis: PilotOverviewKPIs
    top_risk_suppliers: list[SupplierRiskSummary]
    rulebook_version: str
    generated_at: datetime
    disclaimer: str = (
        "Inventaire de pilotage : décisions humaines et liens probatoires déclarés. "
        "Un lien ne prouve pas la suffisance juridique ; aucun taux de conformité légale n'est calculé."
    )


class PreAuditFinding(BaseModel):
    """Detected claim inventory entry; deliberately contains no inferred verdict."""

    model_config = ConfigDict(extra="forbid")

    claim_id: UUID
    claim_text: str
    category: str
    claim_type: str
    severity: str
    legal_basis: str
    coverage_status: str
    validation_decision: str
    reviewer_comment: str | None = None
    remediation_advice: str


class PreAuditReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    organization_id: UUID
    organization_name: str
    generated_at: datetime
    as_of_date: date
    jurisdiction: str
    rulebook_version: str
    rulebook_sha256: str | None
    summary_kpis: PilotOverviewKPIs
    findings: list[PreAuditFinding]
    remediation_summary: list[str]
    content_sha256: str
    legal_disclaimer: str = (
        "INVENTAIRE INTERNE DES ALLÉGATIONS DÉTECTÉES ET DES DÉCISIONS HUMAINES. "
        "Aucune évaluation réglementaire n'est rejouée par cet export. L'empreinte SHA-256 porte sur le contenu, "
        "sans signature électronique ni horodatage qualifié. Ce document n'est ni un avis juridique, "
        "ni une certification, ni un constat de la DGCCRF."
    )


class CatalogImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_legal_name: str
    supplier_country: str | None = "FR"
    supplier_email: str | None = None
    product_reference: str | None = None
    product_name: str | None = None
    product_category: str | None = None


class CatalogImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CatalogImportItem] = Field(min_length=1, max_length=500)


class CatalogImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suppliers_created: int
    suppliers_reused: int
    products_created: int
    products_reused: int
    errors: list[str] = Field(default_factory=list)


class RetentionPolicyResponse(BaseModel):
    """No approved retention/privacy policy is persisted in this version.

    Unknown is not a promise of encryption, region, retention or DPO availability.
    A future policy must be configured, reviewed and audited before publication.
    """

    model_config = ConfigDict(extra="forbid")

    organization_id: UUID
    policy_status: str = "not_configured"
    documents_retention_years: int | None = None
    audit_trail_retention_years: int | None = None
    evidence_archive_retention_years: int | None = None
    gdpr_contact_email: str | None = None
    encryption_standard: str | None = None
    storage_region: str | None = None
    export_formats_supported: list[str] = Field(default_factory=lambda: ["JSON"])
    last_policy_review: str | None = None
    disclaimer: str = (
        "Politique non configurée et non vérifiée. Les durées, le chiffrement, la région et le contact RGPD "
        "ne sont pas attestés par cet écran. L'export disponible est un inventaire JSON, sans binaires."
    )
