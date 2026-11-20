"""C17 — État de couverture probatoire, calculé à partir de faits datés.

L'audit a montré que le produit ne voyait ni un certificat **expiré** (cas D) ni un
certificat qui **ne concerne pas le produit** (cas E) : la couverture d'une allégation
était entièrement déclarée par un humain sur le lien (`coverage_status`), sans qu'aucune
vérification ne soit faite sur la preuve elle-même.

Ce module ajoute cette vérification. Règles de conception, toutes vérifiables :

* **Aucune inférence de texte.** Le module ne lit pas le contenu des documents et ne
  devine pas si un certificat « couvre » une allégation. Il compare des **faits
  structurés** : dates de validité, identifiants de produit et de fournisseur, type de
  preuve, type d'allégation. Ce qu'il ne peut pas comparer, il le dit.
* **Une date déclarée n'est pas une date vérifiée.** Le produit n'a pas de registre de
  certificats exploité (le registre par défaut est vide, cf. `proof_validator`). Un
  certificat expiré selon **sa date déclarée** est donc signalé comme tel, avec la
  mention que la date n'a pas été corroborée par une source indépendante.
* **Une absence de date n'est pas une preuve de validité.** Pour les types de preuve qui
  expirent (certificat, rapport de laboratoire, déclaration environnementale), l'absence
  de date de fin produit `validity_unknown`, jamais « valide ».
* **Le calcul est daté.** Tout est évalué à une date explicite (`as_of`), celle de
  l'évaluation de l'analyse, jamais l'horloge implicite : une couverture recalculée dans
  six mois doit pouvoir dire ce qu'elle disait le jour de l'audit.
* **L'état déclaré n'est jamais effacé.** La réponse expose l'état déclaré par le
  relecteur **et** l'état observé ; l'état effectif est le plus restrictif des deux. Une
  contradiction est signalée au lieu d'être arbitrée en silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import (
    Analysis,
    AnalysisVersion,
    Claim,
    Evidence,
    EvidenceLink,
    EvidenceRelation,
    EvidenceStatus,
    EvidenceType,
)
from app.models.legal_types import ClaimType

# ---------------------------------------------------------------------------
# Tables publiées
# ---------------------------------------------------------------------------

#: États possibles d'une couverture. L'ordre est celui de la **précédence** appliquée :
#: `MISSING` l'emporte sur tout (aucune preuve), puis une preuve inutilisable
#: (`EXPIRED`, `OUT_OF_SCOPE`, `NOT_COVERED`), puis une preuve incomplète (`PARTIAL`),
#: puis `COVERED`. `UNKNOWN` est réservé aux cas où le produit ne peut pas trancher.
COVERAGE_STATES: tuple[str, ...] = (
    "missing",
    "expired",
    "out_of_scope",
    "not_covering",
    "partial",
    "covered",
)

#: Types de preuve qui portent une date de fin de validité. Une preuve de ces types
#: sans date de fin ne peut pas être déclarée valide : `validity_unknown`.
EXPIRY_BEARING_TYPES: frozenset[EvidenceType] = frozenset(
    {
        EvidenceType.CERTIFICATE,
        EvidenceType.LAB_REPORT,
        EvidenceType.ENVIRONMENTAL_DECLARATION,
    }
)

#: Type de preuve capable d'étayer chaque famille d'allégation détectée par le moteur
#: (`ClaimType`). Table **à relire par un juriste** avant toute communication : elle
#: exprime ce qui, dans le produit, est considéré comme une pièce pertinente, pas une
#: règle de droit.
ACCEPTED_EVIDENCE_TYPES: dict[ClaimType, tuple[EvidenceType, ...]] = {
    ClaimType.BIODEGRADABLE: (
        EvidenceType.LAB_REPORT,
        EvidenceType.CERTIFICATE,
        EvidenceType.STANDARD,
    ),
    ClaimType.NATURE_FRIENDLY: (
        EvidenceType.CERTIFICATE,
        EvidenceType.LCA_REPORT,
        EvidenceType.ENVIRONMENTAL_DECLARATION,
    ),
    ClaimType.GENERIC_ENVIRONMENTAL: (
        EvidenceType.CERTIFICATE,
        EvidenceType.LCA_REPORT,
        EvidenceType.ENVIRONMENTAL_DECLARATION,
        EvidenceType.LAB_REPORT,
        EvidenceType.STANDARD,
    ),
    ClaimType.CARBON_NEUTRALITY: (
        EvidenceType.GHG_INVENTORY,
        EvidenceType.GHG_REDUCTION_PLAN,
        EvidenceType.CARBON_OFFSET,
        EvidenceType.CERTIFICATE,
    ),
    ClaimType.COMPARATIVE: (
        EvidenceType.LCA_REPORT,
        EvidenceType.LAB_REPORT,
        EvidenceType.ENVIRONMENTAL_DECLARATION,
    ),
    ClaimType.QUANTIFIED_CLIMATE: (
        EvidenceType.GHG_INVENTORY,
        EvidenceType.LCA_REPORT,
        EvidenceType.GHG_REDUCTION_PLAN,
        EvidenceType.CERTIFICATE,
    ),
    ClaimType.RECYCLABLE: (
        EvidenceType.CERTIFICATE,
        EvidenceType.RECYCLING_ROUTE,
        EvidenceType.LCA_REPORT,
        EvidenceType.LAB_REPORT,
    ),
    ClaimType.CERTIFICATION: (
        EvidenceType.CERTIFICATE,
        EvidenceType.STANDARD,
        EvidenceType.ENVIRONMENTAL_DECLARATION,
    ),
}

#: Ce que l'état de couverture signifie, publié tel quel dans l'API.
STATE_MEANINGS: dict[str, str] = {
    "missing": "aucune preuve n'est rattachée à cette allégation",
    "expired": "la preuve rattachée n'est plus valide à la date de l'audit (date déclarée)",
    "out_of_scope": "la preuve rattachée concerne un autre produit ou un autre fournisseur",
    "not_covering": "le type de preuve rattaché ne peut pas étayer cette famille d'allégation",
    "partial": "la preuve rattachée est utilisable mais incomplète pour trancher",
    "covered": "une preuve utilisable, dans le périmètre et valide à la date de l'audit, soutient l'allégation",
}

#: Ce qu'un examen de pièce conclut, ramené au vocabulaire du registre de preuves.
#: Sert au rattachement (`link_claim_evidence`) : une seule échelle d'états pour une même
#: pièce, afin qu'un lien ne puisse pas dire « present » là où la couverture dit « expired ».
OBSERVED_STATE_TO_EVIDENCE_STATUS: dict[str, EvidenceStatus] = {
    "missing": EvidenceStatus.MISSING,
    "expired": EvidenceStatus.EXPIRED,
    "out_of_scope": EvidenceStatus.OUT_OF_SCOPE,
    "not_covering": EvidenceStatus.REJECTED,
    "partial": EvidenceStatus.PARTIAL,
    "covered": EvidenceStatus.PRESENT,
}


@dataclass(frozen=True)
class CoverageFinding:
    """Un fait constaté, avec les valeurs qui l'ont produit."""

    code: str
    severity: str  # blocking | attention | information
    message: str
    evidence_id: UUID | None = None
    facts: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceCheckResult:
    """Ce que l'examen d'une preuve a donné, indépendamment du lien déclaré."""

    evidence_id: UUID
    evidence_type: EvidenceType
    validity_state: str  # valid | expired | validity_unknown
    scope_state: str  # in_scope | out_of_product | out_of_supplier | scope_unknown
    type_state: str  # accepted | not_accepted
    findings: tuple[CoverageFinding, ...]

    @property
    def usable(self) -> bool:
        return (
            self.validity_state == "valid"
            and self.scope_state in {"in_scope", "scope_unknown"}
            and self.type_state == "accepted"
        )


@dataclass(frozen=True)
class EvidenceSuggestion:
    """Un rapprochement proposé : jamais une preuve, toujours motivé."""

    evidence_id: UUID
    evidence_type: EvidenceType
    reference: str | None
    score: int
    reasons: tuple[str, ...]
    caveats: tuple[str, ...]
    usable_as_of: bool


@dataclass(frozen=True)
class ClaimCoverage:
    claim: Claim
    as_of: date
    state: str
    declared_state: str
    observed_state: str
    is_sufficient: bool
    explanation: str
    checks: tuple[EvidenceCheckResult, ...]
    findings: tuple[CoverageFinding, ...]
    suggestions: tuple[EvidenceSuggestion, ...]
    contradicting_links: tuple[UUID, ...] = ()


# ---------------------------------------------------------------------------
# Examen d'une preuve
# ---------------------------------------------------------------------------


def observed_state(check: EvidenceCheckResult) -> str:
    """L'état d'une **unique** pièce, dans le vocabulaire des états de couverture.

    Une pièce utilisable dont le périmètre n'a pas pu être comparé reste « partial » : le
    produit ne prononce pas de couverture sur une pièce dont il ne peut pas établir la portée.
    """

    if check.validity_state == "expired":
        return "expired"
    if check.scope_state in {"out_of_product", "out_of_supplier"}:
        return "out_of_scope"
    if check.type_state == "not_accepted":
        return "not_covering"
    if check.validity_state == "validity_unknown" or check.scope_state == "scope_unknown":
        return "partial"
    return "covered"


def examine_evidence(
    evidence: Evidence,
    *,
    as_of: date,
    claim_type: ClaimType | None,
    analysis_product_id: UUID | None,
    analysis_supplier_id: UUID | None,
) -> EvidenceCheckResult:
    """Examine une preuve contre une date, un périmètre et une famille d'allégation."""

    findings: list[CoverageFinding] = []

    # --- Validité -----------------------------------------------------------
    if evidence.expires_on is not None and evidence.expires_on < as_of:
        validity_state = "expired"
        findings.append(
            CoverageFinding(
                code="certificate_expired",
                severity="blocking",
                evidence_id=evidence.id,
                message=(
                    f"Preuve expirée : la validité déclarée se termine le "
                    f"{evidence.expires_on.isoformat()}, l'audit est daté du {as_of.isoformat()}."
                ),
                facts={
                    "expires_on": evidence.expires_on.isoformat(),
                    "as_of": as_of.isoformat(),
                    "days_since_expiry": (as_of - evidence.expires_on).days,
                    "date_is_declared_not_verified": True,
                },
            )
        )
    elif evidence.expires_on is None and evidence.evidence_type in EXPIRY_BEARING_TYPES:
        validity_state = "validity_unknown"
        findings.append(
            CoverageFinding(
                code="validity_not_stated",
                severity="attention",
                evidence_id=evidence.id,
                message=(
                    f"Aucune date de fin de validité n'est déclarée pour cette preuve de type "
                    f"« {evidence.evidence_type.value} » : le produit ne peut donc pas dire "
                    "qu'elle est valide à la date de l'audit."
                ),
                facts={"evidence_type": evidence.evidence_type.value, "as_of": as_of.isoformat()},
            )
        )
    else:
        validity_state = "valid"
        if evidence.expires_on is not None:
            findings.append(
                CoverageFinding(
                    code="validity_declared_ok",
                    severity="information",
                    evidence_id=evidence.id,
                    message=(
                        f"Valide à la date de l'audit selon la date déclarée "
                        f"({evidence.expires_on.isoformat()})."
                    ),
                    facts={
                        "expires_on": evidence.expires_on.isoformat(),
                        "date_is_declared_not_verified": True,
                    },
                )
            )
        else:
            findings.append(
                CoverageFinding(
                    code="validity_not_applicable",
                    severity="information",
                    evidence_id=evidence.id,
                    message=(
                        "Ce type de preuve ne porte pas de date d'expiration : aucune échéance "
                        "n'est opposée. Cela ne vaut pas validation."
                    ),
                    facts={"evidence_type": evidence.evidence_type.value},
                )
            )

    # --- Périmètre ----------------------------------------------------------
    if analysis_product_id is not None and evidence.product_id is not None:
        if evidence.product_id != analysis_product_id:
            scope_state = "out_of_product"
            findings.append(
                CoverageFinding(
                    code="other_product",
                    severity="blocking",
                    evidence_id=evidence.id,
                    message=(
                        "Cette preuve est rattachée à un autre produit que celui de l'audit : "
                        "elle ne peut pas étayer une allégation portant sur ce produit."
                    ),
                    facts={
                        "evidence_product_id": str(evidence.product_id),
                        "claim_product_id": str(analysis_product_id),
                    },
                )
            )
        else:
            scope_state = "in_scope"
            findings.append(
                CoverageFinding(
                    code="same_product",
                    severity="information",
                    evidence_id=evidence.id,
                    message="La preuve vise le produit de l'audit.",
                    facts={"product_id": str(analysis_product_id)},
                )
            )
    elif analysis_supplier_id is not None and evidence.supplier_id is not None and evidence.supplier_id != analysis_supplier_id:
        scope_state = "out_of_supplier"
        findings.append(
            CoverageFinding(
                code="other_supplier",
                severity="blocking",
                evidence_id=evidence.id,
                message=(
                    "Cette preuve est rattachée à un autre fournisseur que celui de l'audit : "
                    "le produit de l'audit n'est pas couvert."
                ),
                facts={
                    "evidence_supplier_id": str(evidence.supplier_id),
                    "claim_supplier_id": str(analysis_supplier_id),
                },
            )
        )
    else:
        scope_state = "scope_unknown"
        findings.append(
            CoverageFinding(
                code="scope_not_comparable",
                severity="attention",
                evidence_id=evidence.id,
                message=(
                    "Le périmètre de cette preuve n'est pas comparable à celui de l'audit "
                    "(produit ou fournisseur non renseigné). Le produit ne conclut donc pas "
                    "qu'elle couvre l'allégation."
                ),
                facts={
                    "evidence_product_id": str(evidence.product_id) if evidence.product_id else None,
                    "evidence_supplier_id": str(evidence.supplier_id) if evidence.supplier_id else None,
                    "claim_product_id": str(analysis_product_id) if analysis_product_id else None,
                    "claim_supplier_id": str(analysis_supplier_id) if analysis_supplier_id else None,
                    "declared_product_scope": evidence.product_scope,
                },
            )
        )

    # --- Type de preuve -----------------------------------------------------
    accepted: tuple[EvidenceType, ...] | None = None
    if claim_type is not None:
        accepted = ACCEPTED_EVIDENCE_TYPES.get(claim_type)
        if accepted is not None and evidence.evidence_type not in accepted:
            type_state = "not_accepted"
            findings.append(
                CoverageFinding(
                    code="evidence_type_does_not_cover_claim",
                    severity="blocking",
                    evidence_id=evidence.id,
                    message=(
                        f"Une preuve de type « {evidence.evidence_type.value} » ne peut pas étayer "
                        f"une allégation de la famille « {claim_type.value} »."
                    ),
                    facts={
                        "evidence_type": evidence.evidence_type.value,
                        "claim_type": claim_type.value,
                        "accepted_types": [item.value for item in accepted],
                    },
                )
            )
        else:
            type_state = "accepted"
            findings.append(
                CoverageFinding(
                    code="evidence_type_accepted",
                    severity="information",
                    evidence_id=evidence.id,
                    message=(
                        f"Type de preuve « {evidence.evidence_type.value} » pertinent pour la "
                        f"famille d'allégation « {claim_type.value} »."
                    ),
                    facts={"evidence_type": evidence.evidence_type.value, "claim_type": claim_type.value},
                )
            )
    else:
        type_state = "accepted"
        findings.append(
            CoverageFinding(
                code="claim_family_unknown",
                severity="attention",
                evidence_id=evidence.id,
                message=(
                    "La famille de cette allégation n'est pas reconnue par la table du produit : "
                    "aucune conclusion n'est tirée sur la pertinence du type de preuve."
                ),
                facts={"claim_type": None},
            )
        )

    return EvidenceCheckResult(
        evidence_id=evidence.id,
        evidence_type=evidence.evidence_type,
        validity_state=validity_state,
        scope_state=scope_state,
        type_state=type_state,
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# État par allégation
# ---------------------------------------------------------------------------


def _declared_state(links: list[EvidenceLink]) -> str | None:
    """Ce qu'un relecteur a **déclaré**, ramené au vocabulaire des états de couverture.

    Renvoie `None` quand rien n'a été déclaré. C'est une distinction, pas un détail : la
    première version de cette fonction retombait sur « partial » pour tout lien non déclaré,
    si bien qu'un rattachement `PENDING` — c'est-à-dire **sans** décision humaine — était
    relu comme la déclaration d'une couverture seulement partielle, et plafonnait le constat.
    Le produit se contredisait alors lui-même : « covered » sur les faits, « partial » par
    sa propre écriture. Une absence de déclaration ne se traduit pas en état de couverture.
    """

    declared = [link for link in links if link.coverage_status != EvidenceStatus.PENDING]
    if not declared:
        return None

    for link in declared:
        if link.coverage_status == EvidenceStatus.VERIFIED and link.relation == EvidenceRelation.SUPPORTS:
            return "covered"
    if any(link.coverage_status == EvidenceStatus.EXPIRED for link in declared):
        return "expired"
    if any(link.coverage_status == EvidenceStatus.OUT_OF_SCOPE for link in declared):
        return "out_of_scope"
    if any(
        link.coverage_status in {EvidenceStatus.PRESENT, EvidenceStatus.VERIFIED}
        and link.relation in {EvidenceRelation.SUPPORTS, EvidenceRelation.PARTIALLY_SUPPORTS}
        for link in declared
    ):
        return "partial"
    if any(link.coverage_status == EvidenceStatus.MISSING for link in declared):
        return "missing"
    return "partial"


def _most_restrictive(states: list[str | None]) -> str:
    """L'état le plus défavorable, en ignorant les côtés qui ne se prononcent pas (`None`)."""

    states = [state for state in states if state is not None]
    ordered = [state for state in COVERAGE_STATES if state in set(states)]
    if not ordered:
        return "partial"
    # `COVERAGE_STATES` est ordonné du plus grave au plus favorable.
    return ordered[0]


def observe_claim_coverage(
    *,
    claim: Claim,
    analysis: Analysis,
    as_of: date,
    links: list[EvidenceLink],
    evidences: dict[UUID, Evidence],
) -> tuple[str, str, tuple[EvidenceCheckResult, ...], tuple[CoverageFinding, ...], tuple[UUID, ...]]:
    """L'état observé d'une allégation, à partir des seuls faits examinés."""

    if not links:
        return "missing", "missing", (), (), ()

    try:
        claim_type: ClaimType | None = ClaimType(claim.claim_type)
    except ValueError:
        claim_type = None

    checks: list[EvidenceCheckResult] = []
    findings: list[CoverageFinding] = []
    state_candidates: list[str] = []
    contradicting: list[UUID] = []

    for link in links:
        evidence = evidences.get(link.evidence_id)
        if evidence is None:  # pragma: no cover - l'intégrité référentielle l'interdit
            continue
        check = examine_evidence(
            evidence,
            as_of=as_of,
            claim_type=claim_type,
            analysis_product_id=analysis.product_id,
            analysis_supplier_id=analysis.supplier_id,
        )
        checks.append(check)
        findings.extend(check.findings)
        declared_usable = link.coverage_status in {EvidenceStatus.PRESENT, EvidenceStatus.VERIFIED} and (
            link.relation in {EvidenceRelation.SUPPORTS, EvidenceRelation.PARTIALLY_SUPPORTS}
        )
        if declared_usable and not check.usable:
            contradicting.append(link.id)
            findings.append(
                CoverageFinding(
                    code="declared_but_unusable",
                    severity="blocking",
                    evidence_id=evidence.id,
                    message=(
                        "Ce lien a été déclaré utilisable par un relecteur alors que la preuve "
                        "n'est pas utilisable à la date de l'audit. Les deux constats sont "
                        "conservés : le produit ne réécrit pas une décision humaine, il la "
                        "signale comme contredite par les faits."
                    ),
                    facts={
                        "declared_coverage_status": link.coverage_status.value,
                        "declared_relation": link.relation.value,
                        "observed_validity": check.validity_state,
                        "observed_scope": check.scope_state,
                        "observed_type": check.type_state,
                    },
                )
            )

        if not check.usable:
            if check.validity_state == "expired":
                state_candidates.append("expired")
            elif check.scope_state in {"out_of_product", "out_of_supplier"}:
                state_candidates.append("out_of_scope")
            elif check.type_state == "not_accepted":
                state_candidates.append("not_covering")
            else:
                state_candidates.append("partial")
            continue

        if check.validity_state == "validity_unknown" or check.scope_state == "scope_unknown":
            state_candidates.append("partial")
        else:
            state_candidates.append("covered")

    if not state_candidates:  # pragma: no cover - un lien implique une preuve
        return "partial", _declared_state(links), tuple(checks), tuple(findings), tuple(contradicting)

    observed = _most_restrictive(state_candidates)
    declared = _declared_state(links)
    return observed, declared, tuple(checks), tuple(findings), tuple(contradicting)


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def suggest_evidence(
    *,
    claim: Claim,
    analysis: Analysis,
    candidates: list[Evidence],
    linked_ids: set[UUID],
    as_of: date,
) -> tuple[EvidenceSuggestion, ...]:
    """Proposer des rapprochements, en expliquant chaque proposition.

    Une suggestion n'est pas une preuve : le lien reste une décision humaine. Le score
    n'est pas une probabilité, c'est un compteur de motifs (**+3** même produit,
    **+2** même fournisseur, **+2** type pertinent pour la famille, **+1** valide à la
    date, **−1** périmètre non comparable).
    """

    try:
        claim_type: ClaimType | None = ClaimType(claim.claim_type)
    except ValueError:
        claim_type = None
    accepted = ACCEPTED_EVIDENCE_TYPES.get(claim_type) if claim_type is not None else None

    suggestions: list[EvidenceSuggestion] = []
    for evidence in candidates:
        if evidence.id in linked_ids:
            continue
        score = 0
        reasons: list[str] = []
        caveats: list[str] = []
        if analysis.product_id is not None and evidence.product_id == analysis.product_id:
            score += 3
            reasons.append("même produit que l'audit")
        if analysis.supplier_id is not None and evidence.supplier_id == analysis.supplier_id:
            score += 2
            reasons.append("même fournisseur que l'audit")
        if accepted is not None and evidence.evidence_type in accepted:
            score += 2
            reasons.append(f"type « {evidence.evidence_type.value} » pertinent pour cette famille d'allégation")
        elif accepted is not None:
            score -= 2
            caveats.append(
                f"type « {evidence.evidence_type.value} » non retenu pour cette famille d'allégation"
            )
        if evidence.expires_on is not None and evidence.expires_on < as_of:
            score -= 3
            caveats.append(f"expirée le {evidence.expires_on.isoformat()} : inutilisable pour cet audit")
        elif evidence.expires_on is not None:
            score += 1
            reasons.append(f"valide jusqu'au {evidence.expires_on.isoformat()} (date déclarée)")
        elif evidence.evidence_type in EXPIRY_BEARING_TYPES:
            caveats.append("aucune date de validité déclarée")
        if evidence.product_id is None and evidence.supplier_id is None:
            score -= 1
            caveats.append("périmètre non renseigné : le produit ne peut pas confirmer qu'elle couvre l'allégation")
        if score <= 0:
            continue
        suggestions.append(
            EvidenceSuggestion(
                evidence_id=evidence.id,
                evidence_type=evidence.evidence_type,
                reference=evidence.reference,
                score=score,
                reasons=tuple(reasons),
                caveats=tuple(caveats) + ("suggestion à confirmer par un relecteur : ce n'est pas une preuve",),
                usable_as_of=not (evidence.expires_on is not None and evidence.expires_on < as_of),
            )
        )

    suggestions.sort(key=lambda item: (-item.score, item.evidence_type.value, str(item.evidence_id)))
    return tuple(suggestions)


# ---------------------------------------------------------------------------
# Lecture par lot pour une version d'analyse
# ---------------------------------------------------------------------------


def coverage_for_analysis_version(
    db: Session,
    *,
    organization_id: UUID,
    analysis: Analysis,
    version: AnalysisVersion,
    as_of: date,
    with_suggestions: bool = True,
    suggestion_limit: int = 5,
) -> list[ClaimCoverage]:
    """L'état de couverture de chaque allégation d'une version d'analyse."""

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
    if not claims:
        return []

    claim_ids = [claim.id for claim in claims]
    links = list(
        db.scalars(
            select(EvidenceLink)
            .where(
                EvidenceLink.organization_id == organization_id,
                EvidenceLink.claim_id.in_(claim_ids),
            )
            .order_by(EvidenceLink.created_at.asc(), EvidenceLink.id.asc())
        ).all()
    )
    linked_ids = {link.evidence_id for link in links}
    evidence_ids = set(linked_ids)

    evidences: dict[UUID, Evidence] = {}
    if evidence_ids:
        for evidence in db.scalars(
            select(Evidence).where(
                Evidence.organization_id == organization_id,
                Evidence.id.in_(evidence_ids),
                Evidence.deleted_at.is_(None),
            )
        ).all():
            evidences[evidence.id] = evidence

    candidates: list[Evidence] = []
    if with_suggestions:
        conditions = [
            Evidence.organization_id == organization_id,
            Evidence.deleted_at.is_(None),
        ]
        if linked_ids:
            conditions.append(Evidence.id.not_in(linked_ids))
        candidates = list(
            db.scalars(
                select(Evidence)
                .where(*conditions)
                .order_by(Evidence.created_at.desc(), Evidence.id.asc())
                .limit(200)
            ).all()
        )

    by_claim: dict[UUID, list[EvidenceLink]] = {claim_id: [] for claim_id in claim_ids}
    for link in links:
        by_claim.setdefault(link.claim_id, []).append(link)

    coverages: list[ClaimCoverage] = []
    for claim in claims:
        claim_links = by_claim.get(claim.id, [])
        observed, declared, checks, findings, contradicting = observe_claim_coverage(
            claim=claim,
            analysis=analysis,
            as_of=as_of,
            links=claim_links,
            evidences=evidences,
        )
        # L'état retenu est le plus restrictif de ce que les faits montrent et de ce que le
        # relecteur a déclaré : un constat défavorable n'est jamais effacé par une
        # déclaration favorable, et une déclaration défavorable n'est pas réécrite non plus.
        state = _most_restrictive([observed, declared])
        suggestions = (
            suggest_evidence(
                claim=claim,
                analysis=analysis,
                candidates=candidates,
                linked_ids=set(linked_ids),
                as_of=as_of,
            )[:suggestion_limit]
            if with_suggestions
            else ()
        )
        coverages.append(
            ClaimCoverage(
                claim=claim,
                as_of=as_of,
                state=state,
                declared_state=declared,
                observed_state=observed,
                is_sufficient=state == "covered",
                explanation=_explanation(state=state, observed=observed, declared=declared, checks=checks),
                checks=checks,
                findings=tuple(findings),
                suggestions=tuple(suggestions),
                contradicting_links=contradicting,
            )
        )
    return coverages


def _explanation(
    *, state: str, observed: str, declared: str, checks: tuple[EvidenceCheckResult, ...]
) -> str:
    parts = [f"État de couverture : {state} — {STATE_MEANINGS[state]}."]
    if state == "missing":
        parts.append("Aucun justificatif n'est rattaché à cette allégation.")
    if declared != observed:
        parts.append(
            f"Le relecteur a déclaré « {declared} », les faits examinés donnent « {observed} » : "
            "l'état retenu est le plus restrictif des deux."
        )
    if checks:
        validity = {check.validity_state for check in checks}
        scope = {check.scope_state for check in checks}
        if "validity_unknown" in validity:
            parts.append("Au moins une preuve n'a pas de date de validité déclarée.")
        if "scope_unknown" in scope:
            parts.append("Au moins une preuve n'a pas de périmètre comparable à celui de l'audit.")
    return " ".join(parts)


__all__ = [
    "ACCEPTED_EVIDENCE_TYPES",
    "OBSERVED_STATE_TO_EVIDENCE_STATUS",
    "COVERAGE_STATES",
    "EXPIRY_BEARING_TYPES",
    "STATE_MEANINGS",
    "ClaimCoverage",
    "CoverageFinding",
    "EvidenceCheckResult",
    "EvidenceSuggestion",
    "coverage_for_analysis_version",
    "examine_evidence",
    "observe_claim_coverage",
    "observed_state",
    "suggest_evidence",
]
