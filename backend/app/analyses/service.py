"""Durable, tenant-scoped deterministic claim-detection orchestration.

This module deliberately stops at citeable lexical findings. It never invokes
``InferenceEvaluator``, rule matching, evidence linking, recommendation
creation, risk scoring, or an LLM. A durable PostgreSQL job only reads the
immutable C4 document segments already stored for the selected document
versions.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import sha256_json
from app.engine.fact_extractor import FactExtractor
from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    AnalysisDetectionJob,
    AnalysisDetectionJobStatus,
    AnalysisDocument,
    AnalysisStatus,
    AnalysisVersion,
    Claim,
    ClaimSource,
    ClaimStatus,
    Document,
    DocumentSegment,
    DocumentVersion,
    ExtractionStatus,
    Product,
    SegmentType,
    Supplier,
)

CLAIM_DETECTION_ENGINE_VERSION = "vericlaim-fact-extractor-v1"
# No rule book is consulted in Chantier 5. The non-null legacy field is kept
# explicit rather than pretending that a regulatory rule set was applied.
CLAIM_DETECTION_RULEBOOK_VERSION = "not-applicable-deterministic-claims-v1"
INPUT_MANIFEST_SCHEMA_VERSION = "vericlaim-analysis-input-manifest-v1"
RESULT_SCHEMA_VERSION = "vericlaim-analysis-claim-detection-result-v1"
PROVENANCE_SCHEMA_VERSION = "vericlaim-claim-provenance-v1"

_ALLOWED_EXTRACTION_STATUSES = frozenset({ExtractionStatus.COMPLETED, ExtractionStatus.REVIEW_REQUIRED})


class AnalysisServiceError(RuntimeError):
    """Base error safe to map at the persistent-analysis API boundary."""


class AnalysisNotFoundError(AnalysisServiceError):
    pass


class AnalysisConflictError(AnalysisServiceError):
    pass


class AnalysisInputError(AnalysisServiceError):
    pass


class AnalysisIdempotencyConflictError(AnalysisConflictError):
    pass


class AnalysisDetectionTerminalError(AnalysisServiceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AnalysisDetectionRetryableError(AnalysisServiceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AnalysisInputDocument:
    version: DocumentVersion
    document: Document
    segment_count: int
    segments_sha256: str


@dataclass(frozen=True)
class AnalysisEnqueueResult:
    analysis: Analysis
    version: AnalysisVersion
    job: AnalysisDetectionJob
    idempotent_replay: bool


@dataclass(frozen=True)
class ClaimedAnalysisDetectionJob:
    id: UUID
    organization_id: UUID
    analysis_version_id: UUID
    worker_id: str
    attempt_count: int


@dataclass(frozen=True)
class AnalysisDetectionCompletion:
    analysis: Analysis
    version: AnalysisVersion
    job: AnalysisDetectionJob
    claim_count: int
    review_required_claim_count: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _require_worker_tenant_context(db: Session, organization_id: UUID) -> None:
    if db.info.get("current_organization_id") != organization_id:
        raise AnalysisConflictError("Le worker doit définir le contexte organisationnel avant de traiter un job.")


def _normalize_idempotency_key(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise AnalysisInputError("L’en-tête Idempotency-Key est obligatoire pour cette opération asynchrone.")
    if len(normalized) > 128 or any(ord(character) < 33 or ord(character) > 126 for character in normalized):
        raise AnalysisInputError("Idempotency-Key doit contenir entre 1 et 128 caractères ASCII imprimables sans espace.")
    return normalized


def _normalize_analysis_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 128 or any(ord(character) < 32 for character in normalized):
        raise AnalysisInputError("analysis_key doit contenir entre 1 et 128 caractères valides.")
    return normalized


def _initial_result_json(*, manifest: dict[str, Any], manifest_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "pipeline": "deterministic_claim_detection",
        "scope": "citeable_claim_detection_only",
        "input_manifest": manifest,
        "input_manifest_sha256": manifest_sha256,
        "limitations": [
            "Les résultats sont des détections lexicales déterministes de passages citables.",
            "Aucun verdict réglementaire, score juridique, rapprochement de preuve, recommandation, LLM ou RAG n’est produit.",
        ],
    }


def _get_analysis(
    db: Session,
    *,
    organization_id: UUID,
    analysis_id: UUID,
    lock: bool = False,
    include_deleted: bool = False,
) -> Analysis:
    statement = select(Analysis).where(Analysis.organization_id == organization_id, Analysis.id == analysis_id)
    if not include_deleted:
        statement = statement.where(Analysis.deleted_at.is_(None))
    if lock:
        statement = statement.with_for_update()
    analysis = db.scalar(statement)
    if analysis is None:
        raise AnalysisNotFoundError("Analyse introuvable ou inaccessible.")
    return analysis


def _get_analysis_version(
    db: Session,
    *,
    organization_id: UUID,
    analysis_version_id: UUID,
    lock: bool = False,
) -> AnalysisVersion:
    statement = select(AnalysisVersion).where(
        AnalysisVersion.organization_id == organization_id,
        AnalysisVersion.id == analysis_version_id,
    )
    if lock:
        statement = statement.with_for_update()
    version = db.scalar(statement)
    if version is None:
        raise AnalysisNotFoundError("Version d’analyse introuvable ou inaccessible.")
    return version


def get_analysis(db: Session, *, organization_id: UUID, analysis_id: UUID) -> Analysis:
    """Return a tenant-visible logical analysis dossier."""
    return _get_analysis(db, organization_id=organization_id, analysis_id=analysis_id)


def get_analysis_version_by_number(
    db: Session,
    *,
    organization_id: UUID,
    analysis_id: UUID,
    version_number: int,
) -> AnalysisVersion:
    _get_analysis(db, organization_id=organization_id, analysis_id=analysis_id)
    version = db.scalar(
        select(AnalysisVersion).where(
            AnalysisVersion.organization_id == organization_id,
            AnalysisVersion.analysis_id == analysis_id,
            AnalysisVersion.version_number == version_number,
        )
    )
    if version is None:
        raise AnalysisNotFoundError("Version d’analyse introuvable ou inaccessible.")
    return version


def get_claim(db: Session, *, organization_id: UUID, claim_id: UUID) -> Claim:
    claim = db.scalar(select(Claim).where(Claim.organization_id == organization_id, Claim.id == claim_id))
    if claim is None:
        raise AnalysisNotFoundError("Allégation introuvable ou inaccessible.")
    return claim


def list_analysis_versions(db: Session, *, organization_id: UUID, analysis_id: UUID) -> list[AnalysisVersion]:
    _get_analysis(db, organization_id=organization_id, analysis_id=analysis_id)
    return list(
        db.scalars(
            select(AnalysisVersion)
            .where(AnalysisVersion.organization_id == organization_id, AnalysisVersion.analysis_id == analysis_id)
            .order_by(AnalysisVersion.version_number.asc())
        ).all()
    )


def list_version_claims(db: Session, *, organization_id: UUID, analysis_version_id: UUID) -> list[Claim]:
    return list(
        db.scalars(
            select(Claim)
            .where(Claim.organization_id == organization_id, Claim.analysis_version_id == analysis_version_id)
            .order_by(Claim.created_at.asc(), Claim.id.asc())
        ).all()
    )


def get_analysis_detection_job(
    db: Session,
    *,
    organization_id: UUID,
    analysis_version_id: UUID,
) -> AnalysisDetectionJob | None:
    return db.scalar(
        select(AnalysisDetectionJob).where(
            AnalysisDetectionJob.organization_id == organization_id,
            AnalysisDetectionJob.analysis_version_id == analysis_version_id,
        )
    )


def _get_analysis_detection_job_for_version(
    db: Session,
    *,
    organization_id: UUID,
    analysis_version_id: UUID,
    lock: bool = False,
) -> AnalysisDetectionJob | None:
    statement = select(AnalysisDetectionJob).where(
        AnalysisDetectionJob.organization_id == organization_id,
        AnalysisDetectionJob.analysis_version_id == analysis_version_id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _segments_sha256(segments: Sequence[DocumentSegment]) -> str:
    """Anchor the exact C4 segment snapshot used as C5 lexical input."""
    return sha256_json(
        [
            {
                "id": str(segment.id),
                "sequence_number": segment.sequence_number,
                "page_number": segment.page_number,
                "segment_type": _enum_value(segment.segment_type),
                "text_sha256": hashlib.sha256(segment.text.encode("utf-8")).hexdigest(),
                "start_offset": segment.start_offset,
                "end_offset": segment.end_offset,
                "source_sha256": segment.source_sha256,
            }
            for segment in sorted(segments, key=lambda item: (item.sequence_number, str(item.id)))
        ]
    )


def _load_input_documents(
    db: Session,
    *,
    organization_id: UUID,
    document_version_ids: Sequence[UUID],
    supplier_id: UUID | None,
    product_id: UUID | None,
) -> list[AnalysisInputDocument]:
    if not document_version_ids:
        raise AnalysisInputError("Au moins une version documentaire extraite est requise.")
    if len(set(document_version_ids)) != len(document_version_ids):
        raise AnalysisInputError("Une version documentaire ne peut être fournie qu’une fois dans une analyse.")

    if supplier_id is not None:
        supplier = db.scalar(
            select(Supplier).where(
                Supplier.organization_id == organization_id,
                Supplier.id == supplier_id,
                Supplier.deleted_at.is_(None),
            )
        )
        if supplier is None:
            raise AnalysisInputError("Le fournisseur sélectionné est introuvable ou inaccessible.")
    if product_id is not None:
        product = db.scalar(
            select(Product).where(
                Product.organization_id == organization_id,
                Product.id == product_id,
                Product.deleted_at.is_(None),
            )
        )
        if product is None:
            raise AnalysisInputError("Le produit sélectionné est introuvable ou inaccessible.")
        if supplier_id is not None and product.supplier_id != supplier_id:
            raise AnalysisInputError("Le produit sélectionné n’appartient pas au fournisseur sélectionné.")

    rows = db.execute(
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.id.in_(document_version_ids),
            Document.organization_id == organization_id,
            Document.deleted_at.is_(None),
        )
    ).all()
    by_version_id = {row[0].id: (row[0], row[1]) for row in rows}
    missing = [version_id for version_id in document_version_ids if version_id not in by_version_id]
    if missing:
        raise AnalysisInputError("Une ou plusieurs versions documentaires sont introuvables ou inaccessibles.")

    version_ids = list(by_version_id)
    segments_by_version: dict[UUID, list[DocumentSegment]] = {version_id: [] for version_id in version_ids}
    for segment in db.scalars(
        select(DocumentSegment)
        .where(
            DocumentSegment.organization_id == organization_id,
            DocumentSegment.document_version_id.in_(version_ids),
        )
        .order_by(DocumentSegment.document_version_id.asc(), DocumentSegment.sequence_number.asc(), DocumentSegment.id.asc())
    ).all():
        segments_by_version.setdefault(segment.document_version_id, []).append(segment)

    inputs: list[AnalysisInputDocument] = []
    for version_id in sorted(version_ids, key=str):
        version, document = by_version_id[version_id]
        if version.extraction_status not in _ALLOWED_EXTRACTION_STATUSES:
            raise AnalysisInputError(
                "Chaque version documentaire doit avoir une extraction terminée ou nécessitant une revue avant analyse."
            )
        if not version.extracted_text_sha256:
            raise AnalysisInputError("La version documentaire ne possède pas de transcript extrait vérifiable.")
        if supplier_id is not None and document.supplier_id is not None and document.supplier_id != supplier_id:
            raise AnalysisInputError("Une version documentaire ne correspond pas au fournisseur sélectionné.")
        if product_id is not None and document.product_id is not None and document.product_id != product_id:
            raise AnalysisInputError("Une version documentaire ne correspond pas au produit sélectionné.")
        segments = segments_by_version.get(version.id, [])
        inputs.append(
            AnalysisInputDocument(
                version=version,
                document=document,
                segment_count=len(segments),
                segments_sha256=_segments_sha256(segments),
            )
        )
    return inputs


def _build_input_manifest(inputs: Sequence[AnalysisInputDocument]) -> dict[str, Any]:
    return {
        "schema_version": INPUT_MANIFEST_SCHEMA_VERSION,
        "pipeline": "deterministic_claim_detection",
        "engine_version": CLAIM_DETECTION_ENGINE_VERSION,
        "documents": [
            {
                "document_version_id": str(item.version.id),
                "document_id": str(item.document.id),
                "document_version_number": item.version.version_number,
                "source_filename": item.version.source_filename,
                "source_sha256": item.version.sha256,
                "extracted_text_sha256": item.version.extracted_text_sha256,
                "extraction_status": _enum_value(item.version.extraction_status),
                "extraction_engine_version": item.version.extraction_engine_version,
                "source_language": item.version.source_language,
                "segment_count": item.segment_count,
                "segments_sha256": item.segments_sha256,
            }
            for item in inputs
        ],
    }


def _request_sha256_for_create(
    *,
    analysis_key: str | None,
    supplier_id: UUID | None,
    product_id: UUID | None,
    document_version_ids: Sequence[UUID],
) -> str:
    return sha256_json(
        {
            "operation": "analysis_create",
            "analysis_key": analysis_key,
            "supplier_id": str(supplier_id) if supplier_id else None,
            "product_id": str(product_id) if product_id else None,
            "document_version_ids": sorted(str(value) for value in document_version_ids),
        }
    )


def _request_sha256_for_retry(*, analysis_id: UUID) -> str:
    return sha256_json({"operation": "analysis_retry", "analysis_id": str(analysis_id)})


def _find_idempotent_result(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    request_sha256: str,
) -> AnalysisEnqueueResult | None:
    version = db.scalar(
        select(AnalysisVersion).where(
            AnalysisVersion.organization_id == organization_id,
            AnalysisVersion.idempotency_key == idempotency_key,
        )
    )
    if version is None:
        return None
    if version.request_sha256 != request_sha256:
        raise AnalysisIdempotencyConflictError(
            "Cette Idempotency-Key a déjà été utilisée avec une demande d’analyse différente."
        )
    analysis = _get_analysis(
        db,
        organization_id=organization_id,
        analysis_id=version.analysis_id,
        include_deleted=True,
    )
    job = _get_analysis_detection_job_for_version(
        db,
        organization_id=organization_id,
        analysis_version_id=version.id,
    )
    if job is None:
        raise AnalysisConflictError("La version idempotente ne possède pas de job de détection traçable.")
    return AnalysisEnqueueResult(analysis=analysis, version=version, job=job, idempotent_replay=True)


def _next_version_number(db: Session, *, organization_id: UUID, analysis_id: UUID) -> int:
    last = db.scalar(
        select(func.max(AnalysisVersion.version_number)).where(
            AnalysisVersion.organization_id == organization_id,
            AnalysisVersion.analysis_id == analysis_id,
        )
    )
    return int(last or 0) + 1


def _enqueue_detection_job(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    version: AnalysisVersion,
    actor_user_id: UUID,
) -> AnalysisDetectionJob:
    job = AnalysisDetectionJob(
        id=uuid4(),
        organization_id=organization_id,
        analysis_version_id=version.id,
        requested_by_user_id=actor_user_id,
        status=AnalysisDetectionJobStatus.QUEUED,
        attempt_count=0,
        max_attempts=settings.analysis_detection_max_attempts,
        available_at=utcnow(),
    )
    version.detection_job = job
    db.add(job)
    return job


def _create_analysis_rows(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    actor_user_id: UUID,
    idempotency_key: str,
    request_sha256: str,
    inputs: Sequence[AnalysisInputDocument],
    supplier_id: UUID | None,
    product_id: UUID | None,
    analysis_key: str | None,
    request_id: str | None,
) -> AnalysisEnqueueResult:
    manifest = _build_input_manifest(inputs)
    manifest_sha256 = sha256_json(manifest)
    analysis = Analysis(
        id=uuid4(),
        organization_id=organization_id,
        supplier_id=supplier_id,
        product_id=product_id,
        analysis_key=analysis_key or f"analysis-{uuid4().hex}",
        status=AnalysisStatus.QUEUED,
        requested_by_user_id=actor_user_id,
    )
    version = AnalysisVersion(
        id=uuid4(),
        organization_id=organization_id,
        analysis_id=analysis.id,
        version_number=1,
        status=AnalysisStatus.QUEUED,
        engine_version=CLAIM_DETECTION_ENGINE_VERSION,
        rulebook_version=CLAIM_DETECTION_RULEBOOK_VERSION,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        input_manifest_sha256=manifest_sha256,
        # C5 persists no legal/risk conclusion and no numeric score.
        overall_risk_level=None,
        risk_score=None,
        result_json=_initial_result_json(manifest=manifest, manifest_sha256=manifest_sha256),
    )
    db.add(analysis)
    db.add(version)
    for item in inputs:
        db.add(
            AnalysisDocument(
                id=uuid4(),
                organization_id=organization_id,
                analysis_id=analysis.id,
                document_version_id=item.version.id,
                purpose="source",
                attached_by_user_id=actor_user_id,
            )
        )
    job = _enqueue_detection_job(
        db,
        settings=settings,
        organization_id=organization_id,
        version=version,
        actor_user_id=actor_user_id,
    )
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="analysis",
        entity_id=analysis.id,
        action="analysis.created",
        payload={
            "analysis_version_id": str(version.id),
            "analysis_key": analysis.analysis_key,
            "document_version_ids": [str(item.version.id) for item in inputs],
            "input_manifest_sha256": manifest_sha256,
            "scope": "citeable_claim_detection_only",
        },
        request_id=request_id,
    )
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="analysis_detection_job",
        entity_id=job.id,
        action="analysis.claim_detection_queued",
        payload={
            "analysis_id": str(analysis.id),
            "analysis_version_id": str(version.id),
            "max_attempts": job.max_attempts,
            "engine_version": CLAIM_DETECTION_ENGINE_VERSION,
            "rulebook_version": CLAIM_DETECTION_RULEBOOK_VERSION,
        },
        request_id=request_id,
    )
    return AnalysisEnqueueResult(analysis=analysis, version=version, job=job, idempotent_replay=False)


def create_analysis(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    actor_user_id: UUID,
    document_version_ids: Sequence[UUID],
    idempotency_key: str | None,
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    analysis_key: str | None = None,
    request_id: str | None = None,
) -> AnalysisEnqueueResult:
    """Transactionally persist an analysis/version/job, without doing detection."""
    normalized_key = _normalize_idempotency_key(idempotency_key)
    normalized_analysis_key = _normalize_analysis_key(analysis_key)
    request_sha256 = _request_sha256_for_create(
        analysis_key=normalized_analysis_key,
        supplier_id=supplier_id,
        product_id=product_id,
        document_version_ids=document_version_ids,
    )
    replay = _find_idempotent_result(
        db,
        organization_id=organization_id,
        idempotency_key=normalized_key,
        request_sha256=request_sha256,
    )
    if replay is not None:
        return replay

    inputs = _load_input_documents(
        db,
        organization_id=organization_id,
        document_version_ids=document_version_ids,
        supplier_id=supplier_id,
        product_id=product_id,
    )
    try:
        # The unique tenant/key constraint closes a concurrent replay race
        # without committing the caller’s request transaction independently.
        with db.begin_nested():
            return _create_analysis_rows(
                db,
                settings=settings,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                idempotency_key=normalized_key,
                request_sha256=request_sha256,
                inputs=inputs,
                supplier_id=supplier_id,
                product_id=product_id,
                analysis_key=normalized_analysis_key,
                request_id=request_id,
            )
    except IntegrityError as exc:
        replay = _find_idempotent_result(
            db,
            organization_id=organization_id,
            idempotency_key=normalized_key,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        raise AnalysisConflictError("Impossible de créer l’analyse en raison d’un conflit d’intégrité.") from exc


def _load_retry_inputs(
    db: Session,
    *,
    organization_id: UUID,
    analysis: Analysis,
) -> list[AnalysisInputDocument]:
    document_version_ids = list(
        db.scalars(
            select(AnalysisDocument.document_version_id)
            .where(
                AnalysisDocument.organization_id == organization_id,
                AnalysisDocument.analysis_id == analysis.id,
                AnalysisDocument.purpose == "source",
            )
            .order_by(AnalysisDocument.document_version_id.asc())
        ).all()
    )
    return _load_input_documents(
        db,
        organization_id=organization_id,
        document_version_ids=document_version_ids,
        supplier_id=analysis.supplier_id,
        product_id=analysis.product_id,
    )


def retry_analysis(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    actor_user_id: UUID,
    analysis_id: UUID,
    idempotency_key: str | None,
    request_id: str | None = None,
) -> AnalysisEnqueueResult:
    """Create a new immutable analysis version; never requeue an old result."""
    normalized_key = _normalize_idempotency_key(idempotency_key)
    request_sha256 = _request_sha256_for_retry(analysis_id=analysis_id)
    replay = _find_idempotent_result(
        db,
        organization_id=organization_id,
        idempotency_key=normalized_key,
        request_sha256=request_sha256,
    )
    if replay is not None:
        return replay

    try:
        with db.begin_nested():
            analysis = _get_analysis(db, organization_id=organization_id, analysis_id=analysis_id, lock=True)
            latest = db.scalar(
                select(AnalysisVersion)
                .where(AnalysisVersion.organization_id == organization_id, AnalysisVersion.analysis_id == analysis.id)
                .order_by(AnalysisVersion.version_number.desc())
                .limit(1)
                .with_for_update()
            )
            if latest is None:
                raise AnalysisConflictError("L’analyse ne possède aucune version relançable.")
            latest_job = _get_analysis_detection_job_for_version(
                db,
                organization_id=organization_id,
                analysis_version_id=latest.id,
                lock=True,
            )
            if latest.status in {AnalysisStatus.QUEUED, AnalysisStatus.DETECTING_CLAIMS} or (
                latest_job is not None and latest_job.status in {AnalysisDetectionJobStatus.QUEUED, AnalysisDetectionJobStatus.RUNNING}
            ):
                raise AnalysisConflictError("Une version de cette analyse est déjà en cours de détection.")

            inputs = _load_retry_inputs(db, organization_id=organization_id, analysis=analysis)
            manifest = _build_input_manifest(inputs)
            manifest_sha256 = sha256_json(manifest)
            version = AnalysisVersion(
                id=uuid4(),
                organization_id=organization_id,
                analysis_id=analysis.id,
                version_number=_next_version_number(db, organization_id=organization_id, analysis_id=analysis.id),
                status=AnalysisStatus.QUEUED,
                engine_version=CLAIM_DETECTION_ENGINE_VERSION,
                rulebook_version=CLAIM_DETECTION_RULEBOOK_VERSION,
                idempotency_key=normalized_key,
                request_sha256=request_sha256,
                input_manifest_sha256=manifest_sha256,
                overall_risk_level=None,
                risk_score=None,
                result_json=_initial_result_json(manifest=manifest, manifest_sha256=manifest_sha256),
            )
            db.add(version)
            analysis.status = AnalysisStatus.QUEUED
            analysis.completed_at = None
            job = _enqueue_detection_job(
                db,
                settings=settings,
                organization_id=organization_id,
                version=version,
                actor_user_id=actor_user_id,
            )
            db.flush()
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                entity_type="analysis",
                entity_id=analysis.id,
                action="analysis.version_retry_queued",
                payload={
                    "analysis_version_id": str(version.id),
                    "previous_analysis_version_id": str(latest.id),
                    "version_number": version.version_number,
                    "input_manifest_sha256": manifest_sha256,
                },
                request_id=request_id,
            )
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                entity_type="analysis_detection_job",
                entity_id=job.id,
                action="analysis.claim_detection_queued",
                payload={
                    "analysis_id": str(analysis.id),
                    "analysis_version_id": str(version.id),
                    "retry_of_analysis_version_id": str(latest.id),
                    "max_attempts": job.max_attempts,
                    "engine_version": CLAIM_DETECTION_ENGINE_VERSION,
                },
                request_id=request_id,
            )
            return AnalysisEnqueueResult(analysis=analysis, version=version, job=job, idempotent_replay=False)
    except IntegrityError as exc:
        replay = _find_idempotent_result(
            db,
            organization_id=organization_id,
            idempotency_key=normalized_key,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        raise AnalysisConflictError("Impossible de relancer l’analyse en raison d’un conflit d’intégrité.") from exc


def _set_current_analysis_state(
    db: Session,
    *,
    organization_id: UUID,
    analysis: Analysis,
    version: AnalysisVersion,
    status: AnalysisStatus,
    completed_at: datetime | None,
) -> None:
    latest_id = db.scalar(
        select(AnalysisVersion.id)
        .where(AnalysisVersion.organization_id == organization_id, AnalysisVersion.analysis_id == analysis.id)
        .order_by(AnalysisVersion.version_number.desc())
        .limit(1)
    )
    if latest_id == version.id:
        analysis.status = status
        analysis.completed_at = completed_at


def _failure_result_json(version: AnalysisVersion, *, error_code: str) -> dict[str, Any]:
    previous = dict(version.result_json or {})
    previous["processing_error"] = {"code": error_code}
    previous["state"] = "failed"
    return previous


def _recover_expired_analysis_detection_leases(
    db: Session,
    *,
    organization_id: UUID,
) -> None:
    now = utcnow()
    expired = list(
        db.scalars(
            select(AnalysisDetectionJob)
            .where(
                AnalysisDetectionJob.organization_id == organization_id,
                AnalysisDetectionJob.status == AnalysisDetectionJobStatus.RUNNING,
                AnalysisDetectionJob.lease_expires_at.is_not(None),
                AnalysisDetectionJob.lease_expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        ).all()
    )
    for job in expired:
        version = _get_analysis_version(
            db,
            organization_id=organization_id,
            analysis_version_id=job.analysis_version_id,
            lock=True,
        )
        analysis = _get_analysis(
            db,
            organization_id=organization_id,
            analysis_id=version.analysis_id,
            lock=True,
            include_deleted=True,
        )
        job.locked_at = None
        job.lease_expires_at = None
        job.worker_id = None
        job.error_code = "worker_lease_expired"
        if job.attempt_count < job.max_attempts:
            job.status = AnalysisDetectionJobStatus.QUEUED
            job.available_at = now
            version.status = AnalysisStatus.QUEUED
            _set_current_analysis_state(
                db,
                organization_id=organization_id,
                analysis=analysis,
                version=version,
                status=AnalysisStatus.QUEUED,
                completed_at=None,
            )
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=None,
                entity_type="analysis_detection_job",
                entity_id=job.id,
                action="analysis.claim_detection_lease_requeued",
                payload={"analysis_id": str(analysis.id), "analysis_version_id": str(version.id)},
            )
            continue

        job.status = AnalysisDetectionJobStatus.FAILED
        job.completed_at = now
        version.status = AnalysisStatus.FAILED
        version.completed_at = now
        version.result_json = _failure_result_json(version, error_code="worker_lease_expired")
        version.result_sha256 = sha256_json(version.result_json)
        _set_current_analysis_state(
            db,
            organization_id=organization_id,
            analysis=analysis,
            version=version,
            status=AnalysisStatus.FAILED,
            completed_at=now,
        )
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=None,
            entity_type="analysis_detection_job",
            entity_id=job.id,
            action="analysis.claim_detection_failed",
            payload={
                "analysis_id": str(analysis.id),
                "analysis_version_id": str(version.id),
                "error_code": "worker_lease_expired",
                "attempt_count": job.attempt_count,
            },
        )
    if expired:
        db.flush()


def claim_next_analysis_detection_job(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    worker_id: str,
) -> ClaimedAnalysisDetectionJob | None:
    """Atomically lease one due tenant-scoped analysis-detection job."""
    _require_worker_tenant_context(db, organization_id)
    _recover_expired_analysis_detection_leases(db, organization_id=organization_id)
    now = utcnow()
    job = db.scalar(
        select(AnalysisDetectionJob)
        .where(
            AnalysisDetectionJob.organization_id == organization_id,
            AnalysisDetectionJob.status == AnalysisDetectionJobStatus.QUEUED,
            AnalysisDetectionJob.available_at <= now,
        )
        .order_by(AnalysisDetectionJob.available_at.asc(), AnalysisDetectionJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    version = _get_analysis_version(
        db,
        organization_id=organization_id,
        analysis_version_id=job.analysis_version_id,
        lock=True,
    )
    analysis = _get_analysis(
        db,
        organization_id=organization_id,
        analysis_id=version.analysis_id,
        lock=True,
        include_deleted=True,
    )

    if version.status == AnalysisStatus.COMPLETED:
        job.status = AnalysisDetectionJobStatus.COMPLETED
        job.completed_at = now
        job.lease_expires_at = None
        job.worker_id = None
        db.flush()
        return None
    if version.status == AnalysisStatus.FAILED and job.attempt_count >= job.max_attempts:
        job.status = AnalysisDetectionJobStatus.FAILED
        job.completed_at = now
        db.flush()
        return None

    job.status = AnalysisDetectionJobStatus.RUNNING
    job.attempt_count += 1
    job.locked_at = now
    job.lease_expires_at = now + timedelta(seconds=settings.analysis_detection_lease_seconds)
    job.worker_id = worker_id
    job.started_at = job.started_at or now
    job.completed_at = None
    job.error_code = None
    version.status = AnalysisStatus.DETECTING_CLAIMS
    version.started_at = version.started_at or now
    _set_current_analysis_state(
        db,
        organization_id=organization_id,
        analysis=analysis,
        version=version,
        status=AnalysisStatus.DETECTING_CLAIMS,
        completed_at=None,
    )
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=None,
        entity_type="analysis_detection_job",
        entity_id=job.id,
        action="analysis.claim_detection_started",
        payload={
            "analysis_id": str(analysis.id),
            "analysis_version_id": str(version.id),
            "attempt_count": job.attempt_count,
            "lease_expires_at": job.lease_expires_at.isoformat(),
            "engine_version": version.engine_version,
        },
    )
    db.flush()
    return ClaimedAnalysisDetectionJob(
        id=job.id,
        organization_id=organization_id,
        analysis_version_id=version.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
    )


def _manifest_from_version(version: AnalysisVersion) -> dict[str, Any]:
    result = version.result_json or {}
    manifest = result.get("input_manifest") if isinstance(result, dict) else None
    if not isinstance(manifest, dict) or sha256_json(manifest) != version.input_manifest_sha256:
        raise AnalysisDetectionTerminalError(
            "input_manifest_integrity_invalid",
            "Le manifeste d’entrée de la version d’analyse ne correspond pas à son empreinte.",
        )
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise AnalysisDetectionTerminalError(
            "input_manifest_invalid",
            "Le manifeste d’entrée ne contient aucune version documentaire exploitable.",
        )
    return manifest


def _load_manifest_document_versions(
    db: Session,
    *,
    organization_id: UUID,
    manifest: dict[str, Any],
) -> list[tuple[dict[str, Any], DocumentVersion]]:
    entries = manifest["documents"]
    parsed_ids: list[UUID] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise AnalysisDetectionTerminalError("input_manifest_invalid", "Le manifeste d’entrée est invalide.")
        try:
            parsed_ids.append(UUID(str(entry["document_version_id"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalysisDetectionTerminalError("input_manifest_invalid", "Le manifeste d’entrée est invalide.") from exc
    if len(set(parsed_ids)) != len(parsed_ids):
        raise AnalysisDetectionTerminalError("input_manifest_invalid", "Le manifeste contient des versions dupliquées.")

    versions = list(
        db.scalars(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.id.in_(parsed_ids),
            )
        ).all()
    )
    by_id = {version.id: version for version in versions}
    if len(by_id) != len(parsed_ids):
        raise AnalysisDetectionTerminalError(
            "input_document_missing",
            "Une version documentaire du manifeste n’est plus accessible.",
        )

    result: list[tuple[dict[str, Any], DocumentVersion]] = []
    for entry, version_id in zip(entries, parsed_ids, strict=True):
        version = by_id[version_id]
        if version.extraction_status not in _ALLOWED_EXTRACTION_STATUSES:
            raise AnalysisDetectionTerminalError(
                "input_extraction_not_ready",
                "Une version documentaire n’est plus dans un état d’extraction analysable.",
            )
        if (
            entry.get("document_id") != str(version.document_id)
            or entry.get("source_sha256") != version.sha256
            or entry.get("extracted_text_sha256") != version.extracted_text_sha256
        ):
            raise AnalysisDetectionTerminalError(
                "input_document_manifest_mismatch",
                "Les empreintes de la version documentaire ne correspondent plus au manifeste d’entrée.",
            )
        result.append((entry, version))
    return result


def _segment_type_is_ocr(segment: DocumentSegment) -> bool:
    return segment.segment_type == SegmentType.IMAGE_OCR or _enum_value(segment.segment_type) == SegmentType.IMAGE_OCR.value


def _provenance_for_fact(
    *,
    segment: DocumentSegment,
    document_version: DocumentVersion,
    fact: Any,
) -> tuple[int, int, dict[str, Any]]:
    segment_start = segment.start_offset
    sentence_start = fact.start_offset
    sentence_end = fact.end_offset
    document_start = segment_start + sentence_start if segment_start is not None else sentence_start
    document_end = segment_start + sentence_end if segment_start is not None else sentence_end
    trigger_document_start = (
        segment_start + fact.trigger_start_offset if segment_start is not None else fact.trigger_start_offset
    )
    trigger_document_end = segment_start + fact.trigger_end_offset if segment_start is not None else fact.trigger_end_offset
    return (
        document_start,
        document_end,
        {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "detector_claim_id": fact.claim_id,
            "detection_method": fact.detection_method,
            "trigger_text": fact.trigger_text,
            "affirmative": fact.affirmative,
            "negation_cue": fact.negation_cue,
            "has_offsetting_signal": fact.has_offsetting_signal,
            "has_specific_qualifier": fact.has_specific_qualifier,
            "numeric_value": str(fact.numeric_value) if fact.numeric_value is not None else None,
            "numeric_unit": fact.numeric_unit,
            "sentence_start_offset_in_segment": sentence_start,
            "sentence_end_offset_in_segment": sentence_end,
            "trigger_start_offset_in_segment": fact.trigger_start_offset,
            "trigger_end_offset_in_segment": fact.trigger_end_offset,
            "sentence_start_offset_in_document": document_start,
            "sentence_end_offset_in_document": document_end,
            "trigger_start_offset_in_document": trigger_document_start,
            "trigger_end_offset_in_document": trigger_document_end,
            "document_version_id": str(document_version.id),
            "document_id": str(document_version.document_id),
            "document_source_sha256": document_version.sha256,
            "segment_id": str(segment.id),
            "segment_sequence_number": segment.sequence_number,
            "page_number": segment.page_number,
            "segment_type": _enum_value(segment.segment_type),
            "segment_start_offset_in_document": segment.start_offset,
            "segment_end_offset_in_document": segment.end_offset,
            "segment_source_sha256": segment.source_sha256,
            "source_is_ocr": _segment_type_is_ocr(segment),
        },
    )


def _detect_claim_records(
    db: Session,
    *,
    organization_id: UUID,
    analysis_version_id: UUID,
    manifest_documents: Sequence[tuple[dict[str, Any], DocumentVersion]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    extractor = FactExtractor()
    claim_records: list[dict[str, Any]] = []
    document_summaries: list[dict[str, Any]] = []

    for manifest_entry, document_version in manifest_documents:
        segments = list(
            db.scalars(
                select(DocumentSegment)
                .where(
                    DocumentSegment.organization_id == organization_id,
                    DocumentSegment.document_version_id == document_version.id,
                )
                .order_by(DocumentSegment.sequence_number.asc(), DocumentSegment.id.asc())
            ).all()
        )
        expected_segment_count = manifest_entry.get("segment_count")
        if not isinstance(expected_segment_count, int) or expected_segment_count < 0 or len(segments) != expected_segment_count:
            raise AnalysisDetectionTerminalError(
                "input_segment_manifest_mismatch",
                "Les segments du document ne correspondent plus au manifeste d’entrée.",
            )
        if manifest_entry.get("segments_sha256") != _segments_sha256(segments):
            raise AnalysisDetectionTerminalError(
                "input_segment_manifest_mismatch",
                "L’empreinte des segments ne correspond plus au manifeste d’entrée.",
            )
        document_claim_count = 0
        document_review_count = 0
        for segment in segments:
            if segment.source_sha256 != document_version.sha256:
                raise AnalysisDetectionTerminalError(
                    "segment_source_mismatch",
                    "L’empreinte d’un segment ne correspond pas à sa version documentaire source.",
                )
            for fact in extractor.extract(segment.text):
                start_offset, end_offset, provenance = _provenance_for_fact(
                    segment=segment,
                    document_version=document_version,
                    fact=fact,
                )
                status = ClaimStatus.REVIEW_REQUIRED if _segment_type_is_ocr(segment) else ClaimStatus.DETECTED
                record = {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "analysis_version_id": analysis_version_id,
                    "document_segment_id": segment.id,
                    "claim_type": _enum_value(fact.claim_type),
                    "category": _enum_value(fact.claim_type),
                    "claim_text": fact.claim_text,
                    "normalized_text": None,
                    "language": document_version.source_language,
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                    "source": ClaimSource.DETERMINISTIC,
                    # Lexical detection is not probabilistic; no invented score.
                    "confidence_score": None,
                    "status": status,
                    "detector_version": CLAIM_DETECTION_ENGINE_VERSION,
                    "attributes_json": provenance,
                }
                claim_records.append(record)
                document_claim_count += 1
                if status == ClaimStatus.REVIEW_REQUIRED:
                    document_review_count += 1
        document_summaries.append(
            {
                "document_version_id": str(document_version.id),
                "document_id": str(document_version.document_id),
                "source_sha256": document_version.sha256,
                "segment_count": len(segments),
                "claim_count": document_claim_count,
                "review_required_claim_count": document_review_count,
            }
        )
    return claim_records, document_summaries


def _result_claim_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record["id"]),
        "claim_type": record["claim_type"],
        "category": record["category"],
        "claim_text": record["claim_text"],
        "language": record["language"],
        "start_offset": record["start_offset"],
        "end_offset": record["end_offset"],
        "source": _enum_value(record["source"]),
        "confidence_score": None,
        "status": _enum_value(record["status"]),
        "detector_version": record["detector_version"],
        "document_segment_id": str(record["document_segment_id"]),
        "attributes": record["attributes_json"],
    }


def _completed_result_json(
    *,
    version: AnalysisVersion,
    manifest: dict[str, Any],
    claim_records: Sequence[dict[str, Any]],
    document_summaries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    review_required_count = sum(1 for record in claim_records if record["status"] == ClaimStatus.REVIEW_REQUIRED)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "pipeline": "deterministic_claim_detection",
        "scope": "citeable_claim_detection_only",
        "input_manifest": manifest,
        "input_manifest_sha256": version.input_manifest_sha256,
        "claim_count": len(claim_records),
        "review_required_claim_count": review_required_count,
        "documents": list(document_summaries),
        "claims": [_result_claim_record(record) for record in claim_records],
        "limitations": [
            "Les résultats sont des détections lexicales déterministes de passages citables.",
            "Une allégation issue d’un segment OCR est marquée review_required et doit être comparée au document original.",
            "Aucun verdict réglementaire, score juridique, rapprochement de preuve, recommandation, LLM ou RAG n’est produit.",
        ],
    }


def process_claimed_analysis_detection(
    db: Session,
    *,
    claim: ClaimedAnalysisDetectionJob,
) -> AnalysisDetectionCompletion:
    """Detect claims from persisted C4 segments, then atomically publish once."""
    _require_worker_tenant_context(db, claim.organization_id)
    job = db.scalar(
        select(AnalysisDetectionJob).where(
            AnalysisDetectionJob.organization_id == claim.organization_id,
            AnalysisDetectionJob.id == claim.id,
        )
    )
    if job is None:
        raise AnalysisNotFoundError("Job de détection introuvable.")
    if job.status != AnalysisDetectionJobStatus.RUNNING or job.worker_id != claim.worker_id:
        raise AnalysisConflictError("Le lease du job de détection n’est plus détenu par ce worker.")
    if job.lease_expires_at is None or _as_utc(job.lease_expires_at) <= utcnow():
        raise AnalysisConflictError("Le lease du job de détection a expiré avant son traitement.")

    version = _get_analysis_version(
        db,
        organization_id=claim.organization_id,
        analysis_version_id=job.analysis_version_id,
    )
    manifest = _manifest_from_version(version)
    manifest_documents = _load_manifest_document_versions(
        db,
        organization_id=claim.organization_id,
        manifest=manifest,
    )
    claim_records, document_summaries = _detect_claim_records(
        db,
        organization_id=claim.organization_id,
        analysis_version_id=version.id,
        manifest_documents=manifest_documents,
    )
    return _publish_claim_detection_result(
        db,
        claim=claim,
        manifest=manifest,
        claim_records=claim_records,
        document_summaries=document_summaries,
    )


def _publish_claim_detection_result(
    db: Session,
    *,
    claim: ClaimedAnalysisDetectionJob,
    manifest: dict[str, Any],
    claim_records: Sequence[dict[str, Any]],
    document_summaries: Sequence[dict[str, Any]],
) -> AnalysisDetectionCompletion:
    # Re-lock at publication so a worker which exceeded its lease cannot
    # overwrite a reclaimed attempt’s immutable result.
    job = db.scalar(
        select(AnalysisDetectionJob)
        .where(
            AnalysisDetectionJob.organization_id == claim.organization_id,
            AnalysisDetectionJob.id == claim.id,
        )
        .with_for_update()
    )
    if job is None:
        raise AnalysisNotFoundError("Job de détection introuvable.")
    if job.status != AnalysisDetectionJobStatus.RUNNING or job.worker_id != claim.worker_id:
        raise AnalysisConflictError("Le lease du job de détection n’est plus détenu par ce worker.")
    if job.lease_expires_at is None or _as_utc(job.lease_expires_at) <= utcnow():
        raise AnalysisConflictError("Le lease du job de détection a expiré avant la publication.")

    version = _get_analysis_version(
        db,
        organization_id=claim.organization_id,
        analysis_version_id=job.analysis_version_id,
        lock=True,
    )
    analysis = _get_analysis(
        db,
        organization_id=claim.organization_id,
        analysis_id=version.analysis_id,
        lock=True,
        include_deleted=True,
    )
    existing_claim_count = db.scalar(
        select(func.count())
        .select_from(Claim)
        .where(Claim.organization_id == claim.organization_id, Claim.analysis_version_id == version.id)
    )
    if existing_claim_count:
        raise AnalysisDetectionTerminalError(
            "claims_already_published",
            "Des allégations existent déjà pour cette version immuable; la publication est refusée.",
        )

    now = utcnow()
    for record in claim_records:
        db.add(Claim(**record))
    result_json = _completed_result_json(
        version=version,
        manifest=manifest,
        claim_records=claim_records,
        document_summaries=document_summaries,
    )
    version.result_json = result_json
    version.result_sha256 = sha256_json(result_json)
    version.status = AnalysisStatus.COMPLETED
    version.completed_at = now
    job.status = AnalysisDetectionJobStatus.COMPLETED
    job.completed_at = now
    job.lease_expires_at = None
    job.error_code = None
    _set_current_analysis_state(
        db,
        organization_id=claim.organization_id,
        analysis=analysis,
        version=version,
        status=AnalysisStatus.COMPLETED,
        completed_at=now,
    )
    db.flush()
    review_required_count = sum(1 for record in claim_records if record["status"] == ClaimStatus.REVIEW_REQUIRED)
    append_audit_event(
        db,
        organization_id=claim.organization_id,
        actor_user_id=None,
        entity_type="analysis_detection_job",
        entity_id=job.id,
        action="analysis.claim_detection_completed",
        payload={
            "analysis_id": str(analysis.id),
            "analysis_version_id": str(version.id),
            "attempt_count": job.attempt_count,
            "claim_count": len(claim_records),
            "review_required_claim_count": review_required_count,
            "result_sha256": version.result_sha256,
            "input_manifest_sha256": version.input_manifest_sha256,
            "engine_version": version.engine_version,
        },
    )
    db.flush()
    return AnalysisDetectionCompletion(
        analysis=analysis,
        version=version,
        job=job,
        claim_count=len(claim_records),
        review_required_claim_count=review_required_count,
    )


def record_analysis_detection_failure(
    db: Session,
    *,
    settings: Settings,
    claim: ClaimedAnalysisDetectionJob,
    error_code: str,
    retryable: bool,
) -> bool:
    """Record a safe code and schedule a bounded retry when appropriate."""
    _require_worker_tenant_context(db, claim.organization_id)
    job = db.scalar(
        select(AnalysisDetectionJob)
        .where(
            AnalysisDetectionJob.organization_id == claim.organization_id,
            AnalysisDetectionJob.id == claim.id,
        )
        .with_for_update()
    )
    if job is None or job.status != AnalysisDetectionJobStatus.RUNNING or job.worker_id != claim.worker_id:
        return False
    version = _get_analysis_version(
        db,
        organization_id=claim.organization_id,
        analysis_version_id=job.analysis_version_id,
        lock=True,
    )
    analysis = _get_analysis(
        db,
        organization_id=claim.organization_id,
        analysis_id=version.analysis_id,
        lock=True,
        include_deleted=True,
    )
    now = utcnow()
    job.error_code = error_code
    job.locked_at = None
    job.lease_expires_at = None
    job.worker_id = None

    if retryable and job.attempt_count < job.max_attempts:
        delay_seconds = min(
            settings.analysis_detection_retry_base_seconds * (2 ** max(0, job.attempt_count - 1)),
            60 * 60,
        )
        job.status = AnalysisDetectionJobStatus.QUEUED
        job.available_at = now + timedelta(seconds=delay_seconds)
        version.status = AnalysisStatus.QUEUED
        _set_current_analysis_state(
            db,
            organization_id=claim.organization_id,
            analysis=analysis,
            version=version,
            status=AnalysisStatus.QUEUED,
            completed_at=None,
        )
        append_audit_event(
            db,
            organization_id=claim.organization_id,
            actor_user_id=None,
            entity_type="analysis_detection_job",
            entity_id=job.id,
            action="analysis.claim_detection_retry_scheduled",
            payload={
                "analysis_id": str(analysis.id),
                "analysis_version_id": str(version.id),
                "error_code": error_code,
                "attempt_count": job.attempt_count,
                "available_at": job.available_at.isoformat(),
            },
        )
        db.flush()
        return True

    job.status = AnalysisDetectionJobStatus.FAILED
    job.completed_at = now
    version.status = AnalysisStatus.FAILED
    version.completed_at = now
    version.result_json = _failure_result_json(version, error_code=error_code)
    version.result_sha256 = sha256_json(version.result_json)
    _set_current_analysis_state(
        db,
        organization_id=claim.organization_id,
        analysis=analysis,
        version=version,
        status=AnalysisStatus.FAILED,
        completed_at=now,
    )
    append_audit_event(
        db,
        organization_id=claim.organization_id,
        actor_user_id=None,
        entity_type="analysis_detection_job",
        entity_id=job.id,
        action="analysis.claim_detection_failed",
        payload={
            "analysis_id": str(analysis.id),
            "analysis_version_id": str(version.id),
            "error_code": error_code,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
        },
    )
    db.flush()
    return False
