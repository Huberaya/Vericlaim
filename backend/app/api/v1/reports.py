from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.http import require_active_subscription
from app.core.config import settings
from app.core.database import get_db
from app.documents.storage import ObjectNotFoundError, ObjectStorageError
from app.identity.dependencies import TenantPrincipal, require_permission
from app.models.domain import Analysis, AnalysisVersion, Report, ReportJob, ReportStatus
from app.models.report_schemas import (
    PdfExportRequest,
    ReportJobRead,
    ReportQueuedResponse,
    ReportQueueRead,
    ReportVerificationResponse,
)
from app.reports import queue as report_queue
from app.reports.generation import report_artifact_filename
from app.reports.signing import (
    as_utc,
    build_signature_material,
    canonical_signature_timestamp,
    sign_material,
    signatures_match,
)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

# C13 — garde d'abonnement : cette route produit un artefact payant ou une
# dépense réelle (OCR, e-mail tiers, rapport signé). Voir app/billing/http.py.
SUBSCRIPTION_GATE = Depends(require_active_subscription())
# The verification endpoint is deliberately public: a third party checking a
# report has no VeriClaim account. It exposes only booleans and fingerprints, and
# is addressed by an unguessable handle rather than an internal identifier.
public_router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

DATABASE_DEPENDENCY = Depends(get_db)
AUDIT_READ_DEPENDENCY = Depends(require_permission("audit:read"))


def _product_label(analysis: Analysis) -> str:
    if analysis.product_id:
        return str(analysis.product_id)
    return "produit-non-specifie"


def _surface_label(version: AnalysisVersion) -> str:
    context = (version.result_json or {}).get("evaluation_context") or {}
    return str(context.get("surface") or "packaging")


def _deprecation_reasons(version: AnalysisVersion) -> list[str]:
    """Why a report should not be read as current.

    An analysis stays valid forever, but its verdict rests on a Rule Book. If the
    Rule Book or the engine has moved on, a reader comparing this report with a
    recent one would otherwise see two different answers and no explanation.
    """
    from app.analyses.service import ANALYSIS_ENGINE_VERSION
    from app.engine.rule_book import RULEBOOK_VERSION

    reasons: list[str] = []
    if version.rulebook_version != RULEBOOK_VERSION:
        reasons.append(
            f"Analysé sous l'empreinte de Rule Book {version.rulebook_version}; "
            f"la version courante est {RULEBOOK_VERSION}."
        )
    if version.engine_version != ANALYSIS_ENGINE_VERSION:
        reasons.append(
            f"Produit par le moteur {version.engine_version}; la version courante est "
            f"{ANALYSIS_ENGINE_VERSION}."
        )
    return reasons


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


def _prepare_signature(version: AnalysisVersion) -> tuple[str, str, str, datetime]:
    """Mint the handle and the signature before the document is rendered.

    The reference has to be printed on the report and covered by the signature,
    so it must exist first. The signature deliberately covers the analysis
    binding, not the bytes: the bytes are hashed afterwards and stored, because a
    hash cannot be part of the content it hashes.
    """
    reference = new_verification_reference()
    generated_at = datetime.now(timezone.utc)
    material = build_signature_material(
        analysis_version_id=str(version.id),
        result_sha256=version.result_sha256 or "",
        rulebook_version=version.rulebook_version,
        engine_version=version.engine_version,
        generated_at_utc=canonical_signature_timestamp(generated_at),
        verification_reference=reference,
    )
    signature = sign_material(material, signing_key=settings.report_signing_key)
    return reference, signature, key_id(settings.report_signing_key), generated_at


def _next_report_version(
    db: Session, *, organization_id: UUID, version_id: UUID, report_format: str
) -> int:
    previous = db.scalar(
        select(Report.version_number)
        .where(
            Report.organization_id == organization_id,
            Report.analysis_version_id == version_id,
            Report.report_format == report_format,
        )
        .order_by(Report.version_number.desc())
        .limit(1)
    )
    return (previous or 0) + 1


def _persist_signed_report(
    db: Session,
    *,
    version: AnalysisVersion,
    organization_id: UUID,
    requested_by_user_id: UUID | None,
    report_format: str,
    content: bytes,
    reference: str,
    signature: str,
    signing_key_id: str,
    generated_at: datetime,
) -> None:
    db.add(
        Report(
            id=uuid4(),
            organization_id=organization_id,
            analysis_version_id=version.id,
            version_number=_next_report_version(
                db,
                organization_id=organization_id,
                version_id=version.id,
                report_format=report_format,
            ),
            report_format=report_format,
            status=ReportStatus.READY,
            sha256=file_sha256(content),
            requested_by_user_id=requested_by_user_id,
            generated_at=generated_at,
            verification_reference=reference,
            signature=signature,
            signature_key_id=signing_key_id,
            signature_schema_version=SIGNATURE_SCHEMA_VERSION,
            signed_result_sha256=version.result_sha256,
            signed_rulebook_version=version.rulebook_version,
            signed_engine_version=version.engine_version,
        )
    )
    db.flush()


def _queue_read(db: Session, *, principal: TenantPrincipal) -> ReportQueueRead:
    snapshot = report_queue.queue_snapshot(
        db, organization_id=principal.organization_id, settings=settings
    )
    return ReportQueueRead(**snapshot.as_dict())


def _queued_response(
    db: Session, *, principal: TenantPrincipal, queued: report_queue.QueuedReport
) -> ReportQueuedResponse:
    return ReportQueuedResponse(
        report_id=str(queued.report_id) if queued.report_id else None,
        job_id=str(queued.job_id),
        report_format=queued.report_format,
        analysis_version_id=str(queued.analysis_version_id),
        report_version_number=queued.report_version_number,
        status="queued",
        reused=queued.reused,
        queued_at=queued.queued_at,
        job_url=f"/api/v1/reports/jobs/{queued.job_id}",
        download_url=(
            f"/api/v1/reports/{queued.report_id}/download" if queued.report_id else ""
        ),
        queue=_queue_read(db, principal=principal),
    )


def _enqueue_or_raise(
    db: Session,
    *,
    principal: TenantPrincipal,
    payload: PdfExportRequest,
    report_format: str,
) -> ReportQueuedResponse:
    """Met un rapport en file en traduisant chaque refus, et rien d'autre.

    Ce qui est refusé **ici** ne dépend pas du rendu : analyse ou version absente,
    version sans verdict exploitable, file saturée. Ce qui dépend du rendu est
    constaté par le worker et publié dans l'état du travail. Mélanger les deux
    ferait attendre un client pour une erreur déjà connue.
    """

    try:
        parsed_analysis_id = UUID(payload.analysis_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "analysis_not_found", "message": "Identifiant d'analyse invalide."},
        ) from exc
    try:
        queued = report_queue.enqueue_report(
            db,
            settings=settings,
            organization_id=principal.organization_id,
            analysis_id=parsed_analysis_id,
            report_format=report_format,
            options={
                "document_title": payload.document_title,
                "product_identifier": payload.product_identifier,
                "surface": payload.surface,
                "include_evidence_matrix": payload.include_evidence_matrix,
                "include_remediation_clauses": payload.include_remediation_clauses,
            },
            requested_by_user_id=principal.user_id,
        )
    except report_queue.ReportQueueSaturatedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.detail,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except report_queue.ReportNotRenderableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "report_not_renderable", "message": str(exc)},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "analysis_not_found", "message": str(exc)},
        ) from exc
    return _queued_response(db, principal=principal, queued=queued)


@router.post(
    "/pdf",
    response_model=ReportQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SUBSCRIPTION_GATE],
)
def export_pdf_report(
    payload: PdfExportRequest,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ReportQueuedResponse:
    """Met en file la génération du rapport PDF signé d'une analyse persistée.

    Le client ne fournit jamais de verdict : le rapport est rendu depuis les lignes
    ``analysis_verdicts`` de la version demandée, puis scellé par une signature HMAC
    et une référence de vérification publique. Le rendu lui-même n'a pas lieu dans
    cette requête (C22) : il est exécuté par ``app.workers.report_generation_worker``.
    """

    return _enqueue_or_raise(db, principal=principal, payload=payload, report_format="pdf")


@router.post(
    "/dossier",
    response_model=ReportQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SUBSCRIPTION_GATE],
)
def export_regulatory_dossier(
    payload: PdfExportRequest,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ReportQueuedResponse:
    """Met en file la génération de l'archive ZIP du dossier probatoire."""

    return _enqueue_or_raise(db, principal=principal, payload=payload, report_format="dossier_zip")


@router.get("/queue", response_model=ReportQueueRead)
def read_report_queue(
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ReportQueueRead:
    """Ce que la file de cette organisation contient, sans jargon interne."""

    return _queue_read(db, principal=principal)


@router.get("/jobs/{job_id}", response_model=ReportJobRead)
def read_report_job(
    job_id: str,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ReportJobRead:
    """État d'un travail de génération : ce que le client interroge après un 202."""

    try:
        parsed = UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "job_not_found", "message": "Identifiant de travail invalide."},
        )
    try:
        state = report_queue.report_job_state(
            db, organization_id=principal.organization_id, job_id=parsed
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "job_not_found", "message": str(exc)},
        ) from exc
    download_url = (
        f"/api/v1/reports/{state['report_id']}/download" if state["download_available"] else None
    )
    return ReportJobRead(
        job_id=str(state["job_id"]),
        report_id=str(state["report_id"]) if state["report_id"] else None,
        analysis_version_id=str(state["analysis_version_id"]),
        report_format=str(state["report_format"]),
        job_status=str(state["job_status"]),
        report_status=state["report_status"] if isinstance(state["report_status"], str) else None,
        attempt_count=int(state["attempt_count"]),
        max_attempts=int(state["max_attempts"]),
        available_at=state["available_at"],
        started_at=state["started_at"],
        completed_at=state["completed_at"],
        duration_ms=state["duration_ms"],
        error_code=state["error_code"],
        verification_reference=state["verification_reference"],
        sha256=state["sha256"],
        size_bytes=state["size_bytes"],
        content_type=state["content_type"],
        download_available=bool(state["download_available"]),
        download_url=download_url,
    )


def _load_ready_report(
    db: Session, *, organization_id: UUID, report_id: UUID
) -> Report:
    report = db.scalar(
        select(Report).where(
            Report.organization_id == organization_id,
            Report.id == report_id,
        )
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_not_found", "message": "Rapport introuvable pour cette organisation."},
        )
    return report


def _report_bytes_response(
    request: Request,
    *,
    db: Session,
    organization_id: UUID,
    report: Report,
) -> Response:
    """Renvoie le binaire stocké, avec les en-têtes probatoires du rapport."""

    if report.status != ReportStatus.READY or not report.storage_key:
        job = db.scalar(
            select(ReportJob)
            .where(
                ReportJob.organization_id == organization_id,
                ReportJob.report_id == report.id,
            )
            .order_by(ReportJob.created_at.desc())
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "report_not_ready",
                "report_status": report.status.value
                if hasattr(report.status, "value")
                else str(report.status),
                "job_url": f"/api/v1/reports/jobs/{job.id}" if job else None,
                "message": (
                    "Ce rapport n'est pas encore disponible. Interrogez job_url : la génération "
                    "est exécutée par un worker, pas par cette requête."
                ),
            },
        )
    storage = request.app.state.document_storage
    try:
        content = storage.read_bytes(
            bucket=settings.document_clean_bucket,
            key=report.storage_key,
            max_size_bytes=settings.report_download_max_bytes,
        )
    except ObjectNotFoundError as exc:
        # La ligne dit « prêt » et l'objet a disparu : le dire, plutôt que de
        # renvoyer un fichier vide qui aurait l'air d'un rapport valide.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "report_object_missing",
                "message": (
                    "Le rapport est enregistré comme prêt mais son binaire est absent du stockage. "
                    "Demandez une nouvelle génération : elle produira une nouvelle référence."
                ),
            },
        ) from exc
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "report_storage_unavailable",
                "message": f"Le stockage des rapports est indisponible : {exc}",
            },
        ) from exc

    version = db.scalar(
        select(AnalysisVersion).where(AnalysisVersion.id == report.analysis_version_id)
    )
    version_number = report.version_number
    filename = report_artifact_filename(
        version_number=version_number, report_format=report.report_format
    )
    is_dossier = report.report_format == "dossier_zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Report-Id": str(report.id),
        "X-Report-Type": (
            "regulatory-evidence-pack-zip" if is_dossier else "regulatory-pre-audit-pdf"
        ),
        "X-Analysis-Version": str(report.analysis_version_id),
        "X-Result-Sha256": (version.result_sha256 if version else "") or "",
        "X-Rulebook-Version": (version.rulebook_version if version else "") or "",
        "X-Verification-Reference": report.verification_reference or "",
        "X-Report-Signature": report.signature or "",
        "X-Report-Signature-Key-Id": report.signature_key_id or "",
        "X-Report-File-Sha256": report.sha256 or "",
    }
    return Response(
        content=content,
        media_type=report.content_type or "application/octet-stream",
        headers=headers,
    )


@router.get("/{report_id}/download", response_class=Response)
def download_report(
    report_id: str,
    request: Request,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Télécharge un rapport déjà généré, par son identifiant.

    Un rapport est téléchargeable **sans** abonnement en cours : un client en
    défaut de paiement doit pouvoir récupérer les pièces qu'il a déjà payées.
    """

    try:
        parsed = UUID(report_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_not_found", "message": "Identifiant de rapport invalide."},
        )
    report = _load_ready_report(db, organization_id=principal.organization_id, report_id=parsed)
    return _report_bytes_response(
        request, db=db, organization_id=principal.organization_id, report=report
    )


def _latest_ready_report(
    db: Session, *, organization_id: UUID, version_id: UUID, report_format: str
) -> Report | None:
    return db.scalar(
        select(Report)
        .where(
            Report.organization_id == organization_id,
            Report.analysis_version_id == version_id,
            Report.report_format == report_format,
            Report.status == ReportStatus.READY,
        )
        .order_by(Report.version_number.desc())
        .limit(1)
    )


@router.get("/analyses/{analysis_id}/pdf", response_class=Response)
def get_analysis_pdf(
    analysis_id: str,
    request: Request,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Télécharge le dernier rapport PDF **déjà généré** de cette analyse.

    Cette route ne génère rien : elle lit. Si aucun rapport prêt n'existe, elle
    répond 409 en indiquant la route qui met la génération en file — c'est la
    différence entre « pas encore produit » et « produit et indisponible ».
    """

    analysis, version = _get_analysis_or_404(db, analysis_id, principal.organization_id)
    report = _latest_ready_report(
        db,
        organization_id=principal.organization_id,
        version_id=version.id,
        report_format="pdf",
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "report_not_generated",
                "message": (
                    "Aucun rapport PDF prêt pour cette analyse. Demandez sa génération via "
                    "POST /api/v1/reports/pdf, puis interrogez le travail renvoyé."
                ),
            },
        )
    return _report_bytes_response(
        request, db=db, organization_id=principal.organization_id, report=report
    )


@router.get("/analyses/{analysis_id}/dossier", response_class=Response)
def get_analysis_dossier(
    analysis_id: str,
    request: Request,
    principal: TenantPrincipal = AUDIT_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    """Télécharge le dernier dossier probatoire **déjà généré** de cette analyse."""

    analysis, version = _get_analysis_or_404(db, analysis_id, principal.organization_id)
    report = _latest_ready_report(
        db,
        organization_id=principal.organization_id,
        version_id=version.id,
        report_format="dossier_zip",
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "report_not_generated",
                "message": (
                    "Aucun dossier probatoire prêt pour cette analyse. Demandez sa génération via "
                    "POST /api/v1/reports/dossier, puis interrogez le travail renvoyé."
                ),
            },
        )
    return _report_bytes_response(
        request, db=db, organization_id=principal.organization_id, report=report
    )


@public_router.get("/verify/{reference}", response_model=ReportVerificationResponse)
def verify_report(
    reference: str,
    sha256: str | None = Query(
        default=None,
        min_length=64,
        max_length=64,
        description="SHA-256 du fichier détenu par le vérificateur, pour confirmer qu'il est intact.",
    ),
    db: Session = DATABASE_DEPENDENCY,
) -> ReportVerificationResponse:
    """Public check that a report corresponds to an unmodified persisted analysis.

    Unauthenticated by design. It answers three separate questions, and refuses to
    blur them:

    1. is this a reference we issued, and is the signature valid?
    2. has the analysis behind it changed since the report was signed?
    3. if the caller supplied a file hash, does it match the issued file?

    A reference we did not issue returns ``known: false``, which must never be
    read as "valid".
    """
    report = db.scalar(
        select(Report).where(Report.verification_reference == reference)
    )
    if report is None or report.signature is None:
        return ReportVerificationResponse(
            known=False,
            reference=reference,
            signature_valid=False,
            analysis_unchanged=False,
            file_matches=None,
            reasons=[
                "Référence inconnue, ou rapport émis avant la mise en place de la signature. "
                "Aucune conclusion ne peut être tirée de cette réponse.",
            ],
        )

    version = db.scalar(
        select(AnalysisVersion).where(AnalysisVersion.id == report.analysis_version_id)
    )
    if version is None:
        return ReportVerificationResponse(
            known=True,
            reference=reference,
            signature_valid=False,
            analysis_unchanged=False,
            file_matches=None,
            reasons=["L'analyse signée n'existe plus; le rapport ne peut pas être rattaché."],
        )

    # The signature is recomputed from the stored analysis, not from data supplied
    # by the caller, so a forged reference cannot pass.
    material = build_signature_material(
        analysis_version_id=str(report.analysis_version_id),
        result_sha256=report.signed_result_sha256 or "",
        rulebook_version=report.signed_rulebook_version or "",
        engine_version=report.signed_engine_version or "",
        generated_at_utc=(
            canonical_signature_timestamp(report.generated_at) if report.generated_at else ""
        ),
        verification_reference=reference,
    )
    recomputed = sign_material(material, signing_key=settings.report_signing_key)
    signature_valid = signatures_match(report.signature, recomputed)

    analysis_unchanged = (
        bool(version.result_sha256)
        and version.result_sha256 == report.signed_result_sha256
        and version.engine_version == report.signed_engine_version
        and version.rulebook_version == report.signed_rulebook_version
    )

    file_matches: bool | None = None
    if sha256 is not None:
        file_matches = signatures_match(report.sha256, sha256.lower())

    reasons: list[str] = []
    if not signature_valid:
        reasons.append(
            "La signature ne correspond pas à cette référence. Le document n'a pas été émis "
            "par ce service, ou l'enregistrement a été modifié."
        )
    if not analysis_unchanged:
        reasons.append(
            "L'analyse a changé depuis l'émission du rapport. Le rapport reste une pièce "
            "historique valable, mais il ne décrit plus la version courante."
        )
    if file_matches is False:
        reasons.append(
            "L'empreinte fournie ne correspond pas au fichier émis. Le document a été modifié "
            "ou n'est pas celui délivré."
        )
    if not reasons:
        reasons.append(
            "Signature valide, analyse inchangée depuis l'émission. Cela atteste la correspondance "
            "entre le document et une analyse persistée, pas la véracité juridique des verdicts."
        )

    return ReportVerificationResponse(
        known=True,
        reference=reference,
        signature_valid=signature_valid,
        analysis_unchanged=analysis_unchanged,
        file_matches=file_matches,
        generated_at=as_utc(report.generated_at),
        report_format=report.report_format,
        rulebook_version=report.signed_rulebook_version,
        engine_version=report.signed_engine_version,
        result_sha256=report.signed_result_sha256,
        signature_key_id=report.signature_key_id,
        reasons=reasons,
    )
