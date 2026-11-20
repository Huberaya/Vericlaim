"""Turn the persisted evidence registry into an engine evidence dossier.

The evaluator takes an :class:`EvidenceDossier` built from client-supplied JSON.
A persisted analysis must instead be evaluated against ``evidence`` rows that the
tenant actually recorded, otherwise a verdict could rest on proof that no longer
exists. This module is the single conversion point.

Two rules govern it, and both are fail-closed:

* Nothing is invented. A persisted row only becomes a typed dossier item when the
  fields that type requires are really present. A ``certificate`` row without a
  scheme and a licence number becomes a generic ``other`` item, so the proof
  validator reports missing evidence instead of granting a Safe Harbor.
* Anything excluded is recorded. Silently dropping proof would make a verdict
  stricter without saying why; the caller persists the exclusions so a report can
  state "3 pièces écartées: 2 expirées, 1 rejetée".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.domain import Analysis, Evidence, EvidenceStatus, EvidenceType
from app.models.legal_types import (
    CarbonOffsetEvidence,
    EvidenceDossier,
    GHGInventoryEvidence,
    GHGReductionPlanEvidence,
    LcaEvidence,
    RecyclingRouteEvidence,
)
from app.models.legal_types import EcolabelEvidence, OtherEvidence

# Statuses whose proof must not be treated as usable. ``REJECTED`` was refused by
# a human; ``MISSING`` means the row is a placeholder for an outstanding request.
_UNUSABLE_STATUSES = frozenset({EvidenceStatus.REJECTED, EvidenceStatus.MISSING})


@dataclass(frozen=True)
class EvidenceSelection:
    """The dossier handed to the evaluator, plus what was left out and why."""

    dossier: EvidenceDossier
    included: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, Any], ...]


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "oui"}
    return False


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _base_fields(row: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": str(row.id),
        "reference": row.reference,
        "file_name": None,
        "product_scope": row.product_scope,
        "claim_excerpt": None,
        "issued_on": row.issued_on,
        "expires_on": row.expires_on,
    }


def _to_dossier_item(row: Evidence) -> Any:
    """Map one persisted row onto a typed dossier item, or ``None`` if unsupported."""
    meta: dict[str, Any] = dict(row.evidence_metadata_json or {})
    base = _base_fields(row)

    if row.evidence_type == EvidenceType.LCA_REPORT:
        return LcaEvidence(
            **base,
            standard=_as_text(meta.get("standard")),
            functional_unit=_as_text(meta.get("functional_unit")),
            system_boundary=_as_text(meta.get("system_boundary")),
            impact_categories=_as_str_list(meta.get("impact_categories")),
            comparative=_as_bool(meta.get("comparative")),
            comparison_product=_as_text(meta.get("comparison_product")),
            same_functional_unit=_as_bool(meta.get("same_functional_unit")),
            same_system_boundary=_as_bool(meta.get("same_system_boundary")),
        )

    if row.evidence_type == EvidenceType.RECYCLING_ROUTE:
        return RecyclingRouteEvidence(
            **base,
            material_or_component=_as_text(meta.get("material_or_component")),
            territories=_as_str_list(meta.get("territories")),
            collection_available=_as_bool(meta.get("collection_available")),
            sorting_available=_as_bool(meta.get("sorting_available")),
            consumer_access=_as_bool(meta.get("consumer_access")),
            industrial_processing_available=_as_bool(meta.get("industrial_processing_available")),
            coverage_percent=_as_decimal(meta.get("coverage_percent")),
        )

    if row.evidence_type == EvidenceType.GHG_INVENTORY:
        return GHGInventoryEvidence(
            **base,
            standard=_as_text(meta.get("standard")),
            includes_direct_emissions=_as_bool(meta.get("includes_direct_emissions")),
            includes_indirect_emissions=_as_bool(meta.get("includes_indirect_emissions")),
            product_lifecycle_scope=_as_text(meta.get("product_lifecycle_scope")),
            public_disclosure_url=_as_text(meta.get("public_disclosure_url")),
        )

    if row.evidence_type == EvidenceType.GHG_REDUCTION_PLAN:
        return GHGReductionPlanEvidence(
            **base,
            avoidance_prioritised=_as_bool(meta.get("avoidance_prioritised")),
            reduction_before_compensation=_as_bool(meta.get("reduction_before_compensation")),
            annual_quantified_targets=_as_bool(meta.get("annual_quantified_targets")),
            public_disclosure_url=_as_text(meta.get("public_disclosure_url")),
        )

    if row.evidence_type == EvidenceType.CARBON_OFFSET:
        return CarbonOffsetEvidence(
            **base,
            standard_or_registry=_as_text(meta.get("standard_or_registry")),
            retirement_reference=_as_text(meta.get("retirement_reference")),
            vintage_year=_as_int(meta.get("vintage_year")),
            residual_emissions_reference=_as_text(meta.get("residual_emissions_reference")),
        )

    if row.evidence_type == EvidenceType.CERTIFICATE:
        # ``EcolabelEvidence`` requires a scheme and a licence number. Without
        # both, the row degrades to a generic item: the validator must not grant
        # a Safe Harbor from a certificate whose identity is unknown.
        scheme = _as_text(meta.get("scheme"))
        license_number = _as_text(meta.get("license_number"))
        if scheme in {"EU_ECOLABEL", "EN_ISO_14024_TYPE_I", "OTHER"} and license_number:
            return EcolabelEvidence(
                **base,
                scheme=scheme,
                license_number=license_number,
                product_identifier=_as_text(meta.get("product_identifier")),
                product_category=_as_text(meta.get("product_category")),
                issuer=row.issuer,
            )
        return OtherEvidence(
            **base,
            description=_as_text(meta.get("description"))
            or f"Certificat déclaré ({row.reference or 'sans référence'}) sans schéma ni numéro de licence exploitables.",
        )

    return OtherEvidence(
        **base,
        description=_as_text(meta.get("description"))
        or f"Pièce de type {row.evidence_type.value}{' — ' + row.reference if row.reference else ''}.",
    )


def select_evidence_for_analysis(
    db: Session,
    *,
    organization_id: UUID,
    analysis: Analysis,
    as_of_date: date,
) -> EvidenceSelection:
    """Build the evidence dossier for one analysis, deterministically.

    Scope: evidence attached to the analysed product, to the analysed supplier, or
    to the organization itself. A product-scoped analysis still sees supplier and
    organization-level proof, because a certificate covering the whole supplier
    legitimately backs that product. The converse is not true: an unrelated
    product's proof is never pulled in.

    Ordering is by ``(evidence_type, reference, id)`` so replaying an analysis
    yields the same dossier and therefore the same verdicts.
    """
    # ``analysis.product_id IS NULL`` must not silently widen the scope to every
    # product, so the disjunction is built explicitly rather than by omission.
    scope_clauses = [and_(Evidence.product_id.is_(None), Evidence.supplier_id.is_(None))]
    if analysis.supplier_id is not None:
        scope_clauses.append(Evidence.supplier_id == analysis.supplier_id)
    if analysis.product_id is not None:
        scope_clauses.append(Evidence.product_id == analysis.product_id)

    rows = list(
        db.scalars(
            select(Evidence)
            .where(
                Evidence.organization_id == organization_id,
                Evidence.deleted_at.is_(None),
                or_(*scope_clauses),
            )
            .order_by(Evidence.evidence_type.asc(), Evidence.reference.asc(), Evidence.id.asc())
        ).all()
    )

    items: list[Any] = []
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_ids: set[UUID] = set()

    for row in rows:
        if row.id in seen_ids:
            continue
        seen_ids.add(row.id)

        if row.status in _UNUSABLE_STATUSES:
            excluded.append(
                {
                    "evidence_id": str(row.id),
                    "evidence_type": row.evidence_type.value,
                    "reference": row.reference,
                    "reason": f"status_{row.status.value}",
                }
            )
            continue
        if row.expires_on is not None and row.expires_on < as_of_date:
            # A lapsed certificate is not usable proof. Excluding it is the
            # stricter choice, and the exclusion is reported.
            excluded.append(
                {
                    "evidence_id": str(row.id),
                    "evidence_type": row.evidence_type.value,
                    "reference": row.reference,
                    "reason": "expired",
                    "expires_on": row.expires_on.isoformat(),
                }
            )
            continue

        items.append(_to_dossier_item(row))
        included.append(
            {
                "evidence_id": str(row.id),
                "evidence_type": row.evidence_type.value,
                "reference": row.reference,
                "status": row.status.value,
                "issued_on": row.issued_on.isoformat() if row.issued_on else None,
                "expires_on": row.expires_on.isoformat() if row.expires_on else None,
            }
        )

    return EvidenceSelection(
        dossier=EvidenceDossier(items=items),
        included=tuple(included),
        excluded=tuple(excluded),
    )
