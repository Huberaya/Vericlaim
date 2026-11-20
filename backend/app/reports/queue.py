"""C22 — file de génération des rapports : réclamation, bail, reprise, contre-pression.

Le rendu d'un PDF ou d'un dossier ZIP se faisait dans la requête HTTP. Trois
conséquences, toutes constatées :

* un client attendait le rendu, et un rendu long était un timeout, pas un travail ;
* un redémarrage au mauvais moment perdait le travail sans trace exploitable ;
* dimensionner le service revenait à allonger un délai, pas à ajouter des workers.

La file vit dans PostgreSQL, comme celles de l'extraction documentaire et de la
détection : la mise en file est transactionnelle avec la ligne ``reports``, la
réclamation utilise ``FOR UPDATE SKIP LOCKED`` (donc N workers ne se marchent pas
dessus), et chaque travail porte un bail. Aucun courtier externe n'est requis.

Trois propriétés sont ici, et chacune est vérifiée par un test :

* **contre-pression** — au-delà d'un nombre de travaux en attente par organisation,
  la demande est refusée en nommant la limite au lieu d'empiler indéfiniment des
  travaux qu'aucun worker ne rattrapera ;
* **équité entre locataires** — un plafond de travaux *en cours* par organisation
  empêche qu'un client qui lance trois cents rapports occupe tous les workers ;
* **reprise** — un bail expiré est récupéré : le travail repart, et le worker
  précédent ne peut plus écrire son résultat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    AnalysisVersion,
    Report,
    ReportJob,
    ReportJobStatus,
    ReportStatus,
)

#: Types d'artefacts acceptés par la file. Un ajout ici doit être accompagné d'un
#: générateur et d'un type de contenu, sinon la file produirait un fichier sans nom.
SUPPORTED_REPORT_FORMATS = ("pdf", "dossier_zip")

REPORT_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "dossier_zip": "application/zip",
}

REPORT_FILE_EXTENSIONS = {
    "pdf": "pdf",
    "dossier_zip": "zip",
}


class ReportQueueError(RuntimeError):
    """Erreur de file sûre à exposer au client."""


class ReportNotRenderableError(ReportQueueError):
    """La version demandée ne peut pas être rendue honnêtement en rapport."""


class ReportQueueSaturatedError(ReportQueueError):
    """Contre-pression : la file de cette organisation est pleine."""

    def __init__(self, *, pending: int, limit: int, oldest_pending_seconds: int | None) -> None:
        super().__init__(
            f"File de génération saturée pour cette organisation : {pending} travaux en attente "
            f"pour une limite de {limit}."
        )
        self.pending = pending
        self.limit = limit
        self.oldest_pending_seconds = oldest_pending_seconds

    @property
    def retry_after_seconds(self) -> int:
        # Le client doit pouvoir réessayer sans deviner : on lui donne la même
        # information que la réponse HTTP.
        return max(30, min(self.limit * 5, 600))

    @property
    def detail(self) -> dict[str, object]:
        return {
            "code": "report_queue_saturated",
            "pending": self.pending,
            "limit": self.limit,
            "oldest_pending_seconds": self.oldest_pending_seconds,
            "message": (
                f"{self.pending} rapports de cette organisation sont déjà en attente ou en cours "
                f"(limite : {self.limit}). La demande n'a pas été enregistrée : réessayez dans "
                f"{self.retry_after_seconds} secondes, ou attendez que la file se vide."
            ),
            "retry_after_seconds": self.retry_after_seconds,
            "queue_path": "/api/v1/reports/queue",
        }


class ReportJobConflictError(ReportQueueError):
    """Le travail n'appartient plus au worker qui tente d'écrire son résultat."""


@dataclass(frozen=True)
class QueuedReport:
    #: ``None`` seulement si un travail existant perdait sa ligne de rapport — cas
    #: que le code signale au lieu de fabriquer un identifiant.
    report_id: UUID | None
    job_id: UUID
    analysis_version_id: UUID
    report_format: str
    report_version_number: int
    queued_at: datetime
    #: ``True`` quand un travail équivalent existait déjà : la demande n'a rien
    #: créé de plus, et le client interroge le même travail.
    reused: bool


@dataclass(frozen=True)
class ClaimedReportJob:
    id: UUID
    organization_id: UUID
    analysis_version_id: UUID
    report_id: UUID | None
    report_format: str
    options: dict[str, object]
    worker_id: str
    attempt_count: int
    requested_by_user_id: UUID | None


@dataclass(frozen=True)
class GenerateReportResult:
    report_id: UUID
    job_id: UUID
    storage_key: str
    sha256: str
    size_bytes: int
    verification_reference: str
    signature: str
    signature_key_id: str
    duration_ms: int


@dataclass(frozen=True)
class QueueSnapshot:
    pending: int
    running: int
    oldest_pending_seconds: int | None
    pending_limit: int
    running_limit: int

    def as_dict(self) -> dict[str, object]:
        return {
            "pending": self.pending,
            "running": self.running,
            "oldest_pending_seconds": self.oldest_pending_seconds,
            "pending_limit": self.pending_limit,
            "running_limit": self.running_limit,
            "saturated": self.pending >= self.pending_limit,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_tenant_context(db: Session, organization_id: UUID) -> None:
    """La file est une table de locataire : le contexte doit être posé avant d'y toucher.

    Sous PostgreSQL, RLS refuserait de toute façon ; le contrôle est explicite pour
    que l'erreur soit lisible côté worker plutôt qu'une file qui paraît vide.
    """

    if db.info.get("current_organization_id") != organization_id:
        raise ReportJobConflictError(
            "Le contexte organisationnel doit être défini avant de toucher la file de rapports."
        )


def pending_job_count(db: Session, *, organization_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count(ReportJob.id)).where(
                ReportJob.organization_id == organization_id,
                ReportJob.status.in_((ReportJobStatus.QUEUED, ReportJobStatus.RUNNING)),
            )
        )
        or 0
    )


def running_job_count(db: Session, *, organization_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count(ReportJob.id)).where(
                ReportJob.organization_id == organization_id,
                ReportJob.status == ReportJobStatus.RUNNING,
            )
        )
        or 0
    )


def queue_snapshot(db: Session, *, organization_id: UUID, settings: Settings) -> QueueSnapshot:
    oldest = db.scalar(
        select(func.min(ReportJob.created_at)).where(
            ReportJob.organization_id == organization_id,
            ReportJob.status == ReportJobStatus.QUEUED,
        )
    )
    oldest_seconds = None
    if oldest is not None:
        oldest_seconds = max(int((utcnow() - _as_utc(oldest)).total_seconds()), 0)
    return QueueSnapshot(
        pending=pending_job_count(db, organization_id=organization_id),
        running=running_job_count(db, organization_id=organization_id),
        oldest_pending_seconds=oldest_seconds,
        pending_limit=settings.report_max_pending_per_organization,
        running_limit=settings.report_max_running_per_organization,
    )


def next_report_version(
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


def _status_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def assert_renderable(db: Session, *, organization_id: UUID, version_id: UUID) -> AnalysisVersion:
    """Les deux refus qui ne dépendent pas du rendu, donc vérifiables tout de suite.

    Le reste — une version complète mais sans verdict exploitable, un segment
    disparu — se découvre pendant la génération : c'est le worker qui le constate,
    et le client le lit dans l'état du travail plutôt qu'en attendant un time-out.
    """

    version = db.scalar(
        select(AnalysisVersion).where(
            AnalysisVersion.organization_id == organization_id,
            AnalysisVersion.id == version_id,
        )
    )
    if version is None:
        raise ReportNotRenderableError("Version d'analyse introuvable pour cette organisation.")
    if _status_value(version.status) != "completed":
        raise ReportNotRenderableError(
            "Cette version d'analyse n'est pas terminée : aucun verdict exploitable n'existe "
            "encore, et aucun rapport ne peut être établi."
        )
    if not version.overall_compliance:
        raise ReportNotRenderableError(
            "Cette version d'analyse ne contient aucun verdict réglementaire (pipeline antérieur). "
            "Aucun rapport ne peut être établi à partir d'une analyse vide : relancez l'analyse."
        )
    return version


def enqueue_report(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    analysis_id: UUID,
    report_format: str,
    options: dict[str, object],
    requested_by_user_id: UUID | None,
    now: datetime | None = None,
) -> QueuedReport:
    """Met un rapport en file, ou renvoie le travail équivalent déjà en attente."""

    if report_format not in SUPPORTED_REPORT_FORMATS:
        raise ReportQueueError(f"Format de rapport non pris en charge : {report_format!r}.")

    moment = now or utcnow()
    analysis = db.scalar(
        select(Analysis).where(
            Analysis.organization_id == organization_id,
            Analysis.id == analysis_id,
        )
    )
    if analysis is None:
        raise LookupError("Analyse introuvable pour cette organisation.")
    latest_version = db.scalar(
        select(AnalysisVersion)
        .where(
            AnalysisVersion.organization_id == organization_id,
            AnalysisVersion.analysis_id == analysis.id,
        )
        .order_by(AnalysisVersion.version_number.desc())
    )
    if latest_version is None:
        raise LookupError("Aucune version d'analyse disponible.")
    assert_renderable(db, organization_id=organization_id, version_id=latest_version.id)

    existing = db.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.analysis_version_id == latest_version.id,
            ReportJob.report_format == report_format,
            ReportJob.status.in_((ReportJobStatus.QUEUED, ReportJobStatus.RUNNING)),
        )
        .order_by(ReportJob.created_at.asc())
        .limit(1)
    )
    if existing is not None:
        report = db.get(Report, existing.report_id) if existing.report_id else None
        return QueuedReport(
            report_id=existing.report_id,
            job_id=existing.id,
            analysis_version_id=existing.analysis_version_id,
            report_format=report_format,
            report_version_number=report.version_number if report else 0,
            queued_at=_as_utc(existing.created_at),
            reused=True,
        )

    snapshot = queue_snapshot(db, organization_id=organization_id, settings=settings)
    if snapshot.pending >= settings.report_max_pending_per_organization:
        raise ReportQueueSaturatedError(
            pending=snapshot.pending,
            limit=settings.report_max_pending_per_organization,
            oldest_pending_seconds=snapshot.oldest_pending_seconds,
        )

    report = Report(
        id=uuid4(),
        organization_id=organization_id,
        analysis_version_id=latest_version.id,
        version_number=next_report_version(
            db,
            organization_id=organization_id,
            version_id=latest_version.id,
            report_format=report_format,
        ),
        report_format=report_format,
        status=ReportStatus.REQUESTED,
        requested_by_user_id=requested_by_user_id,
    )
    db.add(report)
    job = ReportJob(
        id=uuid4(),
        organization_id=organization_id,
        analysis_version_id=latest_version.id,
        report_id=report.id,
        report_format=report_format,
        options_json=dict(options),
        requested_by_user_id=requested_by_user_id,
        status=ReportJobStatus.QUEUED,
        attempt_count=0,
        max_attempts=settings.report_max_attempts,
        available_at=moment,
    )
    db.add(job)
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=requested_by_user_id,
        entity_type="report_job",
        entity_id=job.id,
        action="report.generation_queued",
        payload={
            "report_id": str(report.id),
            "analysis_id": str(analysis.id),
            "analysis_version_id": str(latest_version.id),
            "report_format": report_format,
            "report_version_number": report.version_number,
            "pending_before": snapshot.pending,
            "pending_limit": settings.report_max_pending_per_organization,
        },
    )
    db.flush()
    return QueuedReport(
        report_id=report.id,
        job_id=job.id,
        analysis_version_id=latest_version.id,
        report_format=report_format,
        report_version_number=report.version_number,
        queued_at=moment,
        reused=False,
    )


def _recover_expired_leases(db: Session, *, organization_id: UUID) -> int:
    """Rend disponibles les travaux dont le worker n'a plus donné signe de vie."""

    now = utcnow()
    expired = list(
        db.scalars(
            select(ReportJob)
            .where(
                ReportJob.organization_id == organization_id,
                ReportJob.status == ReportJobStatus.RUNNING,
                ReportJob.lease_expires_at.is_not(None),
                ReportJob.lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        ).all()
    )
    for job in expired:
        report = db.get(Report, job.report_id) if job.report_id else None
        job.locked_at = None
        job.lease_expires_at = None
        previous_worker = job.worker_id
        job.worker_id = None
        if job.attempt_count < job.max_attempts:
            job.status = ReportJobStatus.QUEUED
            job.available_at = now
            job.error_code = "worker_lease_expired"
            if report is not None:
                report.status = ReportStatus.REQUESTED
        else:
            job.status = ReportJobStatus.FAILED
            job.completed_at = now
            job.error_code = "worker_lease_expired"
            if report is not None:
                report.status = ReportStatus.FAILED
                report.failure_code = "worker_lease_expired"
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=None,
            entity_type="report_job",
            entity_id=job.id,
            action="report.job_lease_recovered",
            payload={
                "report_id": str(job.report_id) if job.report_id else None,
                "previous_worker_id": previous_worker,
                "attempt_count": job.attempt_count,
                "requeued": job.status == ReportJobStatus.QUEUED,
            },
        )
    if expired:
        db.flush()
    return len(expired)


def claim_next_report_job(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    worker_id: str,
) -> ClaimedReportJob | None:
    """Réclame un travail pour une organisation, en respectant son plafond d'équité."""

    _require_tenant_context(db, organization_id)
    _recover_expired_leases(db, organization_id=organization_id)
    if running_job_count(db, organization_id=organization_id) >= settings.report_max_running_per_organization:
        # Un client qui lance trois cents rapports ne doit pas occuper tous les
        # workers : les autres organisations doivent continuer d'avancer.
        return None
    now = utcnow()
    job = db.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.status == ReportJobStatus.QUEUED,
            ReportJob.available_at <= now,
        )
        .order_by(ReportJob.available_at.asc(), ReportJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    report = db.get(Report, job.report_id) if job.report_id else None
    job.status = ReportJobStatus.RUNNING
    job.attempt_count += 1
    job.locked_at = now
    job.lease_expires_at = now + timedelta(seconds=settings.report_lease_seconds)
    job.worker_id = worker_id
    job.started_at = job.started_at or now
    job.completed_at = None
    job.error_code = None
    if report is not None:
        report.status = ReportStatus.GENERATING
        report.failure_code = None
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=None,
        entity_type="report_job",
        entity_id=job.id,
        action="report.generation_started",
        payload={
            "report_id": str(job.report_id) if job.report_id else None,
            "analysis_version_id": str(job.analysis_version_id),
            "report_format": job.report_format,
            "attempt_count": job.attempt_count,
            "worker_id": worker_id,
            "lease_expires_at": job.lease_expires_at.isoformat(),
        },
    )
    db.flush()
    return ClaimedReportJob(
        id=job.id,
        organization_id=organization_id,
        analysis_version_id=job.analysis_version_id,
        report_id=job.report_id,
        report_format=job.report_format,
        options=dict(job.options_json or {}),
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        requested_by_user_id=job.requested_by_user_id,
    )


def complete_report_job(
    db: Session,
    *,
    claim: ClaimedReportJob,
    report_fields: dict[str, object],
    duration_ms: int,
) -> None:
    """Écrit le résultat du travail, si le worker détient toujours le bail."""

    _require_tenant_context(db, claim.organization_id)
    job = db.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == claim.organization_id,
            ReportJob.id == claim.id,
        )
        .with_for_update()
    )
    if job is None or job.status != ReportJobStatus.RUNNING or job.worker_id != claim.worker_id:
        raise ReportJobConflictError(
            "Le bail de ce travail a été repris par un autre worker : le résultat n'est pas écrit."
        )
    report = db.get(Report, job.report_id) if job.report_id else None
    if report is None:
        raise ReportJobConflictError("Rapport introuvable pour ce travail.")
    now = utcnow()
    for field, value in report_fields.items():
        setattr(report, field, value)
    report.status = ReportStatus.READY
    # ``generated_at`` est fourni par le générateur : c'est l'horodatage **signé**,
    # et la vérification le recompose depuis cette colonne. L'écraser par l'instant
    # d'écriture ferait échouer la vérification de tout rapport honnête — le défaut
    # exact que C7 avait déjà corrigé une fois.
    if report.generated_at is None:
        report.generated_at = now
    report.failure_code = None
    job.status = ReportJobStatus.COMPLETED
    job.completed_at = now
    job.locked_at = None
    job.lease_expires_at = None
    job.error_code = None
    job.duration_ms = duration_ms
    append_audit_event(
        db,
        organization_id=claim.organization_id,
        actor_user_id=claim.requested_by_user_id,
        entity_type="report_job",
        entity_id=job.id,
        action="report.generated",
        payload={
            "report_id": str(report.id),
            "analysis_version_id": str(job.analysis_version_id),
            "report_format": job.report_format,
            "verification_reference": report.verification_reference,
            "sha256": report.sha256,
            "size_bytes": report.size_bytes,
            "attempt_count": job.attempt_count,
            "duration_ms": duration_ms,
            "worker_id": job.worker_id,
        },
    )
    db.flush()


def record_report_failure(
    db: Session,
    *,
    settings: Settings,
    claim: ClaimedReportJob,
    error_code: str,
    detail: str,
    retryable: bool,
) -> bool:
    """Consigne l'échec ; replanifie avec un délai croissant, ou abandonne.

    Retourne ``True`` si le travail a été replanifié.
    """

    _require_tenant_context(db, claim.organization_id)
    job = db.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == claim.organization_id,
            ReportJob.id == claim.id,
        )
        .with_for_update()
    )
    if job is None or job.status != ReportJobStatus.RUNNING or job.worker_id != claim.worker_id:
        return False
    report = db.get(Report, job.report_id) if job.report_id else None
    now = utcnow()
    job.error_code = error_code
    job.locked_at = None
    job.lease_expires_at = None
    job.worker_id = None

    if retryable and job.attempt_count < job.max_attempts:
        delay = min(
            settings.report_retry_base_seconds * (2 ** max(job.attempt_count - 1, 0)),
            60 * 60,
        )
        job.status = ReportJobStatus.QUEUED
        job.available_at = now + timedelta(seconds=delay)
        if report is not None:
            report.status = ReportStatus.REQUESTED
            report.failure_code = error_code
        append_audit_event(
            db,
            organization_id=claim.organization_id,
            actor_user_id=None,
            entity_type="report_job",
            entity_id=job.id,
            action="report.generation_retry_scheduled",
            payload={
                "report_id": str(job.report_id) if job.report_id else None,
                "error_code": error_code,
                "detail": detail[:500],
                "attempt_count": job.attempt_count,
                "available_at": job.available_at.isoformat(),
            },
        )
        db.flush()
        return True

    job.status = ReportJobStatus.FAILED
    job.completed_at = now
    if report is not None:
        report.status = ReportStatus.FAILED
        report.failure_code = error_code
    append_audit_event(
        db,
        organization_id=claim.organization_id,
        actor_user_id=None,
        entity_type="report_job",
        entity_id=job.id,
        action="report.generation_failed",
        payload={
            "report_id": str(job.report_id) if job.report_id else None,
            "error_code": error_code,
            "detail": detail[:500],
            "attempt_count": job.attempt_count,
            "retryable": retryable,
        },
    )
    db.flush()
    return False


def report_job_state(db: Session, *, organization_id: UUID, job_id: UUID) -> dict[str, object]:
    """L'état d'un travail tel qu'un client peut le lire, sans jargon interne."""

    job = db.scalar(
        select(ReportJob).where(
            ReportJob.organization_id == organization_id,
            ReportJob.id == job_id,
        )
    )
    if job is None:
        raise LookupError("Travail de génération introuvable pour cette organisation.")
    report = db.get(Report, job.report_id) if job.report_id else None
    return {
        "job_id": job.id,
        "report_id": job.report_id,
        "analysis_version_id": job.analysis_version_id,
        "report_format": job.report_format,
        "job_status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "report_status": (
            report.status.value if report is not None and hasattr(report.status, "value") else (
                str(report.status) if report is not None else None
            )
        ),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "available_at": _as_utc(job.available_at),
        "started_at": _as_utc(job.started_at) if job.started_at else None,
        "completed_at": _as_utc(job.completed_at) if job.completed_at else None,
        "duration_ms": job.duration_ms,
        "error_code": job.error_code,
        "verification_reference": report.verification_reference if report is not None else None,
        "sha256": report.sha256 if report is not None else None,
        "size_bytes": report.size_bytes if report is not None else None,
        "content_type": report.content_type if report is not None else None,
        "download_available": bool(
            report is not None
            and report.status == ReportStatus.READY
            and report.storage_key
        ),
    }


__all__ = [
    "ClaimedReportJob",
    "GenerateReportResult",
    "QueueSnapshot",
    "QueuedReport",
    "REPORT_CONTENT_TYPES",
    "REPORT_FILE_EXTENSIONS",
    "ReportJobConflictError",
    "ReportNotRenderableError",
    "ReportQueueError",
    "ReportQueueSaturatedError",
    "SUPPORTED_REPORT_FORMATS",
    "assert_renderable",
    "claim_next_report_job",
    "complete_report_job",
    "enqueue_report",
    "next_report_version",
    "pending_job_count",
    "queue_snapshot",
    "record_report_failure",
    "report_job_state",
    "running_job_count",
    "utcnow",
]
