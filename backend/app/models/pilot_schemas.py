"""Pydantic schemas and domain models for the B2B Pilot Pack (Chantier 8)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
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
    claims_with_sufficient_evidence: int
    claims_missing_evidence: int
    claims_expired_evidence: int
    pending_evidence_requests: int
    overdue_evidence_requests: int
    global_compliance_rate_percent: float
    critical_risk_claims_count: int


class SupplierRiskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    supplier_name: str
    country_code: str | None
    products_count: int
    claims_count: int
    missing_evidence_count: int
    pending_requests_count: int
    risk_level: str  # "high" | "medium" | "low"


class PilotOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID
    organization_name: str
    kpis: PilotOverviewKPIs
    top_risk_suppliers: list[SupplierRiskSummary]
    rulebook_version: str
    generated_at: datetime
    disclaimer: str = (
        "Tableau de bord de pilotage du risque de conformité environnementale (B2B SaaS). "
        "Outil interne d'aide à la décision sans valeur de certification officielle."
    )


class PreAuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    rulebook_sha256: str
    summary_kpis: PilotOverviewKPIs
    findings: list[PreAuditFinding]
    remediation_summary: list[str]
    audit_trail_signature: str
    legal_disclaimer: str = (
        "DOCUMENT INTERNE DE GESTION DU RISQUE — PRÉ-AUDIT CONFORMITÉ ALLÉGATIONS ENVIRONNEMENTALES. "
        "Ce rapport est un outil interne de remédiation et de gouvernance. "
        "Il ne constitue ni un avis juridique opposable, ni une certification officielle, ni un constat de la DGCCRF."
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
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID
    documents_retention_years: int = 5
    audit_trail_retention_years: int = 10
    evidence_archive_retention_years: int = 5
    gdpr_contact_email: str
    encryption_standard: str = "AES-256 / TLS 1.3"
    storage_region: str = "EU (Paris / Frankfurt)"
    export_formats_supported: list[str] = Field(default_factory=lambda: ["JSON", "CSV", "AUDIT_ZIP"])
    last_policy_review: str = "2026-09-24"
