"""Durable, tenant-scoped deterministic analysis orchestration.

Pipeline v2 detects citeable lexical findings from the immutable document
segments, then runs the deterministic rule engine over **those persisted
claims** and stores one immutable verdict row per (claim, rule) pair. No text is
re-extracted and no claim is re-detected at evaluation time, so a verdict always
describes the version it is attached to.

The engine is deterministic and rule-driven: no LLM, no RAG, no probabilistic
scoring. ``ANALYSIS_ENGINE_VERSION`` and ``RULEBOOK_VERSION`` are recorded on
every verdict so a decision can be replayed years later.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analyses.evidence_registry import EvidenceSelection, select_evidence_for_analysis
from app.core.config import Settings
from app.core.database import sha256_json
from app.engine.detection_confidence import (
    CONFIDENCE_BASIS,
    CONFIDENCE_RUBRIC_VERSION,
    POLARITY_CONFLICT_REASON,
    VERDICT_REVIEW_REASON,
    DetectionConfidenceLevel,
    polarity_conflicts,
    score_claim,
    with_review_reason,
)
from app.engine.fact_extractor import FactExtractor
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.risk_assessment import (
    build_exposure_matrix,
    derive_overall_status,
    derive_risk_score,
)
from app.engine.lexicon import LEXICON_VERSION
from app.engine.rule_book import RULEBOOK_VERSION
from app.identity.service import append_audit_event
from app.models.legal_types import (
    ClaimType as EngineClaimType,
    DetectedClaim,
    LegalAssessment,
    Verdict,
)
from app.models.schemas import AuditContext, paris_today
from app.models.domain import (
    Analysis,
    AnalysisDetectionJob,
    AnalysisVerdict,
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
# The version recorded on a version row and on every verdict it produced.
ANALYSIS_ENGINE_VERSION = "vericlaim-analysis-engine-v2"
# Retained only so a pre-v2 row stays readable. A v2 version records the real
# Rule Book fingerprint, which C6 requires in the report.
CLAIM_DETECTION_RULEBOOK_VERSION = "not-applicable-deterministic-claims-v1"
INPUT_MANIFEST_SCHEMA_VERSION = "vericlaim-analysis-input-manifest-v1"
RESULT_SCHEMA_VERSION = "vericlaim-analysis-verdicts-result-v2"
LEGACY_RESULT_SCHEMA_VERSION = "vericlaim-analysis-claim-detection-result-v1"
PROVENANCE_SCHEMA_VERSION = "vericlaim-claim-provenance-v1"
# The pipeline that produced a result. Used to refuse rendering a v1 version as
# if it carried a regulatory conclusion.
PIPELINE_V2 = "claim_detection_and_regulatory_verdicts"
PIPELINE_V1 = "deterministic_claim_detection"
SCOPE_V2 = "claim_detection_and_regulatory_verdicts"

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
        "pipeline": PIPELINE_V2,
        "scope": SCOPE_V2,
        "input_manifest": manifest,
        "input_manifest_sha256": manifest_sha256,
        "limitations": [
            "Les résultats sont des détections lexicales déterministes et des verdicts issus d’un Rule Book versionné.",
            "Aucune donnée n’est produite par un modèle de langage, un RAG ou un score probabiliste.",
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
        "pipeline": PIPELINE_V2,
        "engine_version": ANALYSIS_ENGINE_VERSION,
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
    # The evaluation context is frozen here, at creation, not when the worker
    # runs: the audit date is an input to the rule engine, so leaving it to the
    # worker would make the same analysis answer differently depending on when a
    # queue happened to drain.
    context_json = _evaluation_context(analysis_id=analysis.id, as_of_date=paris_today())
    version = AnalysisVersion(
        id=uuid4(),
        organization_id=organization_id,
        analysis_id=analysis.id,
        version_number=1,
        status=AnalysisStatus.QUEUED,
        engine_version=ANALYSIS_ENGINE_VERSION,
        rulebook_version=RULEBOOK_VERSION,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        input_manifest_sha256=manifest_sha256,
        evaluation_context_json=context_json,
        # Deliberately unset until the worker publishes verdicts: NULL means
        # "no conclusion exists", which is not the same as "compliant".
        overall_compliance=None,
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
            "scope": SCOPE_V2,
            "engine_version": ANALYSIS_ENGINE_VERSION,
            "rulebook_version": RULEBOOK_VERSION,
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
            "engine_version": ANALYSIS_ENGINE_VERSION,
            "rulebook_version": RULEBOOK_VERSION,
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
                engine_version=ANALYSIS_ENGINE_VERSION,
                rulebook_version=RULEBOOK_VERSION,
                idempotency_key=normalized_key,
                request_sha256=request_sha256,
                input_manifest_sha256=manifest_sha256,
                evaluation_context_json=_evaluation_context(
                    analysis_id=analysis.id, as_of_date=paris_today()
                ),
                overall_compliance=None,
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
                    "engine_version": ANALYSIS_ENGINE_VERSION,
                    "rulebook_version": RULEBOOK_VERSION,
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


def _citation_cell(
    *,
    segment: DocumentSegment,
    trigger_start: int | None,
    trigger_end: int | None,
    document_start: int,
    document_end: int,
) -> dict[str, Any] | None:
    """C23 — la cellule du tableau où tombe l'allégation, si le segment en est un.

    Une allégation détectée dans un tableau était citée « bloc entier » : la ligne
    `Taux de matière recyclée | 62 | %` était un seul segment de texte, et le relecteur
    recevait les 4 lignes du tableau comme citation.

    On cherche la cellule **du déclencheur** d'abord : c'est lui qui fait l'allégation
    (« matière recyclée » dans la colonne critère), et non la phrase entière — mesuré
    ici, la phrase d'une ligne de tableau est la ligne, qui ne tient dans aucune cellule.
    À défaut, on retient la cellule qui contient le début de la citation, puis celle qui
    la recouvre le plus. Si rien ne correspond, on ne publie rien : le produit ne
    rattache pas une allégation à une cellule choisie au hasard.

    Les offsets publiés de l'allégation (`sentence_start_offset_in_document`) ne sont
    pas modifiés : ils désignent toujours le texte canonique, et l'empreinte du manifeste
    d'entrée ne dépend pas de cette lecture. La cellule est une **lecture en plus**.
    """

    payload = getattr(segment, "bounding_box_json", None)
    if not isinstance(payload, dict) or payload.get("kind") != "structured_table":
        return None

    probes: list[tuple[int, int]] = []
    if isinstance(trigger_start, int) and isinstance(trigger_end, int):
        probes.append((trigger_start, trigger_end))
    probes.append((document_start, document_end))

    cell_match: dict[str, Any] | None = None
    for span_start, span_end in probes:
        best: tuple[int, dict[str, Any]] | None = None
        for row in payload.get("rows", []):
            if not isinstance(row, dict) or row.get("is_header"):
                continue
            for cell in row.get("cells", []):
                if not isinstance(cell, dict) or not cell.get("text"):
                    continue
                start = cell.get("start_offset")
                end = cell.get("end_offset")
                if not isinstance(start, int) or not isinstance(end, int):
                    continue
                if start <= span_start and span_end <= end:
                    overlap = end - start
                elif start <= span_start < end:
                    overlap = end - span_start
                elif start < span_end <= end:
                    overlap = span_end - start
                else:
                    continue
                if best is None or overlap > best[0]:
                    best = (
                        overlap,
                        {
                            "row_index": row.get("index"),
                            "column_index": cell.get("column_index"),
                            "role": cell.get("role"),
                            "text": cell.get("text"),
                            "start_offset": start,
                            "end_offset": end,
                            "row_start_offset": row.get("start_offset"),
                            "row_end_offset": row.get("end_offset"),
                        },
                    )
        if best is not None:
            cell_match = best[1]
            break
    if cell_match is None:
        return None
    entries = payload.get("entries") or []
    row_index = cell_match.get("row_index")
    entry = next(
        (item for item in entries if isinstance(item, dict) and item.get("row_index") == row_index),
        None,
    )
    if entry is not None:
        entry = {
            "criterion": entry.get("criterion"),
            "value": entry.get("value"),
            "numeric_value": entry.get("numeric_value"),
            "unit": entry.get("unit"),
            "row_index": entry.get("row_index"),
        }
    return {
        # Les clés publiées sont celles du schéma d'API : ce qui est stocké est ce que
        # le client reçoit, sans traduction intermédiaire qui pourrait diverger.
        "page_number": payload.get("page_number"),
        "columns": list(payload.get("columns") or []),
        "has_header": bool(payload.get("has_header")),
        "cell": cell_match,
        "row_entry": entry,
    }


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
            # C23 — présent seulement quand la citation tombe dans un tableau lu.
            "table_citation": _citation_cell(
                segment=segment,
                trigger_start=trigger_document_start,
                trigger_end=trigger_document_end,
                document_start=document_start,
                document_end=document_end,
            ),
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
        extraction_review_required = (
            _enum_value(document_version.extraction_status) == ExtractionStatus.REVIEW_REQUIRED.value
        )
        for segment in segments:
            if segment.source_sha256 != document_version.sha256:
                raise AnalysisDetectionTerminalError(
                    "segment_source_mismatch",
                    "L’empreinte d’un segment ne correspond pas à sa version documentaire source.",
                )
            source_is_ocr = _segment_type_is_ocr(segment)
            facts = extractor.extract(segment.text)
            # A claim asserted and denied in the same segment is a genuine
            # editorial pattern; it must not be collapsed into one reading.
            conflicting_types = polarity_conflicts(
                [(_enum_value(fact.claim_type), bool(fact.affirmative)) for fact in facts]
            )
            for fact in facts:
                start_offset, end_offset, provenance = _provenance_for_fact(
                    segment=segment,
                    document_version=document_version,
                    fact=fact,
                )
                # Deterministic rubric, published with its factors: the caller
                # can see why a detection is solid or fragile. Never a model.
                confidence = score_claim(
                    fact,
                    source_is_ocr=source_is_ocr,
                    extraction_review_required=extraction_review_required,
                )
                if _enum_value(fact.claim_type) in conflicting_types:
                    confidence = with_review_reason(confidence, POLARITY_CONFLICT_REASON)
                provenance["detection_confidence"] = confidence.as_dict()
                status = (
                    ClaimStatus.REVIEW_REQUIRED
                    if confidence.requires_human_review
                    else ClaimStatus.DETECTED
                )
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
                    # A rubric score, not a statistical confidence: it is
                    # computed from features of the text and it carries its
                    # factors in ``attributes_json.detection_confidence``.
                    "confidence_score": confidence.score,
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


def _evaluation_context(*, analysis_id: UUID, as_of_date: date) -> dict[str, Any]:
    """The frozen inputs of the evaluation, persisted with the verdicts.

    The date and the transposition status are inputs to the rule engine: the same
    claim can be ``UPCOMING`` or ``NON_COMPLIANT`` depending on them. Persisting
    them is what allows a replayed analysis to be compared honestly.
    """
    return {
        "schema_version": "vericlaim-evaluation-context-v1",
        "as_of_date": as_of_date.isoformat(),
        "jurisdiction": "FR",
        "surface": "packaging",
        "consumer_facing": True,
        "product_identifier": str(analysis_id),
        "product_category": None,
        "operation_spend_eur": None,
        "context_source": "analysis_creation",
        "rulebook_version": RULEBOOK_VERSION,
        "engine_version": ANALYSIS_ENGINE_VERSION,
    }


def _audit_context_from_persisted(context_json: dict[str, Any] | None) -> AuditContext:
    """Rebuild the audit context from the stored JSON, never from today's date."""
    payload = dict(context_json or {})
    raw_date = payload.get("as_of_date")
    try:
        as_of = date.fromisoformat(str(raw_date))
    except (TypeError, ValueError):
        raise AnalysisDetectionTerminalError(
            "evaluation_context_missing",
            "Le contexte d’évaluation persisté est absent ou illisible; aucune évaluation n’est possible.",
        )
    return AuditContext(
        as_of_date=as_of,
        jurisdiction=str(payload.get("jurisdiction") or "FR"),
        surface=payload.get("surface") or "packaging",
        consumer_facing=bool(payload.get("consumer_facing", True)),
        product_identifier=payload.get("product_identifier"),
        product_category=payload.get("product_category"),
        operation_spend_eur=payload.get("operation_spend_eur"),
    )


def _detected_claim_from_persisted(record: dict[str, Any]) -> DetectedClaim:
    """Rebuild the exact claim the extractor produced, from the stored row.

    Every field the evaluator reads is persisted: the claim row holds the text,
    the type and the offsets, and the provenance attributes hold the trigger and
    the linguistic flags. Nothing is re-detected and nothing is guessed — an
    incomplete provenance refuses to evaluate rather than fabricating defaults
    that would change the verdict.
    """
    provenance = record.get("attributes_json") or {}
    missing = [
        field
        for field in ("trigger_text", "affirmative", "has_offsetting_signal", "has_specific_qualifier")
        if field not in provenance
    ]
    if missing:
        raise AnalysisDetectionTerminalError(
            "claim_provenance_incomplete",
            "La provenance persistée d’une allégation est incomplète; l’évaluation est refusée.",
        )
    try:
        claim_type = EngineClaimType(record["claim_type"])
    except ValueError as exc:
        raise AnalysisDetectionTerminalError(
            "claim_type_unknown",
            f"Type d’allégation non reconnu par le moteur: {record['claim_type']!r}.",
        ) from exc

    numeric_raw = provenance.get("numeric_value")
    numeric_value = None
    if numeric_raw is not None:
        try:
            numeric_value = Decimal(str(numeric_raw))
        except InvalidOperation as exc:
            raise AnalysisDetectionTerminalError(
                "claim_numeric_value_invalid",
                "Une valeur numérique persistée n’est pas exploitable; l’évaluation est refusée.",
            ) from exc

    return DetectedClaim(
        claim_id=str(record["id"]),
        claim_text=record["claim_text"],
        trigger_text=str(provenance["trigger_text"]),
        claim_type=claim_type,
        start_offset=int(record["start_offset"] or 0),
        end_offset=int(record["end_offset"] or 0),
        trigger_start_offset=int(provenance.get("trigger_start_offset_in_document") or 0),
        trigger_end_offset=int(provenance.get("trigger_end_offset_in_document") or 0),
        affirmative=bool(provenance["affirmative"]),
        negation_cue=provenance.get("negation_cue"),
        has_offsetting_signal=bool(provenance["has_offsetting_signal"]),
        has_specific_qualifier=bool(provenance["has_specific_qualifier"]),
        numeric_value=numeric_value,
        numeric_unit=provenance.get("numeric_unit"),
        detection_method=str(provenance.get("detection_method") or "DETERMINISTIC_LEXICON"),
    )


def _evaluate_persisted_claims(
    *,
    claim_records: Sequence[dict[str, Any]],
    selection: EvidenceSelection,
    context: AuditContext,
    settings: Settings,
) -> list[tuple[dict[str, Any], LegalAssessment]]:
    """Run the rule engine over stored claims, in a stable order.

    Ordering is ``(analysis order of the claim, rule priority, rule_id)``, which
    is fully determined by the persisted rows and the Rule Book fingerprint.
    """
    evaluator = InferenceEvaluator(
        fr_2024_825_transposition_status=settings.eu_2024_825_fr_transposition_status,
    )
    results: list[tuple[dict[str, Any], LegalAssessment]] = []
    for record in claim_records:
        claim = _detected_claim_from_persisted(record)
        findings = evaluator.evaluate_claim_all(claim, selection.dossier, context)
        if not findings:
            raise AnalysisDetectionTerminalError(
                "no_rule_for_claim_type",
                f"Aucune règle du Rule Book ne couvre le type {claim.claim_type.value}; "
                "l’analyse est refusée plutôt que publiée sans verdict.",
            )
        for finding in findings:
            results.append((record, finding))
    return results


def _verdict_record(
    *,
    organization_id: UUID,
    version: AnalysisVersion,
    sequence_number: int,
    claim_record: dict[str, Any],
    assessment: LegalAssessment,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "organization_id": organization_id,
        "analysis_version_id": version.id,
        "claim_id": claim_record["id"],
        "document_segment_id": claim_record.get("document_segment_id"),
        "sequence_number": sequence_number,
        "claim_type": assessment.claim_type.value,
        "claim_text": assessment.claim_text,
        "trigger_text": assessment.trigger_text,
        "start_offset": assessment.start_offset,
        "end_offset": assessment.end_offset,
        "rule_id": assessment.rule_id,
        "rule_title": assessment.rule_title,
        "law_reference": assessment.law_reference,
        "legal_force": assessment.legal_force.value,
        "severity": assessment.severity.value,
        "verdict": assessment.verdict.value,
        "is_legal_violation": assessment.is_legal_violation,
        "safe_harbor_applicable": assessment.safe_harbor_applicable,
        "safe_harbor_reason": assessment.safe_harbor_reason,
        "legal_caveat": assessment.legal_caveat,
        "source_urls_json": list(assessment.source_urls),
        "required_evidence_json": list(assessment.required_evidence),
        "evidence_checks_json": [item.model_dump(mode="json") for item in assessment.evidence_checks],
        "reasoning_steps_json": [item.model_dump(mode="json") for item in assessment.reasoning_steps],
        "remediation_json": assessment.remediation.model_dump(mode="json") if assessment.remediation else None,
        "sanction_json": assessment.sanction.model_dump(mode="json") if assessment.sanction else None,
        "engine_version": ANALYSIS_ENGINE_VERSION,
        "rulebook_version": RULEBOOK_VERSION,
    }


def _result_verdict_record(record: dict[str, Any]) -> dict[str, Any]:
    """The public, tenant-facing projection of a persisted verdict."""
    return {
        "claim_id": str(record["claim_id"]) if record.get("claim_id") else None,
        "rule_id": record["rule_id"],
        "rule_title": record["rule_title"],
        "law_reference": record["law_reference"],
        "legal_force": record["legal_force"],
        "severity": record["severity"],
        "verdict": record["verdict"],
        "is_legal_violation": record["is_legal_violation"],
        "safe_harbor_applicable": record["safe_harbor_applicable"],
        "safe_harbor_reason": record["safe_harbor_reason"],
        "legal_caveat": record["legal_caveat"],
        "reasoning_steps": record["reasoning_steps_json"],
        "remediation": record["remediation_json"],
        "sanction": record["sanction_json"],
        "engine_version": record["engine_version"],
        "rulebook_version": record["rulebook_version"],
    }


def _confidence_summary(claim_records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How many detections sit at each level, and why reviews were requested.

    Published so a caller can answer "how much of this analysis rests on fragile
    readings?" without re-deriving it from the claim list.
    """
    levels = {level.value: 0 for level in DetectionConfidenceLevel}
    unspecified = 0
    reasons: dict[str, int] = {}
    for record in claim_records:
        attributes = record.get("attributes_json")
        confidence = (
            attributes.get("detection_confidence") if isinstance(attributes, dict) else None
        )
        if not isinstance(confidence, dict) or not confidence.get("level"):
            unspecified += 1
            continue
        level = str(confidence["level"])
        if level in levels:
            levels[level] += 1
        else:
            unspecified += 1
        for reason in confidence.get("review_reasons") or []:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    return {
        "rubric_version": CONFIDENCE_RUBRIC_VERSION,
        "basis": CONFIDENCE_BASIS,
        "levels": levels,
        "review_reasons": reasons,
        "claims_without_rubric": unspecified,
    }


def _result_claim_record(record: dict[str, Any]) -> dict[str, Any]:
    attributes = record.get("attributes_json")
    confidence = (
        attributes.get("detection_confidence") if isinstance(attributes, dict) else None
    )
    confidence = confidence if isinstance(confidence, dict) else {}
    return {
        "id": str(record["id"]),
        "claim_type": record["claim_type"],
        "category": record["category"],
        "claim_text": record["claim_text"],
        "language": record["language"],
        "start_offset": record["start_offset"],
        "end_offset": record["end_offset"],
        "source": _enum_value(record["source"]),
        "confidence_score": record.get("confidence_score"),
        "confidence_level": confidence.get("level"),
        "confidence_factors": list(confidence.get("factors") or []),
        "confidence_basis": confidence.get("basis") or CONFIDENCE_BASIS,
        "confidence_rubric_version": confidence.get("rubric_version"),
        "review_reasons": list(confidence.get("review_reasons") or []),
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
    verdict_records: Sequence[dict[str, Any]],
    assessments: Sequence[LegalAssessment],
    evidence_selection: EvidenceSelection,
    context_json: dict[str, Any],
) -> dict[str, Any]:
    review_required_count = sum(1 for record in claim_records if record["status"] == ClaimStatus.REVIEW_REQUIRED)
    audit_context = _audit_context_from_persisted(context_json)
    overall = derive_overall_status(list(assessments), len(claim_records))
    risk_score = derive_risk_score(list(assessments))
    exposure, exposure_summary = build_exposure_matrix(
        list(assessments), evidence_selection.dossier, audit_context
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "pipeline": PIPELINE_V2,
        "scope": SCOPE_V2,
        "engine_version": ANALYSIS_ENGINE_VERSION,
        "rulebook_version": RULEBOOK_VERSION,
        "evaluation_context": context_json,
        "input_manifest": manifest,
        "input_manifest_sha256": version.input_manifest_sha256,
        "claim_count": len(claim_records),
        "review_required_claim_count": review_required_count,
        "confidence_summary": _confidence_summary(claim_records),
        "verdict_count": len(verdict_records),
        "violations_count": sum(1 for item in verdict_records if item["is_legal_violation"]),
        "overall_compliance": overall.value,
        "risk_score": risk_score,
        "legal_exposure_estimate": exposure_summary,
        "exposure_matrix": exposure.model_dump(mode="json"),
        "evidence_manifest": {
            "included": list(evidence_selection.included),
            "excluded": list(evidence_selection.excluded),
        },
        "documents": list(document_summaries),
        "claims": [_result_claim_record(record) for record in claim_records],
        "verdicts": [_result_verdict_record(record) for record in verdict_records],
        "limitations": [
            "Les résultats sont des détections lexicales déterministes et des verdicts issus d’un Rule Book versionné.",
            f"Détection lexicale ({LEXICON_VERSION}): elle ne couvre que les formulations listées dans le lexique. "
            "Une allégation implicite, une image, un pictogramme ou une formulation hors liste ne sont pas détectés "
            "et ne signifient pas que le support est conforme.",
            "Le score de confiance est une rubrique déterministe (confidence-rubric-v1) calculée sur des éléments "
            "lisibles dans le texte ; ce n’est ni une probabilité, ni un jugement sur le risque juridique.",
            "Une allégation issue d’un segment OCR, d’un document dont l’extraction a demandé une revue, ou dont le "
            "verdict réglementaire est REVIEW_REQUIRED est marquée review_required et doit être revue par une personne.",
            "Les verdicts portent sur le périmètre (support, juridiction, date) figé dans evaluation_context.",
            "Aucune donnée n’est produite par un modèle de langage, un RAG ou un score probabiliste.",
            "Une pièce expirée ou rejetée est écartée du calcul et listée dans evidence_manifest.excluded.",
        ],
    }


def _promote_claims_requiring_review(
    *,
    claim_records: Sequence[dict[str, Any]],
    document_summaries: Sequence[dict[str, Any]],
    evaluated: Sequence[tuple[dict[str, Any], Any]],
) -> int:
    """Raise a claim to ``review_required`` when its own verdict asks for it.

    The rule engine can answer ``REVIEW_REQUIRED`` for a claim the lexicon
    detected with confidence (for example a certification claim with no evidence
    on file). Leaving that claim marked ``detected`` would publish two
    contradictory statements in the same payload. The claim status is therefore
    aligned with its verdict, and the reason is recorded next to the detection
    confidence. Returns the number of claims promoted by this call.
    """
    promoted = 0
    for record, assessment in evaluated:
        if getattr(assessment, "verdict", None) != Verdict.REVIEW_REQUIRED:
            continue
        if record["status"] != ClaimStatus.REVIEW_REQUIRED:
            promoted += 1
        record["status"] = ClaimStatus.REVIEW_REQUIRED
        attributes = record.get("attributes_json")
        if not isinstance(attributes, dict):
            continue
        confidence = attributes.get("detection_confidence")
        if not isinstance(confidence, dict):
            continue
        reasons = list(confidence.get("review_reasons") or [])
        if VERDICT_REVIEW_REASON not in reasons:
            reasons.append(VERDICT_REVIEW_REASON)
        confidence["review_reasons"] = reasons
        confidence["level"] = DetectionConfidenceLevel.HUMAN_REVIEW_REQUIRED.value

    counts: dict[str, int] = {}
    for record in claim_records:
        if record["status"] != ClaimStatus.REVIEW_REQUIRED:
            continue
        attributes = record.get("attributes_json")
        document_key = (
            attributes.get("document_version_id") if isinstance(attributes, dict) else None
        )
        if isinstance(document_key, str):
            counts[document_key] = counts.get(document_key, 0) + 1
    for summary in document_summaries:
        summary["review_required_claim_count"] = counts.get(
            summary.get("document_version_id"), 0
        )
    return promoted


def process_claimed_analysis_detection(
    db: Session,
    *,
    settings: Settings,
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
    # Evaluation runs over the claims just detected, against the evidence the
    # tenant actually recorded, using the context frozen at creation. Nothing is
    # re-extracted and nothing comes from the caller.
    context_json = version.evaluation_context_json
    audit_context = _audit_context_from_persisted(context_json)
    analysis = _get_analysis(
        db,
        organization_id=claim.organization_id,
        analysis_id=version.analysis_id,
    )
    evidence_selection = select_evidence_for_analysis(
        db,
        organization_id=claim.organization_id,
        analysis=analysis,
        as_of_date=audit_context.as_of_date,
    )
    evaluated = _evaluate_persisted_claims(
        claim_records=claim_records,
        selection=evidence_selection,
        context=audit_context,
        settings=settings,
    )
    _promote_claims_requiring_review(
        claim_records=claim_records,
        document_summaries=document_summaries,
        evaluated=evaluated,
    )
    verdict_records = [
        _verdict_record(
            organization_id=claim.organization_id,
            version=version,
            sequence_number=index,
            claim_record=record,
            assessment=assessment,
        )
        for index, (record, assessment) in enumerate(evaluated)
    ]
    return _publish_claim_detection_result(
        db,
        claim=claim,
        manifest=manifest,
        claim_records=claim_records,
        document_summaries=document_summaries,
        verdict_records=verdict_records,
        assessments=[assessment for _, assessment in evaluated],
        evidence_selection=evidence_selection,
        context_json=context_json,
    )


def replay_persisted_version_verdicts(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    analysis_version_id: UUID,
) -> list[dict[str, Any]]:
    """Re-derive the verdicts of a completed version from its stored rows.

    This is the public entry point a verification endpoint or a reproducibility
    audit should call. It reads the persisted claims, the persisted evidence
    registry and the frozen evaluation context, and returns the verdict rows the
    engine produces **now**. Comparing this output with the stored rows is what
    proves a version has not drifted.

    It deliberately does not write anything and does not touch the job table.
    """
    _require_worker_tenant_context(db, organization_id)
    version = _get_analysis_version(
        db, organization_id=organization_id, analysis_version_id=analysis_version_id
    )
    analysis = _get_analysis(
        db, organization_id=organization_id, analysis_id=version.analysis_id
    )
    context_json = version.evaluation_context_json
    audit_context = _audit_context_from_persisted(context_json)

    claims = list(
        db.scalars(
            select(Claim)
            .where(
                Claim.organization_id == organization_id,
                Claim.analysis_version_id == version.id,
            )
            .order_by(Claim.created_at.asc(), Claim.id.asc())
        ).all()
    )
    claim_records = [
        {
            "id": claim.id,
            "claim_type": claim.claim_type,
            "claim_text": claim.claim_text,
            "start_offset": claim.start_offset,
            "end_offset": claim.end_offset,
            "document_segment_id": claim.document_segment_id,
            "attributes_json": claim.attributes_json,
        }
        for claim in claims
    ]
    selection = select_evidence_for_analysis(
        db,
        organization_id=organization_id,
        analysis=analysis,
        as_of_date=audit_context.as_of_date,
    )
    evaluated = _evaluate_persisted_claims(
        claim_records=claim_records,
        selection=selection,
        context=audit_context,
        settings=settings,
    )
    return [
        _verdict_record(
            organization_id=organization_id,
            version=version,
            sequence_number=index,
            claim_record=record,
            assessment=assessment,
        )
        for index, (record, assessment) in enumerate(evaluated)
    ]


def _publish_claim_detection_result(
    db: Session,
    *,
    claim: ClaimedAnalysisDetectionJob,
    manifest: dict[str, Any],
    claim_records: Sequence[dict[str, Any]],
    document_summaries: Sequence[dict[str, Any]],
    verdict_records: Sequence[dict[str, Any]],
    assessments: Sequence[LegalAssessment],
    evidence_selection: EvidenceSelection,
    context_json: dict[str, Any],
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
    existing_verdict_count = db.scalar(
        select(func.count())
        .select_from(AnalysisVerdict)
        .where(
            AnalysisVerdict.organization_id == claim.organization_id,
            AnalysisVerdict.analysis_version_id == version.id,
        )
    )
    if existing_verdict_count:
        raise AnalysisDetectionTerminalError(
            "verdicts_already_published",
            "Des verdicts existent déjà pour cette version immuable; la publication est refusée.",
        )

    now = utcnow()
    claims = [Claim(**record) for record in claim_records]
    for row in claims:
        db.add(row)
    if claims:
        # Les verdicts référencent les allégations par clé étrangère
        # (``analysis_verdicts.claim_id``) : PostgreSQL refuse l'insertion d'un verdict
        # dont l'allégation n'existe pas encore, et SQLAlchemy — qui ne connaît aucune
        # relation entre ces deux tables — choisissait l'ordre inverse. Mesuré : le
        # worker d'analyse échouait sur ``unexpected_worker_error`` à chaque passage,
        # donc aucun verdict, donc aucun rapport.
        db.flush(claims)
    for record in verdict_records:
        db.add(AnalysisVerdict(**record))
    result_json = _completed_result_json(
        version=version,
        manifest=manifest,
        claim_records=claim_records,
        document_summaries=document_summaries,
        verdict_records=verdict_records,
        assessments=assessments,
        evidence_selection=evidence_selection,
        context_json=context_json,
    )
    version.result_json = result_json
    version.result_sha256 = sha256_json(result_json)
    # The published conclusion lives on the version row, but only after the
    # verdict rows it summarises have been written in the same transaction.
    version.overall_compliance = result_json["overall_compliance"]
    version.risk_score = result_json["risk_score"]
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
            "verdict_count": len(verdict_records),
            "violations_count": sum(1 for item in verdict_records if item["is_legal_violation"]),
            "overall_compliance": version.overall_compliance,
            "risk_score": version.risk_score,
            "result_sha256": version.result_sha256,
            "input_manifest_sha256": version.input_manifest_sha256,
            "engine_version": version.engine_version,
            "rulebook_version": version.rulebook_version,
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
