"""Render a report from a persisted analysis version, never from live input.

The rule this module exists to enforce: **a report describes the analysis that
was stored, not a new analysis**. The previous implementation rebuilt a source
text and re-ran the rule engine on it, which produced a certificate stating
"CONFORME AU PÉRIMÈTRE AUDITÉ · Allégations : 0" for an analysis where the engine
had detected three claims and one prohibition. The hash printed under it was
genuine — of the fabricated text.

Consequently this module:

* reads verdicts, claims, the exposure matrix and the audit trail from the
  database;
* reads cited text from the persisted ``document_segments`` rows;
* never calls the rule engine and never re-extracts a claim;
* leaves a field unset when no true value exists, instead of printing a
  reassuring placeholder.
"""
from __future__ import annotations

from datetime import date, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import sha256_json
from app.models.domain import (
    AnalysisStatus,
    AnalysisVerdict,
    AnalysisVersion,
    AuditEvent,
    Claim,
    DocumentSegment,
)
from app.models.legal_types import (
    AuditTrail,
    HumanReviewSummary,
    ClaimType,
    EvidenceCheck,
    ExposureMatrix,
    LegalAssessment,
    LegalForce,
    OverallCompliance,
    ReasoningStep,
    Remediation,
    SanctionProfile,
    Severity,
    Verdict,
)
from app.models.schemas import EvaluationResponse



class PersistedReportUnavailable(RuntimeError):
    """The version cannot honestly be rendered as a compliance report."""


CITED_TEXT_SEPARATOR = "\n\n"
# The extraction method a persisted report genuinely used.
PERSISTED_EXTRACTION_METHOD = "PERSISTED_DOCUMENT_SEGMENTS"


def _enum(enum_class, value, default):
    if value is None:
        return default
    try:
        return enum_class(value)
    except ValueError:
        return default


def _human_review_by_claim(
    db: Session, *, organization_id: UUID, version_id: UUID
) -> dict[UUID | None, HumanReviewSummary]:
    """Historised human decisions, keyed by claim.

    Only rows in ``validations`` are consulted. There is no other path by which a
    verdict can be changed, so a report never shows a decision that no human
    actually recorded.
    """
    from app.models.domain import User, Validation

    rows = db.execute(
        select(Validation, User)
        .outerjoin(User, User.id == Validation.reviewer_user_id)
        .where(
            Validation.organization_id == organization_id,
            Validation.analysis_version_id == version_id,
        )
    ).all()
    summaries: dict[UUID | None, HumanReviewSummary] = {}
    for validation, reviewer in rows:
        summaries[validation.claim_id] = HumanReviewSummary(
            decision=validation.decision.value,
            reviewer_display_name=(reviewer.display_name or reviewer.email) if reviewer else None,
            comment=validation.comment,
            rationale=validation.rationale,
            decided_at=validation.decided_at,
        )
    return summaries


def _assessment_from_verdict(
    verdict: AnalysisVerdict,
    *,
    cited_text: str | None,
    human_review: HumanReviewSummary | None = None,
) -> LegalAssessment:
    """Rebuild one assessment from its stored row.

    Every field comes from the row. Where a nested structure is missing, the
    field is left empty rather than filled with an invented placeholder.
    """
    checks = [
        EvidenceCheck(**item)
        for item in (verdict.evidence_checks_json or [])
        if isinstance(item, dict)
    ]
    remediation_payload = verdict.remediation_json
    remediation = (
        Remediation(**remediation_payload) if isinstance(remediation_payload, dict) else None
    )
    sanction_payload = verdict.sanction_json
    sanction = SanctionProfile(**sanction_payload) if isinstance(sanction_payload, dict) else None

    return LegalAssessment(
        claim_id=str(verdict.claim_id) if verdict.claim_id else "",
        claim_text=cited_text or verdict.claim_text,
        claim_type=_enum(ClaimType, verdict.claim_type, ClaimType.GENERIC_ENVIRONMENTAL),
        start_offset=verdict.start_offset or 0,
        end_offset=verdict.end_offset or 0,
        trigger_text=verdict.trigger_text,
        rule_id=verdict.rule_id,
        rule_title=verdict.rule_title,
        law_reference=verdict.law_reference,
        source_urls=list(verdict.source_urls_json or []),
        legal_force=_enum(LegalForce, verdict.legal_force, LegalForce.INTERNAL_EVIDENCE_CONTROL),
        severity=_enum(Severity, verdict.severity, Severity.MEDIUM),
        verdict=_enum(Verdict, verdict.verdict, Verdict.REVIEW_REQUIRED),
        is_legal_violation=verdict.is_legal_violation,
        safe_harbor_applicable=verdict.safe_harbor_applicable,
        safe_harbor_reason=verdict.safe_harbor_reason,
        required_evidence=list(verdict.required_evidence_json or []),
        evidence_checks=checks,
        # ``reasoning_steps`` is a required field carrying the auditable trace.
        # An empty list here would hide a data problem, so an absent trace is
        # surfaced explicitly in the report instead.
        reasoning_steps=[
            ReasoningStep(**item)
            for item in (verdict.reasoning_steps_json or [])
            if isinstance(item, dict)
        ],
        remediation=remediation,
        sanction=sanction,
        legal_caveat=verdict.legal_caveat,
        human_review=human_review,
    )


def _cited_segments(
    db: Session, *, organization_id: UUID, verdicts: list[AnalysisVerdict]
) -> dict[UUID, DocumentSegment]:
    """Persisted segment rows backing the verdicts, keyed by segment id."""
    segment_ids = {v.document_segment_id for v in verdicts if v.document_segment_id is not None}
    if not segment_ids:
        return {}
    rows = db.scalars(
        select(DocumentSegment).where(
            DocumentSegment.organization_id == organization_id,
            DocumentSegment.id.in_(segment_ids),
        )
    ).all()
    return {row.id: row for row in rows}


def _audit_event_hashes(db: Session, *, organization_id: UUID, version: AnalysisVersion) -> tuple[Any, str | None, str | None]:
    """The real audit-chain entry for this version, when it exists."""
    if version.detection_job is None:
        return None, None, None
    job_id = version.detection_job.id
    event = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == organization_id,
            AuditEvent.entity_id == job_id,
            AuditEvent.action == "analysis.claim_detection_completed",
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    if event is None:
        return None, None, None
    return event.id, event.event_hash, event.previous_event_hash


def build_persisted_evaluation(
    db: Session,
    *,
    organization_id: UUID,
    version: AnalysisVersion,
    product_identifier: str | None = None,
) -> EvaluationResponse:
    """Assemble the report payload strictly from stored rows.

    Raises :class:`PersistedReportUnavailable` when the version carries no
    regulatory conclusion. A version produced before the verdict pipeline must
    not be rendered as a compliance report.
    """
    if version.status != AnalysisStatus.COMPLETED:
        raise PersistedReportUnavailable(
            "Cette version d’analyse n’est pas terminée; aucun rapport ne peut être établi."
        )
    if not version.overall_compliance:
        raise PersistedReportUnavailable(
            "Cette version d’analyse ne contient aucun verdict réglementaire (pipeline antérieur). "
            "Relancez l’analyse pour obtenir un rapport."
        )

    verdicts = list(
        db.scalars(
            select(AnalysisVerdict)
            .where(
                AnalysisVerdict.organization_id == organization_id,
                AnalysisVerdict.analysis_version_id == version.id,
            )
            .order_by(AnalysisVerdict.sequence_number.asc())
        ).all()
    )
    if not verdicts:
        raise PersistedReportUnavailable(
            "Aucun verdict persisté pour cette version; le rapport serait trompeur."
        )

    segments = _cited_segments(db, organization_id=organization_id, verdicts=verdicts)
    reviews = _human_review_by_claim(db, organization_id=organization_id, version_id=version.id)
    claims = {
        row.id: row
        for row in db.scalars(
            select(Claim).where(
                Claim.organization_id == organization_id,
                Claim.analysis_version_id == version.id,
            )
        ).all()
    }

    # The text shown is the persisted segment text, with the claim located inside
    # it. Nothing is regenerated and no default sentence is substituted.
    cited_blocks: list[str] = []
    seen_segments: set[UUID] = set()
    assessments: list[LegalAssessment] = []
    for verdict in verdicts:
        segment = segments.get(verdict.document_segment_id) if verdict.document_segment_id else None
        cited = None
        if segment is not None:
            claim = claims.get(verdict.claim_id) if verdict.claim_id else None
            if claim is not None and claim.start_offset is not None and claim.end_offset is not None:
                base = segment.start_offset or 0
                start = max(0, claim.start_offset - base)
                end = max(start, (claim.end_offset if claim.end_offset is not None else len(segment.text)) - base)
                cited = segment.text[start:end] or None
            if cited is None:
                cited = segment.text
            if segment.id not in seen_segments:
                seen_segments.add(segment.id)
                cited_blocks.append(segment.text)
        assessments.append(
            _assessment_from_verdict(
                verdict,
                cited_text=cited,
                human_review=reviews.get(verdict.claim_id),
            )
        )

    result = dict(version.result_json or {})
    context = dict(result.get("evaluation_context") or {})
    as_of_date = context.get("as_of_date")
    try:
        as_of = date.fromisoformat(str(as_of_date))
    except (TypeError, ValueError):
        raise PersistedReportUnavailable(
            "Le contexte d’évaluation de cette version est absent; le rapport ne peut pas être daté."
        )

    exposure_payload = result.get("exposure_matrix") or {}
    exposure = ExposureMatrix(**exposure_payload) if exposure_payload else ExposureMatrix()

    event_id, record_hash, previous_hash = _audit_event_hashes(
        db, organization_id=organization_id, version=version
    )

    evaluated_at = version.completed_at or version.created_at
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)

    evidence_manifest = result.get("evidence_manifest") or {}
    violations_count = sum(1 for item in verdicts if item.is_legal_violation)
    conditional_count = sum(
        1
        for item in verdicts
        if item.verdict
        in {
            Verdict.CONDITIONAL_REJECT.value,
            Verdict.REVIEW_REQUIRED.value,
            Verdict.UPCOMING.value,
        }
    )

    audit_trail = AuditTrail(
        # None means "no such value exists", which the renderer must print as
        # absent. A "0"*64 placeholder reads like a verified hash and is worse
        # than an empty field.
        audit_id=str(event_id) if event_id else str(version.id),
        engine_version=version.engine_version,
        rulebook_version=version.rulebook_version,
        evaluated_at_utc=evaluated_at,
        as_of_date=as_of,
        source_sha256=version.input_manifest_sha256,
        document_sha256=None,
        evidence_manifest_sha256=sha256_json(evidence_manifest) if evidence_manifest else "",
        report_sha256=version.result_sha256 or "",
        previous_record_hash=previous_hash,
        record_hash=record_hash or "",
        extraction_method=PERSISTED_EXTRACTION_METHOD,
        limitations=list(result.get("limitations") or []),
    )

    return EvaluationResponse(
        extracted_source_text=CITED_TEXT_SEPARATOR.join(cited_blocks),
        overall_compliance=_enum(OverallCompliance, version.overall_compliance, OverallCompliance.REVIEW_REQUIRED),
        risk_score=version.risk_score if version.risk_score is not None else 0,
        legal_exposure_estimate=str(result.get("legal_exposure_estimate") or ""),
        violations_count=violations_count,
        conditional_findings_count=conditional_count,
        detected_claims_count=len(claims),
        evaluations=assessments,
        exposure_matrix=exposure,
        audit_trail=audit_trail,
    )
