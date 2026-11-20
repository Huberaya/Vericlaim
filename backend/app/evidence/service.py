"""Transactional, tenant-scoped persistent evidence registry and claim linking.

This module provides the core Evidence Engine for VeriClaim:
- Registering and managing verifiable pieces of evidence (LCA reports, ISO/Ecolabel certificates, lab tests, GHG reduction plans, recycling route declarations).
- Establishing explicit, auditable links between detected claims and supporting evidence.
- Evaluating evidence coverage, validity dates, product scopes, and computing transparent rationale without statistical hallucination.
- Aggregating the analysis claim-evidence matrix.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog.service import get_product, get_supplier
from app.evidence.coverage import (
    OBSERVED_STATE_TO_EVIDENCE_STATUS,
    coverage_for_analysis_version,
    examine_evidence,
    observed_state,
)
from app.core.database import sha256_json
from app.identity.service import append_audit_event
from app.models.legal_types import ClaimType
from app.models.domain import (
    Analysis,
    AnalysisVersion,
    Certificate,
    Claim,
    DocumentVersion,
    Evidence,
    EvidenceLink,
    EvidenceRelation,
    EvidenceStatus,
    EvidenceType,
    Product,
    Supplier,
)


class EvidenceServiceError(RuntimeError):
    """Base exception for evidence registry operations."""


class EvidenceNotFoundError(EvidenceServiceError):
    """Raised when an evidence record or link is missing or inaccessible in this tenant."""


class EvidenceConflictError(EvidenceServiceError):
    """Raised on invariant or integrity violations."""


class EvidenceInputError(EvidenceServiceError):
    """Raised on invalid request parameters."""


@dataclass(frozen=True)
class EvidencePage:
    items: list[Evidence]
    next_cursor: str | None


@dataclass(frozen=True)
class ClaimEvidenceEvaluation:
    claim_id: UUID
    coverage_status: EvidenceStatus
    is_sufficient: bool
    explanation: str
    links: list[EvidenceLink]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_date() -> date:
    return datetime.now(timezone.utc).date()


def _require_tenant_context(db: Session, organization_id: UUID) -> None:
    current = db.info.get("current_organization_id")
    if current is not None and current != organization_id:
        raise EvidenceConflictError(
            "Le contexte organisationnel actif ne correspond pas à l’opération probatoire."
        )


def _encode_cursor(sort_key: str, item_id: UUID) -> str:
    payload = json.dumps({"k": sort_key, "i": str(item_id)})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: str | None) -> tuple[str | None, UUID | None]:
    if not cursor:
        return None, None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        data = json.loads(raw)
        return data["k"], UUID(data["i"])
    except Exception as exc:
        raise EvidenceInputError("Curseur de pagination invalide.") from exc


def get_evidence(
    db: Session,
    *,
    organization_id: UUID,
    evidence_id: UUID,
    include_archived: bool = False,
    lock: bool = False,
) -> Evidence:
    _require_tenant_context(db, organization_id)
    statement = select(Evidence).where(
        Evidence.organization_id == organization_id,
        Evidence.id == evidence_id,
    )
    if not include_archived:
        statement = statement.where(Evidence.deleted_at.is_(None))
    if lock:
        statement = statement.with_for_update()
    evidence = db.scalar(statement)
    if evidence is None:
        raise EvidenceNotFoundError("Preuve introuvable ou inaccessible dans cette organisation.")
    return evidence


def _validate_evidence_relations(
    db: Session,
    *,
    organization_id: UUID,
    supplier_id: UUID | None,
    product_id: UUID | None,
    document_version_id: UUID | None,
    certificate_id: UUID | None,
) -> None:
    supplier: Supplier | None = None
    if supplier_id is not None:
        supplier = get_supplier(db, organization_id=organization_id, supplier_id=supplier_id)

    if product_id is not None:
        product = get_product(db, organization_id=organization_id, product_id=product_id)
        if supplier is not None and product.supplier_id != supplier.id:
            raise EvidenceConflictError(
                "Incohérence : le produit spécifié n’appartient pas au fournisseur indiqué."
            )

    if document_version_id is not None:
        doc_ver = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.id == document_version_id,
            )
        )
        if doc_ver is None:
            raise EvidenceNotFoundError("Version documentaire introuvable ou inaccessible.")

    if certificate_id is not None:
        cert = db.scalar(
            select(Certificate).where(
                Certificate.organization_id == organization_id,
                Certificate.id == certificate_id,
                Certificate.deleted_at.is_(None),
            )
        )
        if cert is None:
            raise EvidenceNotFoundError("Certificat déclaré introuvable ou inaccessible.")


def create_evidence(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    evidence_type: EvidenceType,
    reference: str | None = None,
    issuer: str | None = None,
    issued_on: date | None = None,
    expires_on: date | None = None,
    product_scope: str | None = None,
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    document_version_id: UUID | None = None,
    certificate_id: UUID | None = None,
    status: EvidenceStatus = EvidenceStatus.PENDING,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> Evidence:
    _require_tenant_context(db, organization_id)
    _validate_evidence_relations(
        db,
        organization_id=organization_id,
        supplier_id=supplier_id,
        product_id=product_id,
        document_version_id=document_version_id,
        certificate_id=certificate_id,
    )

    now_d = today_date()
    computed_status = status
    if expires_on is not None and expires_on < now_d:
        computed_status = EvidenceStatus.EXPIRED
    elif computed_status == EvidenceStatus.PENDING:
        if document_version_id is not None or reference or metadata:
            computed_status = EvidenceStatus.PRESENT

    evidence = Evidence(
        id=uuid4(),
        organization_id=organization_id,
        evidence_type=evidence_type,
        status=computed_status,
        reference=reference,
        issuer=issuer,
        issued_on=issued_on,
        expires_on=expires_on,
        product_scope=product_scope,
        supplier_id=supplier_id,
        product_id=product_id,
        document_version_id=document_version_id,
        certificate_id=certificate_id,
        evidence_metadata_json=metadata or {},
    )
    db.add(evidence)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence",
        entity_id=evidence.id,
        action="evidence.created",
        payload={
            "evidence_type": evidence_type.value,
            "status": computed_status.value,
            "reference": reference,
            "issuer": issuer,
            "has_document": document_version_id is not None,
            "supplier_id": str(supplier_id) if supplier_id else None,
            "product_id": str(product_id) if product_id else None,
        },
        request_id=request_id,
    )
    return evidence


def update_evidence(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    evidence_id: UUID,
    changes: dict[str, Any],
    request_id: str | None = None,
) -> Evidence:
    _require_tenant_context(db, organization_id)
    evidence = get_evidence(db, organization_id=organization_id, evidence_id=evidence_id, lock=True)

    allowed = {
        "evidence_type": "evidence_type",
        "status": "status",
        "reference": "reference",
        "issuer": "issuer",
        "issued_on": "issued_on",
        "expires_on": "expires_on",
        "product_scope": "product_scope",
        "supplier_id": "supplier_id",
        "product_id": "product_id",
        "document_version_id": "document_version_id",
        "certificate_id": "certificate_id",
        "evidence_metadata": "evidence_metadata_json",
    }
    unknown = set(changes).difference(allowed)
    if unknown:
        raise EvidenceInputError("Champs de mise à jour de preuve non reconnus.")

    target_supplier = changes.get("supplier_id", evidence.supplier_id)
    target_product = changes.get("product_id", evidence.product_id)
    target_doc = changes.get("document_version_id", evidence.document_version_id)
    target_cert = changes.get("certificate_id", evidence.certificate_id)
    _validate_evidence_relations(
        db,
        organization_id=organization_id,
        supplier_id=target_supplier,
        product_id=target_product,
        document_version_id=target_doc,
        certificate_id=target_cert,
    )

    changed_fields: list[str] = []
    for api_field, col_name in allowed.items():
        if api_field in changes and getattr(evidence, col_name) != changes[api_field]:
            setattr(evidence, col_name, changes[api_field])
            changed_fields.append(api_field)

    if not changed_fields:
        return evidence

    # Recalculate expired status if expiration date was updated
    if evidence.expires_on and evidence.expires_on < today_date() and evidence.status != EvidenceStatus.EXPIRED:
        evidence.status = EvidenceStatus.EXPIRED
        changed_fields.append("status")

    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence",
        entity_id=evidence.id,
        action="evidence.updated",
        payload={"fields": sorted(changed_fields)},
        request_id=request_id,
    )
    return evidence


def delete_evidence(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    evidence_id: UUID,
    request_id: str | None = None,
) -> None:
    _require_tenant_context(db, organization_id)
    evidence = get_evidence(db, organization_id=organization_id, evidence_id=evidence_id, include_archived=True, lock=True)
    if evidence.deleted_at is not None:
        return

    evidence.deleted_at = utcnow()
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence",
        entity_id=evidence.id,
        action="evidence.deleted",
        payload={"evidence_type": evidence.evidence_type.value},
        request_id=request_id,
    )


def list_evidence(
    db: Session,
    *,
    organization_id: UUID,
    limit: int = 20,
    cursor: str | None = None,
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    evidence_type: EvidenceType | None = None,
    status: EvidenceStatus | None = None,
    query: str | None = None,
) -> EvidencePage:
    _require_tenant_context(db, organization_id)
    sort_key = func.coalesce(Evidence.reference, func.coalesce(Evidence.issuer, "pre"))
    statement = select(Evidence).where(
        Evidence.organization_id == organization_id,
        Evidence.deleted_at.is_(None),
    )

    if supplier_id is not None:
        statement = statement.where(Evidence.supplier_id == supplier_id)
    if product_id is not None:
        statement = statement.where(Evidence.product_id == product_id)
    if evidence_type is not None:
        statement = statement.where(Evidence.evidence_type == evidence_type)
    if status is not None:
        statement = statement.where(Evidence.status == status)

    if query:
        norm_q = query.strip().lower()
        if norm_q:
            statement = statement.where(
                or_(
                    func.lower(func.coalesce(Evidence.reference, "")).contains(norm_q),
                    func.lower(func.coalesce(Evidence.issuer, "")).contains(norm_q),
                    func.lower(func.coalesce(Evidence.product_scope, "")).contains(norm_q),
                )
            )

    after_key, after_id = _decode_cursor(cursor)
    if after_key is not None and after_id is not None:
        statement = statement.where(
            or_(
                sort_key > after_key,
                and_(sort_key == after_key, Evidence.id > after_id),
            )
        )

    rows = list(
        db.scalars(
            statement.order_by(sort_key.asc(), Evidence.id.asc()).limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]

    next_cursor = None
    if has_more and items:
        last = items[-1]
        last_key = (last.reference or last.issuer or "pre").lower()
        next_cursor = _encode_cursor(last_key, last.id)

    return EvidencePage(items=items, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Claim ↔ Evidence Link Management and Rationale Evaluation
# ---------------------------------------------------------------------------


def _compute_deterministic_rationale(
    claim: Claim,
    evidence: Evidence,
    as_of: date,
) -> tuple[EvidenceStatus, str]:
    """Compute explicit, non-probabilistic coverage and rationale."""
    reasons: list[str] = []

    # 1. Date Check
    if evidence.expires_on and evidence.expires_on < as_of:
        return (
            EvidenceStatus.EXPIRED,
            f"Preuve expirée depuis le {evidence.expires_on.isoformat()} (date d’évaluation : {as_of.isoformat()}). "
            f"Type : {evidence.evidence_type.value}, référence : {evidence.reference or 'non spécifiée'}.",
        )

    # 2. Type compatibility
    claim_type = claim.claim_type.lower()
    if "carbon" in claim_type or "neutr" in claim_type:
        if evidence.evidence_type not in {
            EvidenceType.GHG_INVENTORY,
            EvidenceType.GHG_REDUCTION_PLAN,
            EvidenceType.CARBON_OFFSET,
            EvidenceType.LCA_REPORT,
            EvidenceType.CERTIFICATE,
        }:
            reasons.append("Type de preuve non prioritaire pour une allégation carbone (ACV ou bilan GES attendu).")
    elif "recycl" in claim_type:
        if evidence.evidence_type not in {
            EvidenceType.RECYCLING_ROUTE,
            EvidenceType.CERTIFICATE,
            EvidenceType.LAB_REPORT,
            EvidenceType.STANDARD,
            EvidenceType.OTHER,
        }:
            reasons.append("Justificatif de filière ou certificat attendu pour la recyclabilité.")
    elif "biodegrad" in claim_type:
        reasons.append("Note AGEC : Aucune preuve technique ne lève l’interdiction légale d’apposer « biodégradable » sur un produit ou emballage neuf.")

    # 3. Scope Matching (if product_scope is explicitly constrained and does not overlap with claim terms)
    if evidence.product_scope:
        scope_lower = evidence.product_scope.lower()
        claim_lower = claim.claim_text.lower()
        # Common packaging / domain terms
        packaging_synonyms = {"emballage", "bouteille", "flacon", "packaging", "barquette", "pot", "film", "etiquette", "bouchon", "carton"}
        has_pkg_match = any(t in scope_lower for t in packaging_synonyms) and any(t in claim_lower for t in packaging_synonyms)
        has_direct_word_match = any(w in scope_lower for w in claim_lower.split() if len(w) > 3)
        has_cat_match = claim.category.lower() in scope_lower or claim.claim_type.lower() in scope_lower

        if not (has_pkg_match or has_direct_word_match or has_cat_match):
            reasons.append(
                f"Périmètre de preuve restreint ({evidence.product_scope}) ne couvrant pas explicitement l’allégation."
            )

    if not reasons:
        validity_text = f"valide jusqu’au {evidence.expires_on.isoformat()}" if evidence.expires_on else "sans date de fin déclarée"
        issuer_text = f"délivrée par {evidence.issuer}" if evidence.issuer else "déclarée"
        return (
            EvidenceStatus.PRESENT,
            f"Preuve {evidence.evidence_type.value} {issuer_text} ({validity_text}), référence {evidence.reference or 'N/A'}. "
            f"Couverture établie pour l’allégation « {claim.claim_text[:80]} ».",
        )

    return (
        EvidenceStatus.PARTIAL,
        " ; ".join(reasons),
    )


def link_claim_evidence(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    claim_id: UUID,
    evidence_id: UUID,
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
    coverage_status: EvidenceStatus | None = None,
    validity_as_of: date | None = None,
    confidence_score: float | None = None,
    rationale: str | None = None,
    request_id: str | None = None,
) -> EvidenceLink:
    _require_tenant_context(db, organization_id)

    claim = db.scalar(
        select(Claim).where(
            Claim.organization_id == organization_id,
            Claim.id == claim_id,
        )
    )
    if claim is None:
        raise EvidenceNotFoundError("Allégation introuvable ou inaccessible dans cette organisation.")

    evidence = get_evidence(db, organization_id=organization_id, evidence_id=evidence_id)

    eval_date = validity_as_of or today_date()
    computed_status, auto_rationale = _compute_deterministic_rationale(claim, evidence, eval_date)

    # C17 — le rattachement applique **le même examen** que la lecture de couverture,
    # et il cesse d'écrire une décision de machine dans la colonne des déclarations.
    #
    # Deux défauts mesurés :
    #
    # 1. Deux calculs coexistaient. Celui d'ici ne regardait que la date du jour, le type
    #    de preuve et un périmètre en texte libre ; celui de `app.evidence.coverage` compare
    #    le produit réel, le fournisseur, la famille d'allégation et la date choisie. Un
    #    certificat d'un **autre produit** était donc enregistré « present », tandis que la
    #    lecture de couverture le classait « out_of_scope ». Deux vérités pour une pièce.
    #
    # 2. Le statut calculé était écrit dans `coverage_status`, la même colonne où un
    #    relecteur déclare sa décision. La lecture de couverture relisait ensuite cette
    #    valeur comme une **déclaration humaine** (« declared_state ») et la retenait
    #    comme la plus restrictive : le produit se contredisait lui-même, constatait
    #    « covered » sur les faits et « partial » à cause de sa propre écriture. Le lien
    #    n'écrit donc plus que ce qu'un humain déclare ; sans déclaration, il reste
    #    `PENDING`, et le constat est rendu séparément (`observed_state`, motifs).
    analysis = db.scalar(
        select(Analysis)
        .join(AnalysisVersion, AnalysisVersion.analysis_id == Analysis.id)
        .where(
            Analysis.organization_id == organization_id,
            AnalysisVersion.organization_id == organization_id,
            AnalysisVersion.id == claim.analysis_version_id,
        )
    )
    observed_state_value: str | None = None
    if analysis is not None:
        try:
            claim_family = ClaimType(claim.claim_type)
        except ValueError:
            claim_family = None
        observed = examine_evidence(
            evidence,
            as_of=eval_date,
            claim_type=claim_family,
            analysis_product_id=analysis.product_id,
            analysis_supplier_id=analysis.supplier_id,
        )
        observed_state_value = observed_state(observed)
        blocking = [
            finding.message for finding in observed.findings if finding.severity == "blocking"
        ]
        examination = " ".join(blocking) if blocking else auto_rationale
        if coverage_status is None:
            # Aucune déclaration : le motif dit le constat, et rappelle qu'il n'y a pas eu
            # de décision humaine sur ce lien.
            auto_rationale = (
                f"Constat de la pièce à la date d'évaluation ({eval_date.isoformat()}) : "
                f"« {observed_state_value} ». {examination} "
                "Aucune décision humaine n'a été déclarée sur ce rattachement."
            )

    # Le lien ne porte qu'une déclaration humaine — **une seule règle d'écriture**, ici.
    # Sans déclaration, `PENDING`, jamais le verdict calculé : la valeur calculée relue comme
    # une déclaration humaine plafonnait le constat (« covered » sur les faits, « partial »
    # à cause de l'écriture du produit lui-même). Le constat vit dans `observed_state`.
    final_coverage = coverage_status if coverage_status is not None else EvidenceStatus.PENDING
    final_rationale = rationale or auto_rationale
    declared = coverage_status is not None

    existing = db.scalar(
        select(EvidenceLink).where(
            EvidenceLink.organization_id == organization_id,
            EvidenceLink.claim_id == claim_id,
            EvidenceLink.evidence_id == evidence_id,
        ).with_for_update()
    )

    if existing is not None:
        existing.relation = relation
        existing.coverage_status = final_coverage
        existing.validity_as_of = eval_date
        existing.confidence_score = confidence_score
        existing.rationale = final_rationale
        # Un rattachement sans déclaration n'est pas une relecture : ne pas l'horodater
        # comme telle. `reviewed_at` reste la trace d'une décision humaine.
        if declared:
            existing.reviewed_by_user_id = actor_user_id
            existing.reviewed_at = utcnow()
        db.flush()
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            entity_type="evidence_link",
            entity_id=existing.id,
            action="claim.evidence_link_updated",
            payload={
                "claim_id": str(claim_id),
                "evidence_id": str(evidence_id),
                "declared_coverage_status": final_coverage.value if declared else None,
                "observed_state": observed_state_value,
            },
            request_id=request_id,
        )
        existing.observed_state_value = observed_state_value
        return existing

    link = EvidenceLink(
        id=uuid4(),
        organization_id=organization_id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation=relation,
        coverage_status=final_coverage,
        validity_as_of=eval_date,
        confidence_score=confidence_score,
        rationale=final_rationale,
        reviewed_by_user_id=actor_user_id if declared else None,
        reviewed_at=utcnow() if declared else None,
    )
    db.add(link)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_link",
        entity_id=link.id,
        action="claim.evidence_linked",
        payload={
            "claim_id": str(claim_id),
            "evidence_id": str(evidence_id),
            "relation": relation.value,
            "declared_coverage_status": final_coverage.value if declared else None,
            "observed_state": observed_state_value,
        },
        request_id=request_id,
    )
    link.observed_state_value = observed_state_value  # présentation seule, non persisté
    return link


def update_claim_evidence_link(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    link_id: UUID,
    changes: dict[str, Any],
    request_id: str | None = None,
) -> EvidenceLink:
    _require_tenant_context(db, organization_id)
    link = db.scalar(
        select(EvidenceLink).where(
            EvidenceLink.organization_id == organization_id,
            EvidenceLink.id == link_id,
        ).with_for_update()
    )
    if link is None:
        raise EvidenceNotFoundError("Lien de preuve introuvable ou inaccessible.")

    allowed = {"relation", "coverage_status", "validity_as_of", "confidence_score", "rationale"}
    unknown = set(changes).difference(allowed)
    if unknown:
        raise EvidenceInputError("Champs de mise à jour de lien non reconnus.")

    for field in allowed:
        if field in changes:
            setattr(link, field, changes[field])

    link.reviewed_by_user_id = actor_user_id
    link.reviewed_at = utcnow()
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_link",
        entity_id=link.id,
        action="claim.evidence_link_updated",
        payload={"fields": sorted(changes.keys())},
        request_id=request_id,
    )
    return link


def unlink_claim_evidence(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    link_id: UUID,
    request_id: str | None = None,
) -> None:
    _require_tenant_context(db, organization_id)
    link = db.scalar(
        select(EvidenceLink).where(
            EvidenceLink.organization_id == organization_id,
            EvidenceLink.id == link_id,
        ).with_for_update()
    )
    if link is None:
        return

    claim_id = str(link.claim_id)
    evidence_id = str(link.evidence_id)
    db.delete(link)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_link",
        entity_id=link.id,
        action="claim.evidence_unlinked",
        payload={"claim_id": claim_id, "evidence_id": evidence_id},
        request_id=request_id,
    )


def attach_observed_states(
    db: Session,
    *,
    organization_id: UUID,
    links: list[EvidenceLink],
) -> None:
    """Renseigne sur chaque lien le constat C17 de sa pièce (affichage seul, écriture nulle).

    Le lien porte la **déclaration** d'un humain ; ce constat dit ce que l'examen de la pièce
    observe. Les deux sont rendus côte à côte pour qu'une absence de décision ne se lise pas
    comme une preuve acceptée — le défaut corrigé en C17.
    """

    contexts: dict[UUID, tuple[ClaimType | None, UUID | None, UUID | None]] = {}
    for link in links:
        claim = db.scalar(
            select(Claim).where(
                Claim.organization_id == organization_id,
                Claim.id == link.claim_id,
            )
        )
        if claim is None or link.evidence is None:
            continue
        key = claim.analysis_version_id
        if key not in contexts:
            analysis = db.scalar(
                select(Analysis)
                .join(AnalysisVersion, AnalysisVersion.analysis_id == Analysis.id)
                .where(
                    Analysis.organization_id == organization_id,
                    AnalysisVersion.organization_id == organization_id,
                    AnalysisVersion.id == key,
                )
            )
            try:
                family = ClaimType(claim.claim_type)
            except ValueError:
                family = None
            contexts[key] = (
                family,
                analysis.product_id if analysis else None,
                analysis.supplier_id if analysis else None,
            )
        family, product_id, supplier_id = contexts[key]
        link.observed_state_value = observed_state(
            examine_evidence(
                link.evidence,
                as_of=link.validity_as_of or today_date(),
                claim_type=family,
                analysis_product_id=product_id,
                analysis_supplier_id=supplier_id,
            )
        )


def list_claim_evidence_links(
    db: Session,
    *,
    organization_id: UUID,
    claim_id: UUID,
) -> list[EvidenceLink]:
    _require_tenant_context(db, organization_id)
    return list(
        db.scalars(
            select(EvidenceLink)
            .where(
                EvidenceLink.organization_id == organization_id,
                EvidenceLink.claim_id == claim_id,
            )
            .order_by(EvidenceLink.created_at.asc())
        ).all()
    )


def get_analysis_evidence_matrix(
    db: Session,
    *,
    organization_id: UUID,
    analysis_id: UUID,
    version_number: int | None = None,
    as_of: date | None = None,
) -> tuple[Analysis, AnalysisVersion, list[ClaimEvidenceEvaluation]]:
    """Vue historique : la couverture d'une version, présentée par allégation.

    C17 — cette fonction recalculait la couverture selon **sa propre règle** (un lien marqué
    « présent » suffisait ; une seule pièce périmée périmait toute l'allégation), en parallèle
    de `app.evidence.coverage`. Deux règles pour une même question finissent par diverger :
    la matrice et la lecture de couverture pouvaient annoncer deux états différents pour la
    même pièce. La matrice délègue donc au calcul unique et se contente de le présenter dans
    son format d'origine (`EvidenceStatus`).
    """

    _require_tenant_context(db, organization_id)

    analysis = db.scalar(
        select(Analysis).where(
            Analysis.organization_id == organization_id,
            Analysis.id == analysis_id,
            Analysis.deleted_at.is_(None),
        )
    )
    if analysis is None:
        raise EvidenceNotFoundError("Dossier d’analyse introuvable ou inaccessible.")

    ver_stmt = select(AnalysisVersion).where(
        AnalysisVersion.organization_id == organization_id,
        AnalysisVersion.analysis_id == analysis_id,
    )
    if version_number is not None:
        ver_stmt = ver_stmt.where(AnalysisVersion.version_number == version_number)
    else:
        ver_stmt = ver_stmt.order_by(AnalysisVersion.version_number.desc())

    version = db.scalar(ver_stmt)
    if version is None:
        raise EvidenceNotFoundError("Version d’analyse introuvable.")

    links_by_claim: dict[UUID, list[EvidenceLink]] = {}
    for link in db.scalars(
        select(EvidenceLink)
        .where(
            EvidenceLink.organization_id == organization_id,
            EvidenceLink.claim_id.in_(
                select(Claim.id).where(
                    Claim.organization_id == organization_id,
                    Claim.analysis_version_id == version.id,
                )
            ),
        )
        .order_by(EvidenceLink.created_at.asc())
    ).all():
        links_by_claim.setdefault(link.claim_id, []).append(link)

    evaluations: list[ClaimEvidenceEvaluation] = []
    for coverage in coverage_for_analysis_version(
        db,
        organization_id=organization_id,
        analysis=analysis,
        version=version,
        as_of=as_of or today_date(),
        with_suggestions=False,
    ):
        blocking = [
            finding.message for finding in coverage.findings if finding.severity == "blocking"
        ]
        evaluations.append(
            ClaimEvidenceEvaluation(
                claim_id=coverage.claim.id,
                coverage_status=OBSERVED_STATE_TO_EVIDENCE_STATUS[coverage.state],
                is_sufficient=coverage.is_sufficient,
                explanation=blocking[0] if blocking else coverage.explanation,
                links=links_by_claim.get(coverage.claim.id, []),
            )
        )

    return analysis, version, evaluations
