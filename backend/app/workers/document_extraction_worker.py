"""PostgreSQL-backed worker for durable document OCR/extraction jobs.

The queue itself is tenant-scoped. To preserve RLS, this worker enumerates the
small global ``organizations`` directory, then sets one organisation context
before it can see or claim that tenant's jobs. It never needs a broad RLS bypass
or a separate Redis/RabbitMQ broker.
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
from app.documents.extraction import (
    ClaimedExtractionJob,
    DocumentExtractionConflictError,
    DocumentExtractionRetryableError,
    DocumentExtractionTerminalError,
    claim_next_document_extraction_job,
    process_claimed_document_extraction,
    record_document_extraction_failure,
)
from app.documents.storage import ObjectStorage, build_object_storage
from app.identity.service import set_db_request_context
from app.models.domain import Organization, OrganizationStatus

logger = logging.getLogger(__name__)
# This value is only stored in PostgreSQL's request-local setting; it is never
# persisted as an actor foreign key. Worker audit events correctly use NULL.
WORKER_CONTEXT_USER_ID = UUID(int=0)


class DocumentExtractionWorker:
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
        # Organizations are a global directory; document/job rows remain RLS
        # protected and are accessed only after a tenant context is installed.
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

    def _claim(self, organization_id: UUID) -> ClaimedExtractionJob | None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db,
                    user_id=WORKER_CONTEXT_USER_ID,
                    organization_id=organization_id,
                )
                claim = claim_next_document_extraction_job(
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

    def _record_failure(self, claim: ClaimedExtractionJob, *, error_code: str, retryable: bool) -> None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db,
                    user_id=WORKER_CONTEXT_USER_ID,
                    organization_id=claim.organization_id,
                )
                record_document_extraction_failure(
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
                    "Unable to record extraction-job failure",
                    extra={"job_id": str(claim.id), "organization_id": str(claim.organization_id)},
                )

    def _process(self, claim: ClaimedExtractionJob) -> None:
        with self.session_factory() as db:
            try:
                set_db_request_context(
                    db,
                    user_id=WORKER_CONTEXT_USER_ID,
                    organization_id=claim.organization_id,
                )
                completion = process_claimed_document_extraction(
                    db,
                    settings=self.settings,
                    storage=self.storage,
                    claim=claim,
                )
                db.commit()
                logger.info(
                    "Document extraction completed",
                    extra={
                        "job_id": str(claim.id),
                        "organization_id": str(claim.organization_id),
                        "document_version_id": str(claim.document_version_id),
                        "segment_count": completion.segment_count,
                        "requires_human_review": completion.requires_human_review,
                    },
                )
            except DocumentExtractionTerminalError as exc:
                db.rollback()
                self._record_failure(claim, error_code=exc.code, retryable=False)
            except DocumentExtractionRetryableError as exc:
                db.rollback()
                self._record_failure(claim, error_code=exc.code, retryable=True)
            except DocumentExtractionConflictError:
                # A lease can legitimately be recovered by another worker after
                # a crash/timeout. Do not overwrite its decision.
                db.rollback()
                logger.warning("Document extraction lease was lost", extra={"job_id": str(claim.id)})
            except Exception:
                db.rollback()
                logger.exception("Unexpected document extraction worker error", extra={"job_id": str(claim.id)})
                self._record_failure(claim, error_code="unexpected_worker_error", retryable=True)

    def run_once(self) -> bool:
        """Claim and process at most one job, returning whether work was found."""
        for organization_id in self._round_robin_organization_ids():
            try:
                claim = self._claim(organization_id)
            except Exception:
                logger.exception(
                    "Unable to claim a document extraction job",
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
        logger.info("Document extraction worker started", extra={"worker_id": self.worker_id})
        while True:
            if not self.run_once():
                time.sleep(self.settings.document_extraction_poll_seconds)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    worker_id = os.getenv("DOCUMENT_EXTRACTION_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    DocumentExtractionWorker(
        settings=settings,
        storage=build_object_storage(settings),
        worker_id=worker_id,
    ).run_forever()


if __name__ == "__main__":  # pragma: no cover - exercised by Compose deployment.
    main()
