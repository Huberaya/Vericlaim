"""Pydantic schemas and domain models for the B2B Pilot Pack (Chantier 8)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


DEFAULT_RETENTION_LIMITATIONS = [
    "Aucune valeur n'est fournie par défaut : un champ null signifie « non déclaré », "
    "jamais « 5 ans par usage ».",
    "La politique déclarée est appliquée par `POST /api/v1/privacy/purge` et par le job "
    "`python -m app.privacy.purge_job` ; ce dépôt ne fournit aucun ordonnanceur, donc sans "
    "cron ou timer système rien n'est supprimé sans intervention.",
    "La durée déclarée pour la piste d'audit n'est jamais appliquée : supprimer un événement "
    "romprait la chaîne vérifiable (`GET /api/v1/audit/verify`). `GET /api/v1/privacy/retention` "
    "publie la liste de ce qui n'est pas appliqué, avec la raison.",
    "La région d'hébergement n'est affichée que si elle a été déclarée explicitement : "
    "elle relève d'un choix d'infrastructure, pas du code.",
]


class RetentionPolicyResponse(BaseModel):
    """What the organization has declared about its own retention.

    Every value is nullable **on purpose**. An earlier version defaulted to
    5/10/5 years, ``dpo@vericlaim.ai``, ``AES-256 / TLS 1.3`` and
    ``EU (Paris / Frankfurt)`` for every tenant: values no client had agreed to,
    displayed as a compliance artefact and false for all of them. A missing value
    is now ``null``, and the response states which fields are missing so a client
    cannot mistake silence for a commitment.
    """

    model_config = ConfigDict(extra="forbid")

    organization_id: UUID
    configured: bool
    status: str
    documents_retention_years: int | None = None
    audit_trail_retention_years: int | None = None
    evidence_archive_retention_years: int | None = None
    gdpr_contact_email: str | None = None
    encryption_standard: str | None = None
    storage_region: str | None = None
    export_formats_supported: list[str] = Field(default_factory=list)
    last_policy_review: date | None = None
    declared_by_user_id: UUID | None = None
    declared_at: datetime | None = None
    undeclared_fields: list[str] = Field(default_factory=list)
    #: ``True`` seulement si quelque chose supprime **sans intervention humaine** dans le
    #: déploiement. Le code de purge existe (C20), aucun ordonnanceur n'est fourni.
    automatic_deletion_implemented: bool = False
    #: Le code sait appliquer la politique déclarée : la nuance compte, parce qu'un
    #: « non appliqué » et un « applicable mais non planifié » ne se traitent pas pareil.
    deletion_available: bool = True
    deletion_job: str = "python -m app.privacy.purge_job"
    enforcement_endpoint: str = "/api/v1/privacy/retention"
    disclaimer: str
    limitations: list[str] = Field(
        default_factory=lambda: DEFAULT_RETENTION_LIMITATIONS
    )


class RetentionPolicyUpdateRequest(BaseModel):
    """A declaration, accepted only from a member allowed to manage the organization."""

    model_config = ConfigDict(extra="forbid")

    documents_retention_years: int | None = Field(default=None, gt=0, le=100)
    audit_trail_retention_years: int | None = Field(default=None, gt=0, le=100)
    evidence_archive_retention_years: int | None = Field(default=None, gt=0, le=100)
    gdpr_contact_email: EmailStr | None = None
    encryption_standard: str | None = Field(default=None, max_length=64)
    storage_region: str | None = Field(default=None, max_length=128)
    export_formats_supported: list[str] | None = None
    last_policy_review: date | None = None
