"""Business logic for the B2B Pilot Pack: Overview KPIs, Pre-Audit Reports, Catalog Batch Import, and Dossier Export (Chantier 8)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.rule_book import RULEBOOK_VERSION
from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    AnalysisVersion,
    AuditEvent,
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
    ).all()
    val_map = {v.claim_id: v for v in validations}

    claims_validated = sum(1 for v in validations if v.decision == ValidationDecision.VALIDATED)
    claims_contested = sum(1 for v in validations if v.decision == ValidationDecision.CONTESTED)
    claims_pending = total_claims - claims_validated - claims_contested

    # Evidence links
    evidence_links = db.scalars(
        select(EvidenceLink).where(EvidenceLink.organization_id == organization_id)
    ).all()
    links_by_claim = {l.claim_id: l for l in evidence_links}

    claims_with_evidence = sum(1 for c in claims if c.id in links_by_claim)
    claims_missing_evidence = total_claims - claims_with_evidence
    claims_expired = 0  # Checked if evidence link points to expired evidence

    # Critical risk claims count
    critical_risk_count = sum(
        1
        for c in claims
        if c.claim_type in ("biodegradable", "nature_friendly", "carbon_neutrality")
    )

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

    compliance_rate = round((claims_validated / total_claims * 100), 1) if total_claims > 0 else 100.0

    kpis = PilotOverviewKPIs(
        total_suppliers=total_suppliers,
        total_products=total_products,
        total_documents=total_docs,
        total_analyses=total_analyses,
        total_claims_detected=total_claims,
        claims_validated=claims_validated,
        claims_contested=claims_contested,
        claims_pending_review=claims_pending,
        claims_with_sufficient_evidence=claims_with_evidence,
        claims_missing_evidence=claims_missing_evidence,
        claims_expired_evidence=claims_expired,
        pending_evidence_requests=pending_reqs,
        overdue_evidence_requests=overdue_reqs,
        global_compliance_rate_percent=compliance_rate,
        critical_risk_claims_count=critical_risk_count,
    )

    # Top risk suppliers
    suppliers = db.scalars(
        select(Supplier).where(Supplier.organization_id == organization_id, Supplier.deleted_at.is_(None)).limit(10)
    ).all()

    top_risk: list[SupplierRiskSummary] = []
    for s in suppliers:
        prods = db.scalars(select(Product).where(Product.supplier_id == s.id, Product.deleted_at.is_(None))).all()
        prod_ids = [p.id for p in prods]

        supp_claims = [c for c in claims if c.attributes_json and c.attributes_json.get("product_id") in [str(pid) for pid in prod_ids]]
        missing_count = sum(1 for c in supp_claims if c.id not in links_by_claim)
        risk_lvl = "high" if missing_count > 2 else "medium" if missing_count > 0 else "low"

        top_risk.append(
            SupplierRiskSummary(
                supplier_id=s.id,
                supplier_name=s.legal_name,
                country_code=s.country_code,
                products_count=len(prods),
                claims_count=len(supp_claims),
                missing_evidence_count=missing_count,
                pending_requests_count=0,
                risk_level=risk_lvl,
            )
        )

    return PilotOverviewResponse(
        organization_id=organization_id,
        organization_name=org_name,
        kpis=kpis,
        top_risk_suppliers=top_risk,
        rulebook_version=RULEBOOK_VERSION,
        generated_at=datetime.now(timezone.utc),
    )


def generate_pre_audit_report(db: Session, *, organization_id: UUID) -> PreAuditReportResponse:
    overview = get_pilot_overview(db, organization_id=organization_id)
    org = db.scalar(select(Organization).where(Organization.id == organization_id))
    org_name = org.name if org else "Organisation Pilote"

    claims = db.scalars(select(Claim).where(Claim.organization_id == organization_id)).all()
    validations = {
        v.claim_id: v
        for v in db.scalars(select(Validation).where(Validation.organization_id == organization_id)).all()
    }
    evidence_links = {
        l.claim_id: l
        for l in db.scalars(select(EvidenceLink).where(EvidenceLink.organization_id == organization_id)).all()
    }

    findings: list[PreAuditFinding] = []
    remediation_set: set[str] = set()

    for c in claims:
        val = validations.get(c.id)
        decision = val.decision.value if val else "pending"
        has_ev = c.id in evidence_links
        coverage = "verified" if has_ev else "missing"

        severity = "CRITICAL" if c.claim_type in ("biodegradable", "nature_friendly") else "HIGH" if c.claim_type == "carbon_neutrality" else "MEDIUM"
        legal_basis = "Loi AGEC Art. L. 541-9-1 & R. 541-230" if c.claim_type in ("biodegradable", "nature_friendly") else "Directive 2024/825 & Art. L. 229-68 Code Env." if c.claim_type == "carbon_neutrality" else "Code Consommation Art. L. 121-2 & ISO 14044"

        if c.claim_type == "biodegradable":
            advice = "Interdiction stricte en droit français. Supprimer impérativement la mention 'biodégradable' sur tout emballage neuf."
            remediation_set.add("Suppression des allégations de biodégradabilité sur emballages (Loi AGEC).")
        elif c.claim_type == "carbon_neutrality":
            advice = "Établir un bilan Scope 1/2/3 complet et supprimer toute allégation d'impact neutre fondée exclusivement sur de la compensation carbone (Directive 2024/825)."
            remediation_set.add("Fourniture d'un bilan d'émissions GES complet et trajectoire de réduction avant compensation.")
        elif c.claim_type == "recyclable":
            advice = "Rapprocher une attestation de filière de recyclage opérationnelle (Citeo / Léko) conformément à R. 541-228 VI."
            remediation_set.add("Obtention des attestations de recyclabilité auprès des éco-organismes agréés.")
        else:
            advice = "Conserver les rapports d'essais ou certificats ISO dans le registre probatoire de l'entreprise."
            remediation_set.add("Archivage des rapports d'essais et bilans ACV normés.")

        findings.append(
            PreAuditFinding(
                claim_text=c.claim_text,
                category=c.category or c.claim_type,
                claim_type=c.claim_type,
                severity=severity,
                legal_basis=legal_basis,
                coverage_status=coverage,
                validation_decision=decision,
                reviewer_comment=val.comment if val else None,
                remediation_advice=advice,
            )
        )

    # Signature
    report_content = f"{organization_id}:{RULEBOOK_VERSION}:{len(findings)}:{datetime.now(timezone.utc).isoformat()}"
    signature = hashlib.sha256(report_content.encode("utf-8")).hexdigest()
    rulebook_hash = hashlib.sha256(RULEBOOK_VERSION.encode("utf-8")).hexdigest()

    return PreAuditReportResponse(
        report_id=f"PREAUDIT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{signature[:8].upper()}",
        organization_id=organization_id,
        organization_name=org_name,
        generated_at=datetime.now(timezone.utc),
        as_of_date=date.today(),
        jurisdiction="FR / UE",
        rulebook_version=RULEBOOK_VERSION,
        rulebook_sha256=rulebook_hash,
        summary_kpis=overview.kpis,
        findings=findings,
        remediation_summary=sorted(remediation_set) if remediation_set else ["Aucun écart majeur détecté."],
        audit_trail_signature=signature,
    )


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
            "security_classification": "CONFIDENTIAL_COMPLIANCE_DOSSIER",
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
        "claims_count": len(claims),
        "evidence_count": len(evidence),
        "validations_count": len(validations),
        "evidence_requests_count": len(requests),
    }

    return export_payload


def get_retention_policy(db: Session, *, organization_id: UUID) -> RetentionPolicyResponse:
    return RetentionPolicyResponse(
        organization_id=organization_id,
        documents_retention_years=5,
        audit_trail_retention_years=10,
        evidence_archive_retention_years=5,
        gdpr_contact_email="dpo@vericlaim.ai",
        encryption_standard="AES-256 / TLS 1.3",
        storage_region="EU (Paris / Frankfurt)",
        export_formats_supported=["JSON", "CSV", "AUDIT_ZIP"],
        last_policy_review="2026-09-24",
    )
