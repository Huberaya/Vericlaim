"""Profondeur et retard des files durables, lus en base (A-09).

Une métrique de file écrite par le worker serait fausse dès que le worker est
mort — c'est-à-dire exactement quand on en a besoin. Ces compteurs sont donc
lus **dans PostgreSQL**, par le même processus que ``/metrics`` : ils restent
exacts même sans aucun worker en vie.

Chaque lecture passe par le contexte tenant : aucune métrique n'agrège des
lignes que le RLS interdit, et aucun identifiant de tenant n'est exporté.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.identity.service import set_db_request_context
from app.models.domain import (
    AnalysisDetectionJob,
    AnalysisDetectionJobStatus,
    DocumentExtractionJob,
    DocumentExtractionJobStatus,
    Organization,
    OrganizationStatus,
    utcnow,
)


def _age_seconds(moment: datetime | None, *, now: datetime) -> float:
    if moment is None:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (now - moment).total_seconds())


def collect_queue_metrics(db: Session) -> dict[str, Any]:
    """Retourne profondeur par état et âge du plus vieux job en attente.

    Le nom des séries est stable : un tableau de bord n'a pas à changer quand
    une file est ajoutée, seule une nouvelle série apparaît.
    """

    now = utcnow()
    metrics: dict[str, Any] = {
        "document_queue_queued": 0,
        "document_queue_running": 0,
        "document_queue_failed": 0,
        "document_queue_oldest_queued_seconds": 0.0,
        "analysis_queue_queued": 0,
        "analysis_queue_running": 0,
        "analysis_queue_failed": 0,
        "analysis_queue_oldest_queued_seconds": 0.0,
        "organizations_active": 0,
    }

    organization_ids = db.scalars(
        select(Organization.id).where(Organization.status == OrganizationStatus.ACTIVE)
    ).all()
    metrics["organizations_active"] = len(organization_ids)

    for organization_id in organization_ids:
        # Plusieurs tenants partagent ce processus : le contexte est reposé à
        # chaque tour, et jamais laissé en place entre deux organisations.
        # Un worker écrit avec un acteur nul : la métrique ne doit pas inventer
        # d'utilisateur, seulement installer le contexte tenant.
        set_db_request_context(db, user_id=UUID(int=0), organization_id=organization_id)

        for status, key in (
            (DocumentExtractionJobStatus.QUEUED, "document_queue_queued"),
            (DocumentExtractionJobStatus.RUNNING, "document_queue_running"),
            (DocumentExtractionJobStatus.FAILED, "document_queue_failed"),
        ):
            count = db.scalar(
                select(func.count())
                .select_from(DocumentExtractionJob)
                .where(DocumentExtractionJob.status == status)
            )
            metrics[key] += int(count or 0)

        oldest = db.scalar(
            select(func.min(DocumentExtractionJob.created_at)).where(
                DocumentExtractionJob.status == DocumentExtractionJobStatus.QUEUED
            )
        )
        metrics["document_queue_oldest_queued_seconds"] = max(
            metrics["document_queue_oldest_queued_seconds"], _age_seconds(oldest, now=now)
        )

        for status, key in (
            (AnalysisDetectionJobStatus.QUEUED, "analysis_queue_queued"),
            (AnalysisDetectionJobStatus.RUNNING, "analysis_queue_running"),
            (AnalysisDetectionJobStatus.FAILED, "analysis_queue_failed"),
        ):
            count = db.scalar(
                select(func.count())
                .select_from(AnalysisDetectionJob)
                .where(AnalysisDetectionJob.status == status)
            )
            metrics[key] += int(count or 0)

        oldest_analysis = db.scalar(
            select(func.min(AnalysisDetectionJob.created_at)).where(
                AnalysisDetectionJob.status == AnalysisDetectionJobStatus.QUEUED
            )
        )
        metrics["analysis_queue_oldest_queued_seconds"] = max(
            metrics["analysis_queue_oldest_queued_seconds"], _age_seconds(oldest_analysis, now=now)
        )

    return metrics
