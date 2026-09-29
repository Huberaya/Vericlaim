from __future__ import annotations

import io
from datetime import datetime, timezone, date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyses.service import get_analysis
from app.core.database import get_db
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.risk_assessment import (
    build_exposure_matrix,
    derive_overall_status,
    derive_risk_score,
)
from app.identity.dependencies import TenantPrincipal, require_permission
from app.models.domain import Analysis, AnalysisVersion, Organization
from app.models.legal_types import AuditTrail, EvidenceDossier, Verdict
from app.models.report_schemas import DossierExportResponse, PdfExportRequest
from app.models.schemas import AuditContext, EvaluationResponse
from app.reports.dossier_exporter import create_regulatory_dossier_zip
from app.reports.pdf_generator import RegulatoryPdfReportGenerator

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

DATABASE_DEPENDENCY = Depends(get_db)
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))


def _build_evaluation_from_text(
    text: str,
    context: Optional[AuditContext] = None,
    evidence: Optional[EvidenceDossier] = None,
) -> EvaluationResponse:
    evaluator = InferenceEvaluator()
    ctx = context or AuditContext()
    dossier = evidence or EvidenceDossier()
    claims, evaluations = evaluator.evaluate_text(text, dossier, ctx)
    overall = derive_overall_status(evaluations, len(claims))
    risk_score = derive_risk_score(evaluations)
    exposure, exposure_summary = build_exposure_matrix(evaluations, dossier, ctx)

    return EvaluationResponse(
        extracted_source_text=text,
        overall_compliance=overall,
        risk_score=risk_score,
        legal_exposure_estimate=exposure_summary,
        violations_count=sum(1 for i in evaluations if i.is_legal_violation),
        conditional_findings_count=sum(
            1 for i in evaluations if i.verdict != Verdict.COMPLIANT
        ),
        detected_claims_count=len(claims),
        evaluations=evaluations,
        exposure_matrix=exposure,
        audit_trail=AuditTrail(
            audit_id="aud-persisted-export",
            engine_version="1.0.0",
            rulebook_version="2026.09",
            evaluated_at_utc=datetime.now(timezone.utc),
            as_of_date=date.today(),
            source_sha256="0" * 64,
            evidence_manifest_sha256="0" * 64,
            report_sha256="0" * 64,
            record_hash="0" * 64,
        ),
    )


def _get_analysis_or_404(
    db: Session,
    analysis_id: str,
    organization_id: UUID,
) -> tuple[Analysis, AnalysisVersion]:
    try:
        parsed_id = UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Identifiant d'analyse invalide.",
        )

    analysis = db.scalar(
        select(Analysis).where(
            Analysis.id == parsed_id,
            Analysis.organization_id == organization_id,
        )
    )
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analyse introuvable pour cette organisation.",
        )

    latest_version = db.scalar(
        select(AnalysisVersion)
        .where(
            AnalysisVersion.analysis_id == analysis.id,
            AnalysisVersion.organization_id == organization_id,
        )
        .order_by(AnalysisVersion.version_number.desc())
    )
    if not latest_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune version d'analyse disponible.",
        )

    return analysis, latest_version


@router.post("/pdf", response_class=Response)
def export_pdf_report(
    payload: PdfExportRequest,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Génère et télécharge un rapport d'audit réglementaire au format PDF."""
    eval_response = payload.evaluation_response
    org_name = principal.membership_view.organization.name or "Organisation Abonnée"

    if eval_response is None:
        if not payload.analysis_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Fournissez evaluation_response ou analysis_id pour générer le rapport.",
            )
        analysis, version = _get_analysis_or_404(db, payload.analysis_id, principal.organization_id)
        # Reconstitution depuis le résultat stocké
        source_text = ""
        if version.result_json and isinstance(version.result_json, dict):
            source_text = version.result_json.get("extracted_source_text", "")
        if not source_text and version.input_manifest and isinstance(version.input_manifest, dict):
            source_text = version.input_manifest.get("source_text", "")
        eval_response = _build_evaluation_from_text(source_text or "Texte extrait de la pièce justificative.")

    generator = RegulatoryPdfReportGenerator(
        report=eval_response,
        organization_name=org_name,
        document_title=payload.document_title,
        product_identifier=payload.product_identifier,
        surface=payload.surface or "packaging",
        include_evidence_matrix=payload.include_evidence_matrix,
        include_remediation_clauses=payload.include_remediation_clauses,
    )
    pdf_bytes = generator.generate()

    filename = f"rapport-pre-audit-{datetime.now().strftime('%Y%m%d-%H%M')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Report-Type": "regulatory-pre-audit-pdf",
        },
    )


@router.post("/dossier", response_class=Response)
def export_regulatory_dossier(
    payload: PdfExportRequest,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Génère l'archive ZIP complète du dossier probatoire (PDF + JSON + CSV + README)."""
    eval_response = payload.evaluation_response
    org_name = principal.membership_view.organization.name or "Organisation Abonnée"

    if eval_response is None:
        if not payload.analysis_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Fournissez evaluation_response ou analysis_id pour générer le dossier.",
            )
        analysis, version = _get_analysis_or_404(db, payload.analysis_id, principal.organization_id)
        source_text = ""
        if version.result_json and isinstance(version.result_json, dict):
            source_text = version.result_json.get("extracted_source_text", "")
        if not source_text and version.input_manifest and isinstance(version.input_manifest, dict):
            source_text = version.input_manifest.get("source_text", "")
        eval_response = _build_evaluation_from_text(source_text or "Texte extrait de la pièce justificative.")

    zip_bytes = create_regulatory_dossier_zip(
        report=eval_response,
        organization_name=org_name,
        product_identifier=payload.product_identifier,
        surface=payload.surface or "packaging",
    )

    filename = f"dossier-probatoire-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Dossier-Type": "regulatory-evidence-pack-zip",
        },
    )


@router.get("/analyses/{analysis_id}/pdf", response_class=Response)
def get_analysis_pdf(
    analysis_id: str,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Télécharge directement le PDF d'une analyse persistante par son identifiant."""
    return export_pdf_report(
        payload=PdfExportRequest(analysis_id=analysis_id),
        principal=principal,
        db=db,
    )


@router.get("/analyses/{analysis_id}/dossier", response_class=Response)
def get_analysis_dossier(
    analysis_id: str,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Télécharge directement le pack ZIP probatoire d'une analyse persistante."""
    return export_regulatory_dossier(
        payload=PdfExportRequest(analysis_id=analysis_id),
        principal=principal,
        db=db,
    )
