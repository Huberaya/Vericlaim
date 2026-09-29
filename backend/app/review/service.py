"""Transactional service for human reviews/validations and supplier evidence requests."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.catalog.service import get_product, get_supplier
from app.core.database import sha256_json
from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    AnalysisVersion,
    Claim,
    EvidenceRequest,
    EvidenceRequestStatus,
    Product,
    Supplier,
    User,
    Validation,
    ValidationDecision,
)


class ReviewServiceError(RuntimeError):
    """Base exception for review and request operations."""


class ReviewNotFoundError(ReviewServiceError):
    """Raised when a validation or request is missing or in a foreign tenant."""


class ReviewConflictError(ReviewServiceError):
    """Raised on state or invariant conflicts."""


class ReviewInputError(ReviewServiceError):
    """Raised on invalid inputs."""


@dataclass(frozen=True)
class EvidenceRequestPage:
    items: list[EvidenceRequest]
    next_cursor: str | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_tenant_context(db: Session, organization_id: UUID) -> None:
    current = db.info.get("current_organization_id")
    if current is not None and current != organization_id:
        raise ReviewConflictError(
            "Le contexte organisationnel actif ne correspond pas à l’opération de revue."
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
        raise ReviewInputError("Curseur de pagination invalide.") from exc


# ---------------------------------------------------------------------------
# Human Review / Validations
# ---------------------------------------------------------------------------


def record_validation(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    analysis_version_id: UUID,
    claim_id: UUID | None,
    decision: ValidationDecision,
    comment: str | None = None,
    rationale: str | None = None,
    request_id: str | None = None,
) -> Validation:
    _require_tenant_context(db, organization_id)

    version = db.scalar(
        select(AnalysisVersion).where(
            AnalysisVersion.organization_id == organization_id,
            AnalysisVersion.id == analysis_version_id,
        )
    )
    if version is None:
        raise ReviewNotFoundError("Version d’analyse introuvable ou inaccessible.")

    if claim_id is not None:
        claim = db.scalar(
            select(Claim).where(
                Claim.organization_id == organization_id,
                Claim.analysis_version_id == analysis_version_id,
                Claim.id == claim_id,
            )
        )
        if claim is None:
            raise ReviewNotFoundError("Allégation introuvable dans cette version d’analyse.")

    # Check for existing validation for the same version and claim
    existing = db.scalar(
        select(Validation).where(
            Validation.organization_id == organization_id,
            Validation.analysis_version_id == analysis_version_id,
            Validation.claim_id == claim_id,
        ).with_for_update()
    )

    now = utcnow()
    if existing is not None:
        existing.decision = decision
        existing.reviewer_user_id = actor_user_id
        existing.comment = comment
        existing.rationale = rationale
        existing.decided_at = now
        db.flush()
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            entity_type="validation",
            entity_id=existing.id,
            action="validation.updated",
            payload={
                "analysis_version_id": str(analysis_version_id),
                "claim_id": str(claim_id) if claim_id else None,
                "decision": decision.value,
            },
            request_id=request_id,
        )
        return existing

    validation = Validation(
        id=uuid4(),
        organization_id=organization_id,
        analysis_version_id=analysis_version_id,
        claim_id=claim_id,
        decision=decision,
        reviewer_user_id=actor_user_id,
        comment=comment,
        rationale=rationale,
        decided_at=now,
    )
    db.add(validation)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="validation",
        entity_id=validation.id,
        action="validation.created",
        payload={
            "analysis_version_id": str(analysis_version_id),
            "claim_id": str(claim_id) if claim_id else None,
            "decision": decision.value,
        },
        request_id=request_id,
    )
    return validation


def list_validations(
    db: Session,
    *,
    organization_id: UUID,
    analysis_version_id: UUID,
) -> list[Validation]:
    _require_tenant_context(db, organization_id)
    return list(
        db.scalars(
            select(Validation)
            .where(
                Validation.organization_id == organization_id,
                Validation.analysis_version_id == analysis_version_id,
            )
            .order_by(Validation.created_at.asc())
        ).all()
    )


# ---------------------------------------------------------------------------
# Supplier Evidence Requests
# ---------------------------------------------------------------------------


def create_evidence_request(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    supplier_id: UUID,
    product_id: UUID | None,
    claim_id: UUID | None,
    subject: str,
    message: str,
    requested_items: list[dict[str, Any]],
    due_at: datetime | None = None,
    request_id: str | None = None,
) -> EvidenceRequest:
    _require_tenant_context(db, organization_id)

    supplier = get_supplier(db, organization_id=organization_id, supplier_id=supplier_id)
    if product_id is not None:
        product = get_product(db, organization_id=organization_id, product_id=product_id)
        if product.supplier_id != supplier.id:
            raise ReviewConflictError("Incohérence : le produit n’appartient pas au fournisseur sélectionné.")

    if claim_id is not None:
        claim = db.scalar(
            select(Claim).where(
                Claim.organization_id == organization_id,
                Claim.id == claim_id,
            )
        )
        if claim is None:
            raise ReviewNotFoundError("Allégation introuvable dans cette organisation.")

    req = EvidenceRequest(
        id=uuid4(),
        organization_id=organization_id,
        supplier_id=supplier_id,
        product_id=product_id,
        claim_id=claim_id,
        status=EvidenceRequestStatus.DRAFT,
        subject=subject,
        message=message,
        requested_items_json=requested_items or [],
        due_at=due_at,
        created_by_user_id=actor_user_id,
    )
    db.add(req)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_request",
        entity_id=req.id,
        action="evidence_request.created",
        payload={
            "supplier_id": str(supplier_id),
            "product_id": str(product_id) if product_id else None,
            "claim_id": str(claim_id) if claim_id else None,
            "status": req.status.value,
            "requested_items_count": len(requested_items),
        },
        request_id=request_id,
    )
    return req


def get_evidence_request(
    db: Session,
    *,
    organization_id: UUID,
    request_id: UUID,
    include_archived: bool = False,
    lock: bool = False,
) -> EvidenceRequest:
    _require_tenant_context(db, organization_id)
    statement = select(EvidenceRequest).where(
        EvidenceRequest.organization_id == organization_id,
        EvidenceRequest.id == request_id,
    )
    if not include_archived:
        statement = statement.where(EvidenceRequest.deleted_at.is_(None))
    if lock:
        statement = statement.with_for_update()
    req = db.scalar(statement)
    if req is None:
        raise ReviewNotFoundError("Demande de preuve introuvable ou inaccessible.")
    return req


def update_evidence_request(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    request_id: UUID,
    changes: dict[str, Any],
    req_id: str | None = None,
) -> EvidenceRequest:
    _require_tenant_context(db, organization_id)
    req = get_evidence_request(db, organization_id=organization_id, request_id=request_id, lock=True)

    allowed = {"status", "subject", "message", "requested_items", "due_at"}
    unknown = set(changes).difference(allowed)
    if unknown:
        raise ReviewInputError("Champs de mise à jour non autorisés.")

    changed_fields: list[str] = []
    if "status" in changes and req.status != changes["status"]:
        req.status = changes["status"]
        changed_fields.append("status")
    if "subject" in changes and req.subject != changes["subject"]:
        req.subject = changes["subject"]
        changed_fields.append("subject")
    if "message" in changes and req.message != changes["message"]:
        req.message = changes["message"]
        changed_fields.append("message")
    if "requested_items" in changes and req.requested_items_json != changes["requested_items"]:
        req.requested_items_json = changes["requested_items"]
        changed_fields.append("requested_items")
    if "due_at" in changes and req.due_at != changes["due_at"]:
        req.due_at = changes["due_at"]
        changed_fields.append("due_at")

    if not changed_fields:
        return req

    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_request",
        entity_id=req.id,
        action="evidence_request.updated",
        payload={"fields": sorted(changed_fields)},
        request_id=req_id,
    )
    return req


def send_evidence_request(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    request_id: UUID,
    req_id: str | None = None,
) -> EvidenceRequest:
    _require_tenant_context(db, organization_id)
    req = get_evidence_request(db, organization_id=organization_id, request_id=request_id, lock=True)
    req.status = EvidenceRequestStatus.SENT
    req.sent_at = utcnow()
    if not req.due_at:
        req.due_at = utcnow() + timedelta(days=15)
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_request",
        entity_id=req.id,
        action="evidence_request.sent",
        payload={"sent_at": req.sent_at.isoformat(), "due_at": req.due_at.isoformat() if req.due_at else None},
        request_id=req_id,
    )
    return req


def remind_evidence_request(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    request_id: UUID,
    req_id: str | None = None,
) -> EvidenceRequest:
    _require_tenant_context(db, organization_id)
    req = get_evidence_request(db, organization_id=organization_id, request_id=request_id, lock=True)
    req.last_reminded_at = utcnow()
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_request",
        entity_id=req.id,
        action="evidence_request.reminded",
        payload={"last_reminded_at": req.last_reminded_at.isoformat()},
        request_id=req_id,
    )
    return req


def delete_evidence_request(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    request_id: UUID,
    req_id: str | None = None,
) -> None:
    _require_tenant_context(db, organization_id)
    req = get_evidence_request(db, organization_id=organization_id, request_id=request_id, include_archived=True, lock=True)
    if req.deleted_at is not None:
        return
    req.deleted_at = utcnow()
    req.status = EvidenceRequestStatus.CANCELLED
    db.flush()

    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="evidence_request",
        entity_id=req.id,
        action="evidence_request.deleted",
        payload={},
        request_id=req_id,
    )


def list_evidence_requests(
    db: Session,
    *,
    organization_id: UUID,
    limit: int = 20,
    cursor: str | None = None,
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    status: EvidenceRequestStatus | None = None,
) -> EvidenceRequestPage:
    _require_tenant_context(db, organization_id)
    sort_key = EvidenceRequest.created_at
    statement = select(EvidenceRequest).where(
        EvidenceRequest.organization_id == organization_id,
        EvidenceRequest.deleted_at.is_(None),
    )
    if supplier_id is not None:
        statement = statement.where(EvidenceRequest.supplier_id == supplier_id)
    if product_id is not None:
        statement = statement.where(EvidenceRequest.product_id == product_id)
    if status is not None:
        statement = statement.where(EvidenceRequest.status == status)

    after_key, after_id = _decode_cursor(cursor)
    if after_key is not None and after_id is not None:
        try:
            after_dt = datetime.fromisoformat(after_key)
            statement = statement.where(
                or_(
                    sort_key < after_dt,
                    and_(sort_key == after_dt, EvidenceRequest.id < after_id),
                )
            )
        except ValueError:
            pass

    rows = list(
        db.scalars(
            statement.order_by(sort_key.desc(), EvidenceRequest.id.desc()).limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]

    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(last.created_at.isoformat(), last.id)

    return EvidenceRequestPage(items=items, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Template Generation for Supplier Inquiries
# ---------------------------------------------------------------------------


def generate_supplier_request_template(
    db: Session,
    *,
    organization_id: UUID,
    claim_id: UUID,
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
    target_evidence_type: str | None = None,
) -> dict[str, Any]:
    """Craft deterministic, professional compliance inquiry message for buyers."""
    _require_tenant_context(db, organization_id)

    claim = db.scalar(
        select(Claim).where(
            Claim.organization_id == organization_id,
            Claim.id == claim_id,
        )
    )
    if claim is None:
        raise ReviewNotFoundError("Allégation introuvable.")

    supplier_name = "Fournisseur"
    if supplier_id:
        try:
            supp = get_supplier(db, organization_id=organization_id, supplier_id=supplier_id)
            supplier_name = supp.legal_name
        except ReviewNotFoundError:
            pass

    product_ref = "la référence concernée"
    if product_id:
        try:
            prod = get_product(db, organization_id=organization_id, product_id=product_id)
            product_ref = f"{prod.name} (Réf: {prod.reference})"
        except ReviewNotFoundError:
            pass

    claim_type = claim.claim_type.lower()
    subject = f"Demande de justificatifs réglementaires — Allégation « {claim.claim_text[:40]} » ({product_ref})"

    requested_items: list[dict[str, Any]] = []
    if "recycl" in claim_type:
        requested_items.append({
            "type": "recycling_route",
            "name": "Attestation de filière de recyclage effective",
            "description": "Justificatif d’accès à la collecte, tri et recyclage industriel (ex: attestation Citeo, Valorplast ou labellisation filière).",
            "regulatory_basis": "Code de l'environnement (R. 541-228 VI) et ISO 14021:2026",
        })
    elif "carbon" in claim_type or "neutr" in claim_type:
        requested_items.append({
            "type": "lca_report",
            "name": "Bilan des émissions GES & Rapport ACV ISO 14044",
            "description": "Bilan d'émissions directes et indirectes du produit, plan de décarbonation et justificatifs de compensation résiduelle.",
            "regulatory_basis": "Articles L. 229-68 et L. 229-69 du Code de l'environnement / Directive (UE) 2024/825",
        })
    else:
        requested_items.append({
            "type": target_evidence_type or "certificate",
            "name": "Certificat ou rapport d'essais indépendant",
            "description": f"Preuve technique datée et en cours de validité établissant la performance environnementale pour « {claim.claim_text} ».",
            "regulatory_basis": "Directive (UE) 2024/825 et Code de la consommation (L. 121-2)",
        })

    msg_lines = [
        f"Bonjour {supplier_name},",
        "",
        f"Dans le cadre de notre démarche de conformité réglementaire et de pré-audit des allégations environnementales sur {product_ref}, nous avons identifié la formulation suivante figurant sur vos documents ou supports produits :",
        "",
        f"« {claim.claim_text} »",
        "",
        "Afin de garantir la stricte conformité de cette allégation au regard de la réglementation applicable (Directive européenne 2024/825 et dispositions du Code de l'environnement), nous vous remercions de bien vouloir nous transmettre les pièces justificatives suivantes :",
        "",
    ]

    for idx, item in enumerate(requested_items, 1):
        msg_lines.append(f"{idx}. {item['name']} : {item['description']} (Cadre : {item['regulatory_basis']})")

    msg_lines.extend([
        "",
        "Merci de nous faire parvenir ces éléments sous 15 jours ouvrés.",
        "Nous vous remercions par avance pour votre diligence et votre collaboration.",
        "",
        "Cordialement,",
        "La Direction Achats & RSE / Conformité",
    ])

    return {
        "subject": subject,
        "message": "\n".join(msg_lines),
        "requested_items": requested_items,
        "suggested_due_days": 15,
    }
