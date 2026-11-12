"""Business logic for the B2B Pilot Pack: Overview KPIs, Pre-Audit Reports, Catalog Batch Import, and Dossier Export (Chantier 8)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.rule_book import RULEBOOK_VERSION
from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    AnalysisVersion,
    Claim,
    Document,
    Evidence,
    EvidenceLink,
    EvidenceRequest,
    EvidenceRequestStatus,
    Organization,
    Product,
    Supplier,
    Validation,
    ValidationDecision,
)
from app.models.pilot_schemas import (
    CatalogImportItem,
    CatalogImportResult,
    PilotOverviewKPIs,
    PilotOverviewResponse,
    PreAuditFinding,
    PreAuditReportResponse,
    RetentionPolicyResponse,
    SupplierRiskSummary,
)


def _log_audit(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    action: str,
    entity_type: str,
    entity_id: UUID | None,
    details: dict[str, Any],
    req_id: str | None = None,
) -> None:
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        payload=details,
        request_id=req_id,
    )


def get_pilot_overview(db: Session, *, organization_id: UUID) -> PilotOverviewResponse:
    org = db.scalar(select(Organization).where(Organization.id == organization_id))
    org_name = org.name if org else "Organisation Pilote"

    total_suppliers = db.scalar(
        select(func.count(Supplier.id)).where(
            Supplier.organization_id == organization_id, Supplier.deleted_at.is_(None)
        )
    ) or 0

    total_products = db.scalar(
        select(func.count(Product.id)).where(
            Product.organization_id == organization_id, Product.deleted_at.is_(None)
        )
    ) or 0

    total_docs = db.scalar(
        select(func.count(Document.id)).where(
            Document.organization_id == organization_id, Document.deleted_at.is_(None)
        )
    ) or 0

    total_analyses = db.scalar(
        select(func.count(Analysis.id)).where(
            Analysis.organization_id == organization_id, Analysis.deleted_at.is_(None)
        )
    ) or 0

    # Claims across all analysis versions of this tenant
    claims = db.scalars(
        select(Claim).where(Claim.organization_id == organization_id)
    ).all()
    total_claims = len(claims)

    # Validations
    validations = db.scalars(
        select(Validation).where(Validation.organization_id == organization_id)
        .order_by(Validation.created_at, Validation.id)
    ).all()
    # One latest recorded decision per claim, not the number of review events.
    decisions = {v.claim_id: v.decision for v in validations if v.claim_id is not None}
    claims_validated = sum(1 for c in claims if decisions.get(c.id) == ValidationDecision.VALIDATED)
    claims_contested = sum(1 for c in claims if decisions.get(c.id) == ValidationDecision.CONTESTED)
    claims_pending = total_claims - claims_validated - claims_contested

    # Evidence links
    evidence_links = db.scalars(
        select(EvidenceLink).where(EvidenceLink.organization_id == organization_id)
    ).all()
    links_by_claim = {link.claim_id: link for link in evidence_links}

    claims_with_evidence = sum(1 for c in claims if c.id in links_by_claim)
    claims_missing_evidence = total_claims - claims_with_evidence
    # Link existence is not evidence sufficiency. No freshness or regulatory
    # risk evaluation is performed by this inventory endpoint.
    claims_expired = None
    critical_risk_count = None

    # Evidence requests
    pending_reqs = db.scalar(
        select(func.count(EvidenceRequest.id)).where(
            EvidenceRequest.organization_id == organization_id,
            EvidenceRequest.deleted_at.is_(None),
            EvidenceRequest.status.in_([EvidenceRequestStatus.DRAFT, EvidenceRequestStatus.SENT]),
        )
    ) or 0

    overdue_reqs = db.scalar(
        select(func.count(EvidenceRequest.id)).where(
            EvidenceRequest.organization_id == organization_id,
            EvidenceRequest.deleted_at.is_(None),
            EvidenceRequest.status == EvidenceRequestStatus.OVERDUE,
        )
    ) or 0

    validation_rate = round((claims_validated / total_claims * 100), 1) if total_claims > 0 else None

    kpis = PilotOverviewKPIs(
        total_suppliers=total_suppliers,
        total_products=total_products,
        total_documents=total_docs,
        total_analyses=total_analyses,
        total_claims_detected=total_claims,
        claims_validated=claims_validated,
        claims_contested=claims_contested,
        claims_pending_review=claims_pending,
        claims_with_evidence_links=claims_with_evidence,
        claims_missing_evidence=claims_missing_evidence,
        claims_expired_evidence=claims_expired,
        pending_evidence_requests=pending_reqs,
        overdue_evidence_requests=overdue_reqs,
        human_validation_rate_percent=validation_rate,
        critical_risk_claims_count=critical_risk_count,
    )

    # Associate persisted claims via their analysis, never detector attributes.
    # Explicit analysis ownership takes precedence; a product owner is a fallback.
    claim_suppliers = db.execute(
        select(Claim.id, Analysis.supplier_id, Product.supplier_id)
        .join(AnalysisVersion, Claim.analysis_version_id == AnalysisVersion.id)
        .join(Analysis, AnalysisVersion.analysis_id == Analysis.id)
        .outerjoin(Product, (Product.id == Analysis.product_id) & (Product.organization_id == organization_id))
        .where(Claim.organization_id == organization_id, AnalysisVersion.organization_id == organization_id,
               Analysis.organization_id == organization_id, Analysis.deleted_at.is_(None))
    ).all()
    supplier_by_claim = {claim_id: supplier_id or product_supplier_id
                         for claim_id, supplier_id, product_supplier_id in claim_suppliers}
    suppliers = db.scalars(
        select(Supplier).where(Supplier.organization_id == organization_id, Supplier.deleted_at.is_(None))
    ).all()

    top_risk: list[SupplierRiskSummary] = []
    for s in suppliers:
        prods = db.scalars(select(Product).where(Product.organization_id == organization_id, Product.supplier_id == s.id, Product.deleted_at.is_(None))).all()
        supp_claims = [c for c in claims if supplier_by_claim.get(c.id) == s.id]
        missing_count = sum(1 for c in supp_claims if c.id not in links_by_claim)
        priority = "high" if missing_count > 2 else "medium" if missing_count > 0 else "low"
        pending_requests_count = db.scalar(
            select(func.count(EvidenceRequest.id)).where(
                EvidenceRequest.organization_id == organization_id,
                EvidenceRequest.supplier_id == s.id,
                EvidenceRequest.deleted_at.is_(None),
                EvidenceRequest.status.in_([
                    EvidenceRequestStatus.DRAFT,
                    EvidenceRequestStatus.SENT,
                    EvidenceRequestStatus.PARTIALLY_FULFILLED,
                    EvidenceRequestStatus.OVERDUE,
                ]),
            )
        ) or 0

        top_risk.append(
            SupplierRiskSummary(
                supplier_id=s.id,
                supplier_name=s.legal_name,
                country_code=s.country_code,
                products_count=len(prods),
                claims_count=len(supp_claims),
                missing_evidence_count=missing_count,
                pending_requests_count=pending_requests_count,
                review_priority=priority,
            )
        )

    top_risk.sort(key=lambda row: (-row.missing_evidence_count, row.supplier_name, str(row.supplier_id)))
    return PilotOverviewResponse(
        organization_id=organization_id,
        organization_name=org_name,
        kpis=kpis,
        top_risk_suppliers=top_risk[:10],
        rulebook_version="not-applicable-inventory-v1",
        generated_at=datetime.now(timezone.utc),
    )


def generate_pre_audit_report(db: Session, *, organization_id: UUID) -> PreAuditReportResponse:
    """Export detected claims and recorded human decisions, without inventing law.

    C5 persists detections, not reviewed regulatory verdicts. This inventory
    must not turn a claim category into a severity, a legal basis or a finding
    of compliance. The hash covers the complete JSON material (excluding the
    derived report id and the hash field itself); it is not a signature.
    """
    overview = get_pilot_overview(db, organization_id=organization_id)
    org = db.scalar(select(Organization).where(Organization.id == organization_id))
    generated_at = datetime.now(timezone.utc)
    claims = db.scalars(
        select(Claim).where(Claim.organization_id == organization_id).order_by(Claim.id)
    ).all()
    validations = {
        validation.claim_id: validation
        for validation in db.scalars(
            select(Validation)
            .where(Validation.organization_id == organization_id)
            .order_by(Validation.created_at, Validation.id)
        ).all()
    }
    links_by_claim: dict[UUID, list[EvidenceLink]] = {}
    for link in db.scalars(
        select(EvidenceLink).where(EvidenceLink.organization_id == organization_id).order_by(EvidenceLink.id)
    ).all():
        links_by_claim.setdefault(link.claim_id, []).append(link)

    findings: list[PreAuditFinding] = []
    for claim in claims:
        validation = validations.get(claim.id)
        links = links_by_claim.get(claim.id, [])
        coverage = "missing" if not links else "linked_unverified"
        findings.append(
            PreAuditFinding(
                claim_id=claim.id,
                claim_text=claim.claim_text,
                category=claim.category or claim.claim_type,
                claim_type=claim.claim_type,
                severity="not_evaluated",
                legal_basis="Non évalué : aucune conclusion réglementaire persistée et vérifiée disponible.",
                coverage_status=coverage,
                validation_decision=validation.decision.value if validation else "pending",
                reviewer_comment=validation.comment if validation else None,
                remediation_advice=(
                    "Faire examiner l'allégation, son contexte et les pièces rattachées par un réviseur habilité ; "
                    "un lien documentaire ne suffit pas à établir une conclusion juridique."
                ),
            )
        )

    response = PreAuditReportResponse(
        report_id="pending",
        organization_id=organization_id,
        organization_name=org.name if org else "Organisation",
        generated_at=generated_at,
        as_of_date=generated_at.date(),
        jurisdiction="non évaluée",
        rulebook_version="not-applicable-inventory-v1",
        rulebook_sha256=None,
        summary_kpis=overview.kpis,
        findings=findings,
        remediation_summary=(
            ["Planifier la revue humaine des allégations inventorées et compléter les pièces manquantes."]
            if findings
            else ["Aucune allégation dans cet inventaire ; ce constat ne démontre pas la conformité ni l'exhaustivité."]
        ),
        content_sha256="0" * 64,
    )
    material = response.model_dump(mode="json", exclude={"report_id", "content_sha256"})
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    response.content_sha256 = digest
    response.report_id = f"INVENTORY-{generated_at.strftime('%Y%m%d')}-{digest[:12].upper()}"
    return response


def import_pilot_catalog(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    items: list[CatalogImportItem],
    req_id: str | None = None,
) -> CatalogImportResult:
    suppliers_created = 0
    suppliers_reused = 0
    products_created = 0
    products_reused = 0
    errors: list[str] = []

    # Cache existing suppliers
    existing_suppliers = {
        s.legal_name.lower(): s
        for s in db.scalars(
            select(Supplier).where(Supplier.organization_id == organization_id, Supplier.deleted_at.is_(None))
        ).all()
    }

    # Cache existing products
    existing_products = {
        p.reference.lower(): p
        for p in db.scalars(
            select(Product).where(Product.organization_id == organization_id, Product.deleted_at.is_(None))
        ).all()
    }

    for idx, item in enumerate(items):
        try:
            supp_name_clean = item.supplier_legal_name.strip()
            if not supp_name_clean:
                errors.append(f"Ligne {idx + 1}: Raison sociale fournisseur manquante.")
                continue

            supp_key = supp_name_clean.lower()
            supplier = existing_suppliers.get(supp_key)

            if not supplier:
                supplier = Supplier(
                    id=uuid4(),
                    organization_id=organization_id,
                    legal_name=supp_name_clean,
                    country_code=item.supplier_country or "FR",
                    contact_email=item.supplier_email,
                    metadata_json={"source": "pilot_batch_import"},
                )
                db.add(supplier)
                existing_suppliers[supp_key] = supplier
                suppliers_created += 1
            else:
                suppliers_reused += 1

            if item.product_reference and item.product_name:
                prod_ref_clean = item.product_reference.strip()
                prod_name_clean = item.product_name.strip()
                prod_key = prod_ref_clean.lower()

                product = existing_products.get(prod_key)
                if not product:
                    product = Product(
                        id=uuid4(),
                        organization_id=organization_id,
                        supplier_id=supplier.id,
                        reference=prod_ref_clean,
                        name=prod_name_clean,
                        category=item.product_category or "general",
                        metadata_json={"source": "pilot_batch_import"},
                    )
                    db.add(product)
                    existing_products[prod_key] = product
                    products_created += 1
                else:
                    products_reused += 1

        except Exception as exc:
            errors.append(f"Ligne {idx + 1}: {str(exc)}")

    db.commit()

    _log_audit(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action="pilot.catalog_imported",
        entity_type="catalog",
        entity_id=organization_id,
        details={
            "suppliers_created": suppliers_created,
            "suppliers_reused": suppliers_reused,
            "products_created": products_created,
            "products_reused": products_reused,
            "errors_count": len(errors),
        },
        req_id=req_id,
    )
    db.commit()

    return CatalogImportResult(
        suppliers_created=suppliers_created,
        suppliers_reused=suppliers_reused,
        products_created=products_created,
        products_reused=products_reused,
        errors=errors,
    )


def get_pilot_dossier_export(db: Session, *, organization_id: UUID) -> dict[str, Any]:
    org = db.scalar(select(Organization).where(Organization.id == organization_id))
    suppliers = db.scalars(select(Supplier).where(Supplier.organization_id == organization_id)).all()
    products = db.scalars(select(Product).where(Product.organization_id == organization_id)).all()
    docs = db.scalars(select(Document).where(Document.organization_id == organization_id)).all()
    analyses = db.scalars(select(Analysis).where(Analysis.organization_id == organization_id)).all()
    claims = db.scalars(select(Claim).where(Claim.organization_id == organization_id)).all()
    evidence = db.scalars(select(Evidence).where(Evidence.organization_id == organization_id)).all()
    validations = db.scalars(select(Validation).where(Validation.organization_id == organization_id)).all()
    requests = db.scalars(select(EvidenceRequest).where(EvidenceRequest.organization_id == organization_id)).all()

    export_payload = {
        "export_metadata": {
            "organization_id": str(organization_id),
            "organization_name": org.name if org else "Pilot Org",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "rulebook_version": RULEBOOK_VERSION,
            "security_classification": "CONFIDENTIAL_COMPLIANCE_INVENTORY",
            "export_scope": "metadata_inventory_not_binary_archive",
        },
        "suppliers": [
            {"id": str(s.id), "legal_name": s.legal_name, "country": s.country_code, "archived": s.archived_at is not None}
            for s in suppliers
        ],
        "products": [
            {"id": str(p.id), "supplier_id": str(p.supplier_id), "reference": p.reference, "name": p.name}
            for p in products
        ],
        "documents": [
            {"id": str(d.id), "key": d.document_key, "title": d.title, "type": d.document_type.value if hasattr(d.document_type, "value") else str(d.document_type)}
            for d in docs
        ],
        "analyses_count": len(analyses),
        "claims_count": len(claims),
        "evidence_count": len(evidence),
        "validations_count": len(validations),
        "evidence_requests_count": len(requests),
    }

    return export_payload


def get_retention_policy(db: Session, *, organization_id: UUID) -> RetentionPolicyResponse:
    """Expose the absence of a reviewed policy instead of hardcoded promises."""
    del db  # No retention/privacy policy is persisted in this schema version.
    return RetentionPolicyResponse(organization_id=organization_id)
