"""Real alerts, replacing the two fabricated ones (chantier C16).

`GET /api/v1/enterprise/alerts` used to answer the same two literal alerts to every
organization — « SSO OIDC / SAML Actif » and « Intégrité du Référentiel Rule Book »,
both `is_acknowledged: true` — from a hardcoded list. Nothing was monitored, and a
monitoring screen that cannot go red is worse than no screen: it is a claim that
someone is watching.

The alerts below are computed from the database and from the readiness probes, and
each carries the measurement that produced it. Three rules:

1. **Every alert names its measurement.** `observed_value` and `threshold` are part
   of the payload, so a human can judge without trusting the wording.
2. **Nothing is acknowledged automatically.** `is_acknowledged` is always false here:
   acknowledging is a human act, and this code base has no acknowledgement storage —
   claiming otherwise would be the same lie in a different field.
3. **An organization sees its own queues, never the platform's totals.** The one
   exception is the readiness block, which is explicitly labelled as concerning the
   instance and not the tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

#: A queue that has not moved for this long is stalled, whatever its depth.
QUEUE_STALL_SECONDS = 15 * 60
#: Depth at which an operator should look before a client complains.
QUEUE_DEPTH_WARNING = 50
QUEUE_DEPTH_CRITICAL = 500


@dataclass(frozen=True)
class Alert:
    id: str
    severity: str  # info | warning | critical
    category: str  # queue | integrity | security | system | quota
    title: str
    message: str
    occurred_at: datetime
    observed_value: float | str | None = None
    threshold: float | str | None = None
    is_acknowledged: bool = False
    scope: str = "organization"

    def as_enterprise_alert(self, index: int) -> dict[str, object]:
        return {
            "id": f"{self.id}-{index:02d}",
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "message": self.message,
            "occurred_at": self.occurred_at,
            "is_acknowledged": self.is_acknowledged,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_organization_alerts(db: Session, *, organization_id) -> list[Alert]:
    """Everything this organization's own data can tell an operator."""
    from app.audit.service import verify_audit_chain
    from app.models.domain import (
        AnalysisDetectionJob,
        AnalysisDetectionJobStatus,
        DocumentExtractionJob,
        DocumentExtractionJobStatus,
        OrganizationRetentionPolicy,
    )

    alerts: list[Alert] = []
    now = _now()

    # --- document extraction queue ---------------------------------------- #
    extraction_depth = int(
        db.scalar(
            select(func.count(DocumentExtractionJob.id)).where(
                DocumentExtractionJob.organization_id == organization_id,
                DocumentExtractionJob.status.in_(
                    [DocumentExtractionJobStatus.QUEUED, DocumentExtractionJobStatus.RUNNING]
                ),
            )
        )
        or 0
    )
    detection_depth = int(
        db.scalar(
            select(func.count(AnalysisDetectionJob.id)).where(
                AnalysisDetectionJob.organization_id == organization_id,
                AnalysisDetectionJob.status.in_(
                    [AnalysisDetectionJobStatus.QUEUED, AnalysisDetectionJobStatus.RUNNING]
                ),
            )
        )
        or 0
    )
    for queue_name, depth in (("extraction documentaire", extraction_depth), ("détection d'allégations", detection_depth)):
        if depth >= QUEUE_DEPTH_CRITICAL:
            alerts.append(
                Alert(
                    id=f"queue-depth-critical-{queue_name.split()[0]}",
                    severity="critical",
                    category="queue",
                    title=f"File {queue_name} saturée",
                    message=(
                        f"{depth} travaux en attente ou en cours pour cette organisation. "
                        "Le traitement n'avance pas au rythme des dépôts."
                    ),
                    occurred_at=now,
                    observed_value=depth,
                    threshold=QUEUE_DEPTH_CRITICAL,
                )
            )
        elif depth >= QUEUE_DEPTH_WARNING:
            alerts.append(
                Alert(
                    id=f"queue-depth-warning-{queue_name.split()[0]}",
                    severity="warning",
                    category="queue",
                    title=f"File {queue_name} en retard",
                    message=f"{depth} travaux en attente ou en cours pour cette organisation.",
                    occurred_at=now,
                    observed_value=depth,
                    threshold=QUEUE_DEPTH_WARNING,
                )
            )

    # --- stalled queue ----------------------------------------------------- #
    oldest = None
    for model in (DocumentExtractionJob, AnalysisDetectionJob):
        value = db.scalar(
            select(func.min(model.created_at)).where(
                model.organization_id == organization_id,
                model.status == (
                    DocumentExtractionJobStatus.QUEUED
                    if model is DocumentExtractionJob
                    else AnalysisDetectionJobStatus.QUEUED
                ),
            )
        )
        if value is not None and (oldest is None or value < oldest):
            oldest = value
    if oldest is not None:
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        age = (now - oldest).total_seconds()
        if age >= QUEUE_STALL_SECONDS:
            alerts.append(
                Alert(
                    id="queue-stalled",
                    severity="critical",
                    category="queue",
                    title="Travaux en attente depuis trop longtemps",
                    message=(
                        f"Le plus ancien travail en attente date de {int(age // 60)} minutes. "
                        "Aucun worker ne le réclame : vérifier que le worker tourne et que "
                        "la base est accessible depuis lui."
                    ),
                    occurred_at=now,
                    observed_value=int(age),
                    threshold=QUEUE_STALL_SECONDS,
                )
            )

    # --- repeated job failures --------------------------------------------- #
    for model, failed_status, label in (
        (DocumentExtractionJob, DocumentExtractionJobStatus.FAILED, "extraction documentaire"),
        (AnalysisDetectionJob, AnalysisDetectionJobStatus.FAILED, "détection d'allégations"),
    ):
        failures = int(
            db.scalar(
                select(func.count(model.id)).where(
                    model.organization_id == organization_id,
                    model.status == failed_status,
                )
            )
            or 0
        )
        # "Repeated failure" is not an invented threshold: a job that consumed all of
        # its attempts is exactly the definition the worker already uses to stop
        # retrying (`attempt_count >= max_attempts`).
        exhausted = int(
            db.scalar(
                select(func.count(model.id)).where(
                    model.organization_id == organization_id,
                    model.status == failed_status,
                    model.attempt_count >= model.max_attempts,
                )
            )
            or 0
        )
        if failures:
            alerts.append(
                Alert(
                    id=f"job-failures-{label.split()[0]}",
                    severity="critical" if exhausted else "warning",
                    category="queue",
                    title=f"Échecs de traitement ({label})",
                    message=(
                        f"{failures} travail(x) en échec pour cette organisation, dont {exhausted} "
                        "ayant épuisé leurs tentatives. Chaque échec conserve son code d'erreur : "
                        "consulter la liste des travaux pour la cause."
                    ),
                    occurred_at=now,
                    observed_value=failures,
                    threshold="attempts épuisés" if exhausted else "tentatives restantes",
                )
            )

    # --- audit chain -------------------------------------------------------- #
    try:
        verification = verify_audit_chain(db, organization_id=organization_id)
    except Exception as exc:  # noqa: BLE001 — an unverifiable chain is itself an alert
        alerts.append(
            Alert(
                id="audit-chain-unverifiable",
                severity="critical",
                category="integrity",
                title="Chaîne d'audit non vérifiable",
                message=(
                    "La vérification de la chaîne d'audit a échoué techniquement "
                    f"({type(exc).__name__}). L'intégrité ne peut pas être affirmée."
                ),
                occurred_at=now,
                observed_value=type(exc).__name__,
            )
        )
    else:
        if not verification.is_valid:
            alerts.append(
                Alert(
                    id="audit-chain-invalid",
                    severity="critical",
                    category="integrity",
                    title="Chaîne d'audit rompue",
                    message=(
                        "La chaîne d'audit de l'organisation ne vérifie plus : "
                        f"{verification.error_detail or 'rupture détectée'}. "
                        "Aucun rapport ne devrait être émis avant arbitrage."
                    ),
                    occurred_at=now,
                    observed_value=verification.total_events,
                )
            )

    # --- retention policy --------------------------------------------------- #
    policy = db.scalar(
        select(OrganizationRetentionPolicy).where(
            OrganizationRetentionPolicy.organization_id == organization_id
        )
    )
    if policy is None:
        alerts.append(
            Alert(
                id="retention-undeclared",
                severity="info",
                category="system",
                title="Politique de rétention non déclarée",
                message=(
                    "Cette organisation n'a déclaré aucune durée de conservation. Les durées "
                    "affichées resteront nulles tant que la déclaration n'est pas faite."
                ),
                occurred_at=now,
                observed_value="non déclarée",
            )
        )

    return alerts


def evaluate_platform_alerts(settings, *, storage: object, scanner: object) -> list[Alert]:
    """Readiness of the instance itself — explicitly not a tenant-scoped signal."""
    from app.core.readiness import build_readiness_report

    report = build_readiness_report(settings, storage=storage, scanner=scanner)
    alerts: list[Alert] = []
    now = _now()

    for check in report.checks:
        if check.status == "failed":
            alerts.append(
                Alert(
                    id=f"platform-{check.name}",
                    severity="critical",
                    category="system",
                    title=f"Dépendance indisponible : {check.name}",
                    message=(
                        f"La sonde de disponibilité échoue ({check.detail}). "
                        "Cette alerte concerne l'instance, pas l'organisation : elle est "
                        "identique pour tous les clients de cette instance."
                    ),
                    occurred_at=now,
                    observed_value=check.status,
                    threshold="ok",
                    scope="platform",
                )
            )
        elif check.status == "disabled":
            alerts.append(
                Alert(
                    id=f"platform-{check.name}-disabled",
                    severity="info",
                    category="system",
                    title=f"Capacité désactivée : {check.name}",
                    message=check.detail,
                    occurred_at=now,
                    observed_value="disabled",
                    scope="platform",
                )
            )
    return alerts
