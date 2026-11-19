"""C22 — worker de génération des rapports PDF et ZIP.

Ce processus est le seul endroit où un rapport est rendu. Il remplace le rendu
qui se faisait dans la requête HTTP.

Trois propriétés, et pourquoi elles sont ici plutôt qu'ailleurs :

* **Il est sûr à plusieurs répliques.** La réclamation utilise ``FOR UPDATE SKIP
  LOCKED`` et un bail : deux workers ne prennent pas le même travail, et un worker
  qui meurt en cours de route rend son travail disponible après expiration du bail
  (``_recover_expired_leases``). ``docker compose up --scale report-worker=4``
  suffit donc à multiplier la capacité — il n'y a rien à configurer côté client ;
* **Il tourne en rond sur les organisations** plutôt que de traiter une
  organisation jusqu'à épuisement : un client qui a mis mille rapports en file ne
  retarde pas le suivant. Un plafond de travaux en cours par organisation
  (``REPORT_MAX_RUNNING_PER_ORGANIZATION``) borne en plus ce qu'un locataire peut
  occuper simultanément ;
* **Il ne décide de rien.** Les refus qui ne dépendent pas du rendu (analyse
  inexistante, version sans verdict, file saturée) ont déjà été prononcés à l'entrée
  dans la file. Ici, on ne fait qu'exécuter, consigner et, si nécessaire, replanifier.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, settings
from app.core.database import SessionLocal
from app.core.logging import bind_log_context, configure_logging
from app.core.metrics import record_job_outcome, record_worker_heartbeat
from app.documents.storage import ObjectStorage, build_object_storage
from app.identity.service import set_db_request_context
from app.models.domain import Organization, OrganizationStatus
from app.reports.generation import (
    ReportGenerationRetryable,
    ReportGenerationUnavailable,
    process_claimed_report_job,
)
from app.reports.queue import (
    ClaimedReportJob,
    ReportJobConflictError,
    claim_next_report_job,
    record_report_failure,
)

logger = logging.getLogger(__name__)

WORKER_CONTEXT_USER_ID = UUID(int=0)


class ReportGenerationWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        storage: ObjectStorage,
        session_factory: sessionmaker[Session] = SessionLocal,
        worker_id: str,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.session_factory = session_factory
        self.worker_id = worker_id
        self._last_organization_id: UUID | None = None

    def _active_organization_ids(self) -> list[UUID]:
        # ``organizations`` est un annuaire global : les lignes de la file restent
        # protégées par RLS et ne sont lues qu'après avoir posé le contexte.
        with self.session_factory() as db:
            return list(
                db.scalars(
                    select(Organization.id)
                    .where(Organization.status == OrganizationStatus.ACTIVE)
                    .order_by(Organization.id.asc())
                ).all()
            )

    def _round_robin_organization_ids(self) -> list[UUID]:
        organization_ids = self._active_organization_ids()
        if not organization_ids or self._last_organization_id not in organization_ids:
            return organization_ids
        position = organization_ids.index(self._last_organization_id)
        return organization_ids[position + 1 :] + organization_ids[: position + 1]

    def _claim(self, organization_id: UUID) -> ClaimedReportJob | None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db, user_id=WORKER_CONTEXT_USER_ID, organization_id=organization_id
                )
                claim = claim_next_report_job(
                    db,
                    settings=self.settings,
                    organization_id=organization_id,
                    worker_id=self.worker_id,
                )
                db.commit()
                return claim
            except Exception:
                db.rollback()
                raise

    def _record_failure(
        self, claim: ClaimedReportJob, *, error_code: str, detail: str, retryable: bool
    ) -> None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db, user_id=WORKER_CONTEXT_USER_ID, organization_id=claim.organization_id
                )
                record_report_failure(
                    db,
                    settings=self.settings,
                    claim=claim,
                    error_code=error_code,
                    detail=detail,
                    retryable=retryable,
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Impossible de consigner l'échec du travail de génération",
                    extra={
                        "job_id": str(claim.id),
                        "organization_id": str(claim.organization_id),
                    },
                )

    def _process(self, claim: ClaimedReportJob) -> None:
        bind_log_context(job_id=str(claim.id), organization_id=str(claim.organization_id))
        started = time.perf_counter()
        outcome = "processed"
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db, user_id=WORKER_CONTEXT_USER_ID, organization_id=claim.organization_id
                )
                result = process_claimed_report_job(
                    db, settings=self.settings, storage=self.storage, claim=claim
                )
                db.commit()
                logger.info(
                    "Rapport généré",
                    extra={
                        "job_id": str(claim.id),
                        "organization_id": str(claim.organization_id),
                        "report_id": str(result.report_id),
                        "report_format": claim.report_format,
                        "size_bytes": result.size_bytes,
                        "duration_ms": result.duration_ms,
                        "verification_reference": result.verification_reference,
                    },
                )
            except ReportGenerationUnavailable as exc:
                db.rollback()
                outcome = "failed"
                logger.error(
                    "Génération de rapport impossible",
                    extra={
                        "event": "job_failed",
                        "job_id": str(claim.id),
                        "organization_id": str(claim.organization_id),
                        "error_code": "report_not_renderable",
                        "retryable": False,
                        "detail": str(exc)[:500],
                    },
                )
                self._record_failure(
                    claim,
                    error_code="report_not_renderable",
                    detail=str(exc),
                    retryable=False,
                )
            except ReportGenerationRetryable as exc:
                db.rollback()
                outcome = "failed"
                logger.warning(
                    "Génération de rapport à réessayer",
                    extra={
                        "event": "job_failed",
                        "job_id": str(claim.id),
                        "organization_id": str(claim.organization_id),
                        "error_code": exc.code,
                        "retryable": True,
                    },
                )
                self._record_failure(
                    claim, error_code=exc.code, detail=str(exc), retryable=True
                )
            except ReportJobConflictError:
                # Le bail a été repris par un autre worker : son résultat fait foi,
                # écrire le nôtre écraserait une décision déjà prise.
                db.rollback()
                outcome = "skipped"
                logger.warning(
                    "Bail de génération perdu",
                    extra={"job_id": str(claim.id), "organization_id": str(claim.organization_id)},
                )
            except Exception as exc:  # pragma: no cover - garde-fou de dernier recours
                db.rollback()
                outcome = "failed"
                logger.exception(
                    "Erreur inattendue du worker de rapports", extra={"job_id": str(claim.id)}
                )
                self._record_failure(
                    claim,
                    error_code="unexpected_worker_error",
                    detail=f"{type(exc).__name__}: {exc}",
                    retryable=True,
                )
            finally:
                record_job_outcome(
                    job_kind="report_generation",
                    outcome=outcome,
                    duration_seconds=time.perf_counter() - started,
                )

    def run_once(self) -> bool:
        for organization_id in self._round_robin_organization_ids():
            try:
                claim = self._claim(organization_id)
            except Exception:
                logger.exception(
                    "Impossible de réclamer un travail de génération",
                    extra={"organization_id": str(organization_id)},
                )
                continue
            if claim is None:
                continue
            self._last_organization_id = organization_id
            self._process(claim)
            return True
        return False

    def run_forever(self) -> None:
        logger.info("Worker de génération de rapports démarré", extra={"worker_id": self.worker_id})
        while True:
            record_worker_heartbeat(worker_kind="report_generation", worker_id=self.worker_id)
            if not self.run_once():
                time.sleep(self.settings.report_poll_seconds)


def main() -> None:
    configure_logging(service="vericlaim-report-worker")
    worker_id = os.getenv("REPORT_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    ReportGenerationWorker(
        settings=settings,
        storage=build_object_storage(settings),
        worker_id=worker_id,
    ).run_forever()


if __name__ == "__main__":  # pragma: no cover - exécuté par le déploiement Compose.
    main()
