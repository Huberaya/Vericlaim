from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.database import append_audit_record, get_db, sha256_json
from app.documents.scanner import MalwareScanError, MalwareScannerUnavailableError
from app.documents.security import DocumentSecurityValidationError, inspect_document_bytes
from app.identity.dependencies import TenantPrincipal, require_permission, request_id_from_request
from app.identity.service import append_audit_event
from app.engine.document_extractor import DocumentExtractionError, DocumentTextExtractor
from app.engine.risk_assessment import (
    build_exposure_matrix,
    derive_overall_status,
    derive_risk_score,
)
from app.engine.rule_book import RULES, RULEBOOK_VERSION
from app.models.legal_types import AuditTrail, EvidenceDossier, Verdict
from app.models.schemas import AuditContext, EvaluationRequest, EvaluationResponse, RuleBookResponse, RuleSummary


router = APIRouter(prefix="/api/v1/engine", tags=["regulatory-engine"])


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validation_error(exc: ValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.errors(include_url=False))


def _parse_json_body(raw: Any) -> EvaluationRequest:
    try:
        return EvaluationRequest.model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(exc) from exc


async def _read_request(request: Request) -> tuple[EvaluationRequest, str, str | None]:
    """Read JSON or multipart upload, returning request, extraction method and document hash."""
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Corps JSON invalide.") from exc
        return _parse_json_body(raw), "INLINE_TEXT", None

    if "multipart/form-data" not in content_type:
        raise HTTPException(
            status_code=415,
            detail="Utilisez application/json ou multipart/form-data avec source_text/document.",
        )

    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Formulaire multipart invalide.") from exc

    source_text = str(form.get("source_text") or "").strip()
    evidence_payload: dict[str, Any] = {"items": []}
    context_payload: dict[str, Any] = {}
    evidence_json = form.get("evidence_json")
    context_json = form.get("context_json")
    try:
        if evidence_json:
            evidence_payload = json.loads(str(evidence_json))
        if context_json:
            context_payload = json.loads(str(context_json))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="evidence_json/context_json doit contenir du JSON valide.") from exc

    extraction_method = "INLINE_TEXT"
    document_hash: str | None = None
    uploaded = form.get("document")
    if isinstance(uploaded, StarletteUploadFile) and uploaded.filename:
        maximum = request.app.state.settings.max_upload_bytes
        payload = await uploaded.read(maximum + 1)
        if len(payload) > maximum:
            raise HTTPException(status_code=413, detail=f"Fichier trop volumineux (maximum {maximum} octets).")
        # Do not hand untrusted bytes to PDF/image/OCR libraries until their
        # extension, MIME type and magic bytes are coherent and malware scan
        # has returned a clean verdict. This legacy endpoint remains ephemeral
        # (it stores no file) but cannot bypass the secure ingestion boundary.
        try:
            inspected = inspect_document_bytes(
                payload,
                filename=uploaded.filename,
                declared_content_type=uploaded.content_type,
                max_size_bytes=maximum,
            )
        except DocumentSecurityValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            scan = request.app.state.document_scanner.scan(payload)
        except MalwareScannerUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail="Le contrôle antivirus est indisponible; le document ne peut pas être analysé.",
            ) from exc
        except MalwareScanError as exc:
            raise HTTPException(
                status_code=503,
                detail="Le contrôle antivirus n’a pas produit de verdict exploitable.",
            ) from exc
        if not scan.is_clean:
            raise HTTPException(status_code=422, detail="Le fichier a été rejeté par les contrôles de sécurité.")
        document_hash = inspected.sha256
        try:
            extracted_text, extraction_method = DocumentTextExtractor().extract(
                inspected.source_filename,
                inspected.content_type,
                payload,
            )
        except DocumentExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source_text = "\n".join(part for part in (source_text, extracted_text) if part).strip()

    if len(source_text) > request.app.state.settings.max_source_chars:
        raise HTTPException(status_code=413, detail="Texte source trop long.")
    try:
        evidence = EvidenceDossier.model_validate(evidence_payload)
        context = AuditContext.model_validate(context_payload)
        payload = EvaluationRequest(source_text=source_text, context=context, evidence=evidence)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    return payload, extraction_method, document_hash


def _audit_limitations(evaluator_status: str) -> list[str]:
    return [
        "Le moteur applique un Rule Book explicite et versionné; sa couverture lexicale est limitée aux motifs configurés.",
        "Les fichiers de preuve ne sont pas authentifiés ni analysés juridiquement par le moteur; les contrôles portent principalement sur des métadonnées.",
        "L'OCR peut altérer une négation, un chiffre, une unité ou un symbole. Relire le texte extrait sur le document original.",
        "La trace et les empreintes SHA-256 rendent les altérations détectables dans le registre, mais ne constituent ni signature qualifiée, ni horodatage qualifié, ni constat officiel, ni garantie d'opposabilité.",
        f"État de transposition française de la directive (UE) 2024/825 configuré côté serveur: {evaluator_status}.",
        "Le résultat est un outil d'aide à la conformité, non un avis juridique ni une décision de la DGCCRF ou d'un tribunal.",
    ]


@router.post("/evaluate", response_model=EvaluationResponse)
async def evaluate_claims(
    request: Request,
    db: Session = Depends(get_db),
    principal: TenantPrincipal = Depends(require_permission("audit:run", csrf_protected=True)),
) -> EvaluationResponse:
    body, extraction_method, document_sha256 = await _read_request(request)
    if not body.source_text.strip():
        raise HTTPException(status_code=422, detail="Fournissez source_text ou un document exploitable.")

    evaluator = request.app.state.evaluator
    claims, evaluations = evaluator.evaluate_text(body.source_text, body.evidence, body.context)
    overall = derive_overall_status(evaluations, len(claims))
    risk_score = derive_risk_score(evaluations)
    exposure, exposure_summary = build_exposure_matrix(evaluations, body.evidence, body.context)

    violations_count = sum(1 for item in evaluations if item.is_legal_violation)
    conditional_findings_count = sum(
        1
        for item in evaluations
        if item.verdict in {Verdict.CONDITIONAL_REJECT, Verdict.REVIEW_REQUIRED, Verdict.UPCOMING}
    )
    source_sha256 = _sha256_text(body.source_text)
    evidence_manifest_sha256 = sha256_json(body.evidence.model_dump(mode="json"))

    base_response: dict[str, Any] = {
        "extracted_source_text": body.source_text,
        "overall_compliance": overall,
        "risk_score": risk_score,
        "legal_exposure_estimate": exposure_summary,
        "violations_count": violations_count,
        "conditional_findings_count": conditional_findings_count,
        "detected_claims_count": len(claims),
        "evaluations": [item.model_dump(mode="json") for item in evaluations],
        "exposure_matrix": exposure.model_dump(mode="json"),
    }
    report_sha256 = sha256_json(base_response)
    audit_id = str(uuid4())
    now = datetime.now(timezone.utc)
    summary = {
        "overall_compliance": overall.value,
        "risk_score": risk_score,
        "violations_count": violations_count,
        "conditional_findings_count": conditional_findings_count,
        "detected_claims_count": len(claims),
        "rule_ids": sorted({item.rule_id for item in evaluations}),
    }
    try:
        previous_hash, record_hash, created_at = append_audit_record(
            db,
            organization_id=principal.organization_id,
            audit_id=audit_id,
            source_sha256=source_sha256,
            evidence_manifest_sha256=evidence_manifest_sha256,
            report_sha256=report_sha256,
            summary=summary,
            created_at_utc=now,
        )
        append_audit_event(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            entity_type="regulatory_audit",
            entity_id=None,
            action="regulatory_audit.completed",
            payload={
                "legacy_audit_id": audit_id,
                "report_sha256": report_sha256,
                "overall_compliance": overall.value,
                "risk_score": risk_score,
            },
            request_id=request_id_from_request(request),
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Le registre d'audit est indisponible; aucune évaluation n'a été certifiée.") from exc

    audit_trail = AuditTrail(
        audit_id=audit_id,
        engine_version="0.1.0",
        rulebook_version=RULEBOOK_VERSION,
        evaluated_at_utc=created_at,
        as_of_date=body.context.as_of_date,
        source_sha256=source_sha256,
        document_sha256=document_sha256,
        evidence_manifest_sha256=evidence_manifest_sha256,
        report_sha256=report_sha256,
        previous_record_hash=previous_hash,
        record_hash=record_hash,
        extraction_method=extraction_method,
        limitations=_audit_limitations(evaluator.fr_2024_825_transposition_status),
    )
    try:
        return EvaluationResponse(**base_response, audit_trail=audit_trail)
    except ValidationError as exc:
        # This should indicate a developer/schema mismatch, not user input.
        raise HTTPException(status_code=500, detail="Erreur interne de construction du rapport.") from exc


@router.get("/rules", response_model=RuleBookResponse)
def list_rules(
    _: TenantPrincipal = Depends(require_permission("rules:read")),
) -> RuleBookResponse:
    summaries = [
        RuleSummary(
            rule_id=rule.rule_id,
            title=rule.title,
            legal_reference=rule.legal_reference,
            legal_force=rule.legal_force.value,
            severity=rule.severity.value,
            effective_from=rule.effective_from,
            scope=rule.scope,
            source_urls=list(rule.source_urls),
            notes=list(rule.notes),
        )
        for rule in RULES
    ]
    return RuleBookResponse(rulebook_version=RULEBOOK_VERSION, rules=summaries)
