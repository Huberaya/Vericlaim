"""Report exports.

Interactive exports render the evaluation supplied by the caller. Persisted
analysis exports fail closed until a reviewed, tenant-scoped report builder can
reconstruct verdicts, evidence and audit material from durable rows. Never
re-evaluate placeholder text or present null hashes as a probative report.
"""
from __future__ import annotations

from datetime import datetime
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.identity.dependencies import TenantPrincipal, require_permission
from app.models.domain import Analysis, AnalysisVersion
from app.models.report_schemas import PdfExportRequest
from app.reports.dossier_exporter import create_regulatory_dossier_zip
from app.reports.pdf_generator import RegulatoryPdfReportGenerator

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

DATABASE_DEPENDENCY = Depends(get_db)
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))


def _get_analysis_or_404(
    db: Session,
    analysis_id: str,
    organization_id: UUID,
) -> tuple[Analysis, AnalysisVersion]:
    try:
        parsed_id = UUID(analysis_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identifiant d'analyse invalide.") from None

    analysis = db.scalar(
        select(Analysis).where(Analysis.id == parsed_id, Analysis.organization_id == organization_id)
    )
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analyse introuvable pour cette organisation.")

    latest_version = db.scalar(
        select(AnalysisVersion)
        .where(
            AnalysisVersion.analysis_id == analysis.id,
            AnalysisVersion.organization_id == organization_id,
        )
        .order_by(AnalysisVersion.version_number.desc())
    )
    if not latest_version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucune version d'analyse disponible.")
    return analysis, latest_version


def _persisted_report_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Export réglementaire persistant non disponible : cette analyse ne contient pas de verdicts "
            "réglementaires persistés et vérifiés. Utilisez l'export de l'évaluation interactive ou "
            "attendez le workflow de rapport persistant revu. Aucun rapport de substitution n'est généré."
        ),
    )


@router.post("/pdf", response_class=Response)
def export_pdf_report(
    payload: PdfExportRequest,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Render an interactive evaluation, without claiming a qualified signature."""
    eval_response = payload.evaluation_response
    if eval_response is None:
        if not payload.analysis_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Fournissez evaluation_response ou analysis_id pour générer le rapport.",
            )
        _get_analysis_or_404(db, payload.analysis_id, principal.organization_id)
        _persisted_report_unavailable()

    generator = RegulatoryPdfReportGenerator(
        report=eval_response,
        organization_name=principal.membership_view.organization.name,
        document_title=payload.document_title,
        product_identifier=payload.product_identifier,
        surface=payload.surface or "non précisé",
        include_evidence_matrix=payload.include_evidence_matrix,
        include_remediation_clauses=payload.include_remediation_clauses,
    )
    filename = f"rapport-pre-audit-{datetime.now().strftime('%Y%m%d-%H%M')}.pdf"
    return Response(
        content=generator.generate(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Report-Type": "regulatory-pre-audit-pdf",
            "Cache-Control": "no-store",
        },
    )


@router.post("/dossier", response_class=Response)
def export_regulatory_dossier(
    payload: PdfExportRequest,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Export an interactive evaluation and its declared, unverified evidence metadata."""
    eval_response = payload.evaluation_response
    if eval_response is None:
        if not payload.analysis_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Fournissez evaluation_response ou analysis_id pour générer le dossier.",
            )
        _get_analysis_or_404(db, payload.analysis_id, principal.organization_id)
        _persisted_report_unavailable()

    zip_bytes = create_regulatory_dossier_zip(
        report=eval_response,
        organization_name=principal.membership_view.organization.name,
        product_identifier=payload.product_identifier,
        surface=payload.surface or "non précisé",
    )
    filename = f"dossier-probatoire-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Dossier-Type": "regulatory-evidence-pack-zip",
            "Cache-Control": "no-store",
        },
    )


@router.get("/analyses/{analysis_id}/pdf", response_class=Response)
def get_analysis_pdf(
    analysis_id: str,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    _get_analysis_or_404(db, analysis_id, principal.organization_id)
    _persisted_report_unavailable()


@router.get("/analyses/{analysis_id}/dossier", response_class=Response)
def get_analysis_dossier(
    analysis_id: str,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    _get_analysis_or_404(db, analysis_id, principal.organization_id)
    _persisted_report_unavailable()
