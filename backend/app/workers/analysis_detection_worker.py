"""PostgreSQL-backed worker for durable deterministic claim-detection jobs.

Jobs remain tenant-scoped. The worker only enumerates the global organization
registry, installs one tenant RLS context, and then claims/processes a single
job in that context. It never reads raw object storage or invokes the
regulatory prototype evaluator.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.analyses.service import (
    AnalysisConflictError,
    AnalysisDetectionRetryableError,
    AnalysisDetectionTerminalError,
    ClaimedAnalysisDetectionJob,
    claim_next_analysis_detection_job,
    process_claimed_analysis_detection,
    record_analysis_detection_failure,
)
from app.core.config import Settings, settings
from app.core.database import SessionLocal
from app.identity.service import set_db_request_context
from app.models.domain import Organization, OrganizationStatus

logger = logging.getLogger(__name__)
# This UUID only establishes PostgreSQL request-local RLS context; worker audit
# events correctly retain no human actor.
WORKER_CONTEXT_USER_ID = UUID(int=0)


class AnalysisDetectionWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session] = SessionLocal,
        worker_id: str,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.worker_id = worker_id
        self._last_organization_id: UUID | None = None

    def _active_organization_ids(self) -> list[UUID]:
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

    def _claim(self, organization_id: UUID) -> ClaimedAnalysisDetectionJob | None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db,
                    user_id=WORKER_CONTEXT_USER_ID,
                    organization_id=organization_id,
                )
                claim = claim_next_analysis_detection_job(
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

    def _record_failure(self, claim: ClaimedAnalysisDetectionJob, *, error_code: str, retryable: bool) -> None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db,
                    user_id=WORKER_CONTEXT_USER_ID,
                    organization_id=claim.organization_id,
                )
                record_analysis_detection_failure(
                    db,
                    settings=self.settings,
                    claim=claim,
                    error_code=error_code,
                    retryable=retryable,
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Unable to record analysis-detection failure",
                    extra={"job_id": str(claim.id), "organization_id": str(claim.organization_id)},
                )

    def _process(self, claim: ClaimedAnalysisDetectionJob) -> None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db,
                    user_id=WORKER_CONTEXT_USER_ID,
                    organization_id=claim.organization_id,
                )
                completion = process_claimed_analysis_detection(db, claim=claim)
                db.commit()
                logger.info(
                    "Analysis claim detection completed",
                    extra={
                        "job_id": str(claim.id),
                        "organization_id": str(claim.organization_id),
                        "analysis_version_id": str(claim.analysis_version_id),
                        "claim_count": completion.claim_count,
                        "review_required_claim_count": completion.review_required_claim_count,
                    },
                )
            except AnalysisDetectionTerminalError as exc:
                db.rollback()
                self._record_failure(claim, error_code=exc.code, retryable=False)
            except AnalysisDetectionRetryableError as exc:
                db.rollback()
                self._record_failure(claim, error_code=exc.code, retryable=True)
            except AnalysisConflictError:
                # Another worker may legitimately have recovered a lease. Do
                # not overwrite the durable state it published.
                db.rollback()
                logger.warning("Analysis detection lease was lost", extra={"job_id": str(claim.id)})
            except Exception:
                db.rollback()
                logger.exception("Unexpected analysis-detection worker error", extra={"job_id": str(claim.id)})
                self._record_failure(claim, error_code="unexpected_worker_error", retryable=True)

    def run_once(self) -> bool:
        """Claim and process at most one job, returning whether work was found."""
        for organization_id in self._round_robin_organization_ids():
            try:
                claim = self._claim(organization_id)
            except Exception:
                logger.exception(
                    "Unable to claim an analysis-detection job",
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
        logger.info("Analysis detection worker started", extra={"worker_id": self.worker_id})
        while True:
            if not self.run_once():
                time.sleep(self.settings.analysis_detection_poll_seconds)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    worker_id = os.getenv("ANALYSIS_DETECTION_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    AnalysisDetectionWorker(settings=settings, worker_id=worker_id).run_forever()


if __name__ == "__main__":  # pragma: no cover - exercised by Compose deployment.
    main()
