from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.database import AuditRecord, append_audit_record, canonical_json, get_db, sha256_json
from app.core.limiter import limiter
from app.engine.document_extractor import DocumentExtractionError, DocumentTextExtractor
from app.engine.pdf_exporter import generate_audit_pdf
from app.engine.risk_assessment import (
    build_exposure_matrix,
    derive_overall_status,
    derive_risk_score,
)
from app.engine.rule_book import RULES, RULEBOOK_VERSION
from app.models.legal_types import AuditTrail, EvidenceDossier, LegalAssessment, OverallCompliance, Surface, Verdict
from app.models.schemas import (
    AuditContext,
    AuditHistoryItem,
    AuditHistoryResponse,
    EvaluationRequest,
    EvaluationResponse,
    RuleBookResponse,
    RuleSummary,
    SupplierCompareRequest,
    SupplierCompareResponse,
    SupplierComparisonItem,
    SupplierSubmission,
    UrlAuditRequest,
    CatalogBatchRequest,
    CatalogBatchResponse,
    CatalogItemInput,
    CatalogItemResult,
)


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
        document_hash = hashlib.sha256(payload).hexdigest()
        try:
            extracted_text, extraction_method = DocumentTextExtractor().extract(
                uploaded.filename,
                uploaded.content_type,
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


def _execute_evaluation(
    evaluator,
    source_text: str,
    context: AuditContext,
    evidence: EvidenceDossier,
    db: Session,
    extraction_method: str = "INLINE_TEXT",
    document_sha256: str | None = None,
) -> EvaluationResponse:
    claims, evaluations = evaluator.evaluate_text(source_text, evidence, context)
    overall = derive_overall_status(evaluations, len(claims))
    risk_score = derive_risk_score(evaluations)
    exposure, exposure_summary = build_exposure_matrix(evaluations, evidence, context)

    violations_count = sum(1 for item in evaluations if item.is_legal_violation)
    conditional_findings_count = sum(
        1
        for item in evaluations
        if item.verdict in {Verdict.CONDITIONAL_REJECT, Verdict.REVIEW_REQUIRED, Verdict.UPCOMING}
    )
    source_sha256 = _sha256_text(source_text)
    evidence_manifest_sha256 = sha256_json(evidence.model_dump(mode="json"))

    base_response: dict[str, Any] = {
        "extracted_source_text": source_text,
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
    fine_val = (
        str(exposure.max_known_fixed_fine_eur)
        if exposure.max_known_fixed_fine_eur is not None
        else None
    )
    summary = {
        "overall_compliance": overall.value,
        "risk_score": risk_score,
        "violations_count": violations_count,
        "conditional_findings_count": conditional_findings_count,
        "detected_claims_count": len(claims),
        "rule_ids": sorted({item.rule_id for item in evaluations}),
        "max_fixed_fine_eur": fine_val,
        "source_snippet": source_text[:200],
    }
    try:
        previous_hash, record_hash, created_at = append_audit_record(
            db,
            audit_id=audit_id,
            source_sha256=source_sha256,
            evidence_manifest_sha256=evidence_manifest_sha256,
            report_sha256=report_sha256,
            summary=summary,
            created_at_utc=now,
            supplier_name=context.supplier_name,
            product_identifier=context.product_identifier,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Le registre d'audit est indisponible; aucune évaluation n'a été certifiée.",
        ) from exc

    audit_trail = AuditTrail(
        audit_id=audit_id,
        engine_version="0.1.0",
        rulebook_version=RULEBOOK_VERSION,
        evaluated_at_utc=created_at,
        as_of_date=context.as_of_date,
        source_sha256=source_sha256,
        document_sha256=document_sha256,
        evidence_manifest_sha256=evidence_manifest_sha256,
        report_sha256=report_sha256,
        previous_record_hash=previous_hash,
        record_hash=record_hash,
        extraction_method=extraction_method,
        limitations=_audit_limitations(evaluator.fr_2024_825_transposition_status),
    )
    final_response = EvaluationResponse(**base_response, audit_trail=audit_trail)

    # Sauvegarder le rapport complet pour consultation historique et comparaison
    try:
        record = db.scalar(select(AuditRecord).where(AuditRecord.audit_id == audit_id))
        if record:
            record.report_json = final_response.model_dump(mode="json")
            db.commit()
    except Exception:
        pass

    try:
        from app.core.monitoring import record_evaluation_metrics
        violation_rules = [item.rule_id for item in evaluations if item.is_legal_violation]
        claim_types = [item.claim_type.value for item in evaluations if hasattr(item, "claim_type") and hasattr(item.claim_type, "value")]
        record_evaluation_metrics(
            compliance=overall.value,
            extraction_method=extraction_method,
            duration_sec=0.02,
            violations=violation_rules,
            claims=claim_types,
        )
    except Exception:
        pass

    return final_response


@router.get("/rate-limit-check")
@limiter.limit("2/minute")
async def rate_limit_check(request: Request) -> dict[str, Any]:
    """Route de test pour la vérification du rate limiting et de la protection anti-abus."""
    return {"status": "ok", "ratelimited": True}


@router.post("/evaluate", response_model=EvaluationResponse)
@limiter.limit("60/minute")
async def evaluate_claims(request: Request, db: Session = Depends(get_db)) -> EvaluationResponse:
    body, extraction_method, document_sha256 = await _read_request(request)
    if not body.source_text.strip():
        raise HTTPException(status_code=422, detail="Fournissez source_text ou un document exploitable.")

    evaluator = request.app.state.evaluator
    return _execute_evaluation(
        evaluator=evaluator,
        source_text=body.source_text,
        context=body.context,
        evidence=body.evidence,
        db=db,
        extraction_method=extraction_method,
        document_sha256=document_sha256,
    )


@router.post("/evaluate/url", response_model=EvaluationResponse)
@limiter.limit("20/minute")
async def evaluate_ecommerce_url(
    body: UrlAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> EvaluationResponse:
    """Scrape et audite en direct une page produit e-commerce (Shopify, WooCommerce, Amazon...)."""
    from app.engine.url_scraper import EcommerceUrlScraper, UrlScraperError

    scraper = EcommerceUrlScraper()
    try:
        extracted_text, document_sha256, metadata = await scraper.scrape(body.url)
    except UrlScraperError as exc:
        raise HTTPException(status_code=422, detail=f"Erreur d'extraction URL : {exc}") from exc

    context = body.context or AuditContext()
    # Par défaut, le scraping de page e-commerce audite sur le support boutique en ligne
    if not body.context or body.context.surface == Surface.PACKAGING:
        context.surface = Surface.ONLINE_STORE

    if not context.product_identifier and metadata.get("page_title"):
        context.product_identifier = metadata["page_title"][:64]

    evaluator = request.app.state.evaluator
    evidence = body.evidence or EvidenceDossier(items=[])

    return _execute_evaluation(
        evaluator=evaluator,
        source_text=extracted_text,
        context=context,
        evidence=evidence,
        db=db,
        extraction_method="URL_SCRAPER",
        document_sha256=document_sha256,
    )


def _process_catalog_batch(
    items: list[CatalogItemInput],
    jurisdiction: str,
    as_of_date: str | None,
    consumer_facing: bool,
    evaluator: Any,
    db: Session,
) -> CatalogBatchResponse:
    results: list[CatalogItemResult] = []
    total_fines = 0
    compliant_count = 0
    non_compliant_count = 0
    review_required_count = 0
    total_risk = 0

    for item in items:
        evidence_items = []
        if item.has_lca:
            evidence_items.append({"kind": "lca_report", "standard": "ISO 14044"})
        if item.ecolabel_license:
            evidence_items.append({
                "kind": "ecolabel_certificate",
                "scheme": "EU_ECOLABEL",
                "license_number": item.ecolabel_license,
            })
        ctx_kwargs: dict[str, Any] = {
            "jurisdiction": jurisdiction,
            "surface": item.surface,
            "consumer_facing": consumer_facing,
            "product_identifier": item.sku,
            "supplier_name": item.supplier_name,
            "product_category": item.category or "packaging",
        }
        if as_of_date:
            ctx_kwargs["as_of_date"] = as_of_date
        ctx = AuditContext(**ctx_kwargs)
        dossier = EvidenceDossier(items=evidence_items, legal_person=True)
        rep = _execute_evaluation(
            evaluator=evaluator,
            source_text=item.text,
            context=ctx,
            evidence=dossier,
            db=db,
            extraction_method="CATALOG_BATCH",
        )
        fine_val = int(rep.exposure_matrix.max_known_fixed_fine_eur or 0)
        total_fines += fine_val
        total_risk += rep.risk_score

        if rep.overall_compliance == OverallCompliance.COMPLIANT:
            compliant_count += 1
        elif rep.overall_compliance == OverallCompliance.NON_COMPLIANT:
            non_compliant_count += 1
        else:
            review_required_count += 1

        claims_list = list({ev.claim_text for ev in rep.evaluations})
        results.append(
            CatalogItemResult(
                sku=item.sku,
                title=item.title or item.sku,
                supplier_name=item.supplier_name,
                overall_compliance=rep.overall_compliance,
                risk_score=rep.risk_score,
                detected_claims_count=rep.detected_claims_count,
                violations_count=rep.violations_count,
                fines_ceiling_eur=fine_val,
                summary=rep.legal_exposure_estimate,
                claims=claims_list,
            )
        )

    n = len(items)
    comp_rate = round((compliant_count / n) * 100, 1) if n > 0 else 0.0
    avg_risk = round(total_risk / n, 1) if n > 0 else 0.0

    return CatalogBatchResponse(
        total_items=n,
        compliant_items=compliant_count,
        non_compliant_items=non_compliant_count,
        review_required_items=review_required_count,
        total_fines_ceiling_eur=total_fines,
        compliance_rate_pct=comp_rate,
        average_risk_score=avg_risk,
        results=results,
    )


@router.post("/evaluate/batch", response_model=CatalogBatchResponse)
@limiter.limit("30/minute")
def evaluate_catalog_batch(
    body: CatalogBatchRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CatalogBatchResponse:
    """Audite un lot (batch) de fiches ou références catalogue au format JSON structuré."""
    evaluator = request.app.state.evaluator
    return _process_catalog_batch(
        items=body.items,
        jurisdiction=body.jurisdiction,
        as_of_date=body.as_of_date,
        consumer_facing=body.consumer_facing,
        evaluator=evaluator,
        db=db,
    )


@router.post("/evaluate/batch-csv", response_model=CatalogBatchResponse)
@limiter.limit("20/minute")
async def evaluate_catalog_batch_csv(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> CatalogBatchResponse:
    """Audite un catalogue de produits importé via un fichier CSV."""
    content = await file.read()
    text_content = content.decode("utf-8-sig", errors="replace")
    
    # Détection automatique du séparateur (, ou ;)
    first_line = text_content.split("\n")[0] if text_content else ""
    delimiter = ";" if ";" in first_line else ","
    
    reader = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)
    items: list[CatalogItemInput] = []

    for row in reader:
        # Recherche tolérante des noms de colonnes
        sku = row.get("sku") or row.get("ref") or row.get("id") or row.get("reference") or f"SKU-{len(items)+1}"
        title = row.get("title") or row.get("nom") or row.get("name") or row.get("produit") or ""
        text = row.get("text") or row.get("description") or row.get("claim") or row.get("allegation") or ""
        if not text.strip():
            continue
        surface_str = (row.get("surface") or "packaging").strip().lower()
        surface = Surface.ONLINE_STORE if "online" in surface_str or "web" in surface_str else Surface.PACKAGING
        supplier = row.get("supplier") or row.get("fournisseur")
        has_lca = (row.get("has_lca") or row.get("acv") or "").strip().lower() in {"true", "1", "oui", "yes"}
        ecolabel = row.get("ecolabel") or row.get("license") or None

        items.append(
            CatalogItemInput(
                sku=sku.strip(),
                title=title.strip(),
                text=text.strip(),
                surface=surface,
                supplier_name=supplier.strip() if supplier else None,
                has_lca=has_lca,
                ecolabel_license=ecolabel.strip() if ecolabel else None,
            )
        )

    if not items:
        raise HTTPException(
            status_code=422,
            detail="Aucune ligne exploitable trouvée dans le CSV. Colonnes supportées : sku/ref, title/nom, text/description, surface, supplier.",
        )

    evaluator = request.app.state.evaluator
    return _process_catalog_batch(
        items=items,
        jurisdiction="FR",
        as_of_date=None,
        consumer_facing=True,
        evaluator=evaluator,
        db=db,
    )


@router.post("/export/batch-csv")
def export_catalog_batch_csv(body: CatalogBatchResponse) -> Response:
    """Exporte la synthèse de conformité du catalogue au format CSV prêt pour Excel."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "SKU",
        "Titre Produit",
        "Fournisseur",
        "Statut Conformité",
        "Score de Risque (/100)",
        "Allégations Détectées",
        "Infractions Constatées",
        "Plafond d'Amende (€)",
        "Synthèse Réglementaire",
    ])
    for item in body.results:
        writer.writerow([
            item.sku,
            item.title,
            item.supplier_name or "",
            item.overall_compliance.value,
            item.risk_score,
            item.detected_claims_count,
            item.violations_count,
            item.fines_ceiling_eur,
            item.summary,
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vericlaim_audit_catalogue.csv"'},
    )


@router.get("/audits", response_model=AuditHistoryResponse)
def list_audits(
    limit: int = 50,
    offset: int = 0,
    supplier: str | None = None,
    compliance: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
) -> AuditHistoryResponse:
    """Consulte l'historique chronologique des audits enregistrés dans le registre immuable."""
    query = select(AuditRecord).order_by(AuditRecord.sequence.desc())
    records = list(db.scalars(query).all())

    filtered: list[AuditRecord] = []
    for r in records:
        summary = r.summary_json or {}
        comp = summary.get("overall_compliance", "")
        if compliance and comp != compliance:
            continue
        if supplier and (not r.supplier_name or supplier.lower() not in r.supplier_name.lower()):
            continue
        if search:
            snippet = summary.get("source_snippet", "")
            supp = r.supplier_name or ""
            sku = r.product_identifier or ""
            term = search.lower()
            if term not in snippet.lower() and term not in supp.lower() and term not in sku.lower() and term not in r.audit_id.lower():
                continue
        filtered.append(r)

    total = len(filtered)
    page_records = filtered[offset : offset + limit]

    items: list[AuditHistoryItem] = []
    for r in page_records:
        summary = r.summary_json or {}
        fine_val = summary.get("max_fixed_fine_eur")
        fine = Decimal(str(fine_val)) if fine_val is not None else None
        comp_val = summary.get("overall_compliance", "NO_CLAIMS_DETECTED")
        items.append(
            AuditHistoryItem(
                audit_id=r.audit_id,
                created_at_utc=r.created_at_utc,
                supplier_name=r.supplier_name,
                product_identifier=r.product_identifier,
                overall_compliance=(
                    OverallCompliance(comp_val)
                    if comp_val in OverallCompliance._value2member_map_
                    else OverallCompliance.NO_CLAIMS_DETECTED
                ),
                risk_score=int(summary.get("risk_score", 0)),
                violations_count=int(summary.get("violations_count", 0)),
                conditional_findings_count=int(summary.get("conditional_findings_count", 0)),
                detected_claims_count=int(summary.get("detected_claims_count", 0)),
                record_hash=r.record_hash,
                max_fixed_fine_eur=fine,
                source_snippet=summary.get("source_snippet", ""),
            )
        )
    return AuditHistoryResponse(total=total, items=items)


@router.get("/audits/{audit_id}", response_model=EvaluationResponse)
def get_audit(audit_id: str, db: Session = Depends(get_db)) -> EvaluationResponse:
    """Récupère le rapport complet d'un audit archivé par son identifiant."""
    record = db.scalar(select(AuditRecord).where(AuditRecord.audit_id == audit_id))
    if not record or not record.report_json:
        raise HTTPException(status_code=404, detail="Rapport d'audit introuvable.")
    return EvaluationResponse.model_validate(record.report_json)


@router.post("/suppliers/compare", response_model=SupplierCompareResponse)
def compare_suppliers(
    body: SupplierCompareRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SupplierCompareResponse:
    """Compare et classe plusieurs offres ou dossiers fournisseurs selon leur niveau de risque réglementaire."""
    evaluator = request.app.state.evaluator
    evaluations_to_compare: list[tuple[str, str | None, EvaluationResponse]] = []

    # 1. Depuis des identifiants d'audits passés
    for audit_id in body.audit_ids:
        record = db.scalar(select(AuditRecord).where(AuditRecord.audit_id == audit_id))
        if record and record.report_json:
            rep = EvaluationResponse.model_validate(record.report_json)
            s_name = record.supplier_name or f"Audit {audit_id[:8]}"
            p_id = record.product_identifier
            evaluations_to_compare.append((s_name, p_id, rep))

    # 2. Depuis des soumissions directes
    for sub in body.submissions:
        ctx = sub.context or AuditContext(
            supplier_name=sub.supplier_name, product_identifier=sub.product_identifier
        )
        if sub.supplier_name and not ctx.supplier_name:
            ctx.supplier_name = sub.supplier_name
        if sub.product_identifier and not ctx.product_identifier:
            ctx.product_identifier = sub.product_identifier
        dossier = sub.evidence or EvidenceDossier(items=[])
        rep = _execute_evaluation(evaluator, sub.source_text, ctx, dossier, db)
        evaluations_to_compare.append((sub.supplier_name, sub.product_identifier, rep))

    if not evaluations_to_compare:
        raise HTTPException(
            status_code=400,
            detail="Fournissez au moins une offre fournisseur (via 'audit_ids' ou 'submissions') à évaluer.",
        )

    # Critères de classement du comparateur achats :
    # 1. Conformité globale (COMPLIANT en tête, NON_COMPLIANT en queue)
    # 2. Score de risque le plus faible
    # 3. Moins d'infractions
    # 4. Exposition financière la plus faible
    def sort_key(item: tuple[str, str | None, EvaluationResponse]):
        _, _, rep = item
        status_rank = {
            OverallCompliance.COMPLIANT: 0,
            OverallCompliance.NO_CLAIMS_DETECTED: 1,
            OverallCompliance.REVIEW_REQUIRED: 2,
            OverallCompliance.CONDITIONAL_REJECT: 3,
            OverallCompliance.UPCOMING_REQUIREMENTS: 4,
            OverallCompliance.NON_COMPLIANT: 5,
        }.get(rep.overall_compliance, 9)
        fine = rep.exposure_matrix.max_known_fixed_fine_eur or Decimal(0)
        return (status_rank, rep.risk_score, rep.violations_count, fine)

    sorted_evals = sorted(evaluations_to_compare, key=sort_key)

    items: list[SupplierComparisonItem] = []
    for rank, (name, pid, rep) in enumerate(sorted_evals, start=1):
        if rep.overall_compliance == OverallCompliance.COMPLIANT and rep.risk_score < 30:
            rec = "CONFORME — Validé pour référencement achat"
            color = "green"
        elif rep.overall_compliance == OverallCompliance.NON_COMPLIANT or rep.violations_count > 0:
            rec = "NON CONFORME — Risque juridique élevé (infractions retenues)"
            color = "red"
        else:
            rec = "VIGILANCE — Preuves ou modifications obligatoires avant signature"
            color = "amber"

        viol_rules = sorted({ev.rule_title for ev in rep.evaluations if ev.is_legal_violation})
        claims_det = [ev.trigger_text for ev in rep.evaluations]

        first_clause = (
            rep.evaluations[0].remediation.supplier_contract_clause
            if rep.evaluations
            else "Le Fournisseur garantit la stricte conformité réglementaire de ses allégations environnementales."
        )

        has_lca = any(
            "ACV" in ev.rule_id or any("14044" in c.check_name for c in ev.evidence_checks)
            for ev in rep.evaluations
        )
        has_ecolabel = any(
            any("ECOLABEL" in c.check_name and c.independently_verified for c in ev.evidence_checks)
            for ev in rep.evaluations
        )

        items.append(
            SupplierComparisonItem(
                supplier_name=name,
                product_identifier=pid,
                audit_id=rep.audit_trail.audit_id,
                overall_compliance=rep.overall_compliance,
                risk_score=rep.risk_score,
                rank=rank,
                recommendation=rec,
                recommendation_color=color,
                violations_count=rep.violations_count,
                violations_summary=viol_rules,
                max_known_fine_eur=rep.exposure_matrix.max_known_fixed_fine_eur,
                claims_detected=list(dict.fromkeys(claims_det)),
                procurement_clause=first_clause,
                has_lca_declared=has_lca,
                has_ecolabel_declared=has_ecolabel,
            )
        )

    best_supp = items[0].supplier_name if items else None
    summary_parts = []
    if items:
        summary_parts.append(f"Comparatif de {len(items)} fournisseur(s) : ")
        summary_parts.append(
            f"Fournisseur le plus vertueux : « {items[0].supplier_name} » (Score risque : {items[0].risk_score}/100). "
        )
        non_compliant = [it for it in items if it.overall_compliance == OverallCompliance.NON_COMPLIANT]
        if non_compliant:
            summary_parts.append(
                f"{len(non_compliant)} offre(s) présentent un risque d'infraction juridique caractérisée."
            )
        else:
            summary_parts.append("Aucun manquement critique non remédiable détecté.")

    return SupplierCompareResponse(
        evaluated_at=datetime.now(timezone.utc),
        suppliers_count=len(items),
        ranked_suppliers=items,
        best_supplier=best_supp,
        benchmark_summary="".join(summary_parts),
    )


@router.get("/rules", response_model=RuleBookResponse)
def list_rules() -> RuleBookResponse:
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


@router.post("/export/pdf")
@limiter.limit("30/minute")
def export_audit_pdf(evaluation: EvaluationResponse, request: Request) -> Response:
    """Génère l'attestation officielle d'audit juridique en PDF."""
    try:
        pdf_bytes = generate_audit_pdf(evaluation)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur de génération PDF : {exc}") from exc

    short_id = evaluation.audit_trail.audit_id.replace("-", "")[:8].upper()
    filename = f"VeriClaim_Attestation_{short_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.get("/ecolabels/registries")
def get_ecolabel_registries(request: Request) -> list[dict[str, Any]]:
    """Retourne la liste des registres d'écolabels officiels connectés (UE, AFNOR, RAL, Nordic)."""
    connector = getattr(request.app.state, "ecolabel_connector", None)
    if connector is None:
        from app.engine.ecolabel_connector import LiveEcolabelConnector
        connector = LiveEcolabelConnector()
    return connector.list_registries()


@router.get("/ecolabels/verify")
def verify_ecolabel_license(
    license_number: str,
    request: Request,
    scheme: str | None = None,
    product_identifier: str | None = None,
) -> dict[str, Any]:
    """Vérifie en direct un numéro de licence d'écolabel auprès des registres officiels."""
    connector = getattr(request.app.state, "ecolabel_connector", None)
    if connector is None:
        from app.engine.ecolabel_connector import LiveEcolabelConnector
        connector = LiveEcolabelConnector()
    return connector.verify_live(
        license_number=license_number,
        scheme=scheme,
        product_identifier=product_identifier,
    )


@router.post("/ecolabels/sync")
def sync_ecolabel_registries(request: Request) -> dict[str, Any]:
    """Synchronise et actualise le cache local avec les registres officiels."""
    connector = getattr(request.app.state, "ecolabel_connector", None)
    if connector is None:
        from app.engine.ecolabel_connector import LiveEcolabelConnector
        connector = LiveEcolabelConnector()
    return connector.sync()
