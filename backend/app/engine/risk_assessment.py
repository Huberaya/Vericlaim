"""Explainable risk score and conservative financial exposure matrix."""

from __future__ import annotations

from decimal import Decimal

from app.models.legal_types import (
    EvidenceDossier,
    ExposureCategory,
    ExposureItem,
    ExposureMatrix,
    LegalAssessment,
    LegalForce,
    OverallCompliance,
    Severity,
    Verdict,
)
from app.models.schemas import AuditContext


SEVERITY_WEIGHTS = {
    Severity.LOW: 30,
    Severity.MEDIUM: 55,
    Severity.HIGH: 78,
    Severity.CRITICAL: 95,
}


CIVIL_RISK_TEXT = [
    "Risque d'action en cessation ou d'injonction par une association/autorité habilitée, sous réserve des conditions de recevabilité et de preuve.",
    "Risque d'action d'un concurrent pour concurrence déloyale (notamment C. civ., art. 1240), si faute, préjudice et lien de causalité sont établis; montant non estimable par le moteur.",
]


def _eur(value: Decimal | int | float | None) -> str:
    if value is None:
        return "non chiffré"
    amount = int(value) if Decimal(str(value)) == Decimal(str(value)).to_integral_value() else float(value)
    formatted = f"{amount:,.0f}" if isinstance(amount, int) else f"{amount:,.2f}"
    return formatted.replace(",", " ").replace(".", ",")


def derive_overall_status(
    evaluations: list[LegalAssessment],
    detected_claims_count: int,
) -> OverallCompliance:
    if detected_claims_count == 0:
        return OverallCompliance.NO_CLAIMS_DETECTED
    if any(item.is_legal_violation for item in evaluations):
        return OverallCompliance.NON_COMPLIANT
    if any(item.verdict == Verdict.CONDITIONAL_REJECT for item in evaluations):
        return OverallCompliance.CONDITIONAL_REJECT
    if any(item.verdict == Verdict.REVIEW_REQUIRED for item in evaluations):
        return OverallCompliance.REVIEW_REQUIRED
    if any(item.verdict == Verdict.UPCOMING for item in evaluations):
        return OverallCompliance.UPCOMING_REQUIREMENTS
    return OverallCompliance.COMPLIANT


def derive_risk_score(evaluations: list[LegalAssessment]) -> int:
    scores: list[int] = []
    for item in evaluations:
        if item.verdict in {Verdict.NOT_APPLICABLE, Verdict.COMPLIANT, Verdict.ADVISORY_ONLY}:
            continue
        if item.verdict == Verdict.UPCOMING:
            scores.append(20 if item.legal_force == LegalForce.EU_DIRECTIVE_DATE_GATED else 10)
        elif item.is_legal_violation:
            scores.append(SEVERITY_WEIGHTS[item.severity])
        elif item.verdict == Verdict.CONDITIONAL_REJECT:
            # Publication hold for a missing proof is material, but it is not
            # represented as a legal violation unless a binding rule says so.
            scores.append(max(25, SEVERITY_WEIGHTS[item.severity] - 18))
        elif item.verdict == Verdict.REVIEW_REQUIRED:
            scores.append(max(20, SEVERITY_WEIGHTS[item.severity] - 10))
    if not scores:
        return 0
    distinct_actionable_rules = len(
        {
            item.rule_id
            for item in evaluations
            if item.verdict not in {Verdict.NOT_APPLICABLE, Verdict.COMPLIANT, Verdict.ADVISORY_ONLY}
        }
    )
    return min(100, max(scores) + min(15, max(0, distinct_actionable_rules - 1) * 4))


def build_exposure_matrix(
    evaluations: list[LegalAssessment],
    dossier: EvidenceDossier,
    context: AuditContext,
) -> tuple[ExposureMatrix, str]:
    items: list[ExposureItem] = []
    seen_bases: set[str] = set()
    candidate_fixed: list[Decimal] = []
    subject_is_legal_person = dossier.legal_person

    for finding in evaluations:
        sanction = finding.sanction
        actionable = finding.is_legal_violation or finding.verdict in {
            Verdict.CONDITIONAL_REJECT,
            Verdict.REVIEW_REQUIRED,
        }
        if not sanction or not actionable or sanction.legal_basis in seen_bases:
            continue
        seen_bases.add(sanction.legal_basis)
        fixed = sanction.max_legal_person_eur if subject_is_legal_person else sanction.max_natural_person_eur
        calculated = Decimal(str(fixed)) if fixed is not None else None
        may_scale = sanction.may_scale_to_advertising_spend
        if may_scale:
            operation_spend = context.operation_spend_eur or dossier.advertising_spend_eur
            if operation_spend is not None:
                calculated = max(calculated or Decimal(0), operation_spend)
        if fixed is not None:
            candidate_fixed.append(Decimal(str(fixed)))
        items.append(
            ExposureItem(
                category=ExposureCategory.ADMINISTRATIVE,
                title=sanction.mechanism,
                legal_basis=sanction.legal_basis,
                max_natural_person_eur=sanction.max_natural_person_eur,
                max_legal_person_eur=sanction.max_legal_person_eur,
                calculated_amount_eur=calculated,
                may_scale_to_advertising_spend=may_scale,
                conditional=True,
                note=sanction.notes,
            )
        )

    # General consumer-law claims are deliberately shown as an unquantified
    # parallel risk unless a separate, fact-specific sanctions module is added.
    if any(
        finding.is_legal_violation
        and finding.legal_force in {LegalForce.EU_DIRECTIVE_DATE_GATED, LegalForce.BINDING_FR}
        and finding.rule_id in {"RULE_EU_GENERIC_CLAIM", "RULE_EU_CARBON_NEUTRAL_COMPENSATION"}
        for finding in evaluations
    ):
        items.append(
            ExposureItem(
                category=ExposureCategory.PENAL,
                title="Pratiques commerciales trompeuses — exposition générale à qualifier",
                legal_basis="Code de la consommation, notamment articles L. 121-2 à L. 121-4 et L. 132-2 et suivants",
                conditional=True,
                note=(
                    "L'existence, l'autorité compétente, le quantum (dont éventuels critères de chiffre d'affaires ou de dépenses publicitaires), "
                    "les multiplicateurs applicables à une personne morale et les conséquences d'un support numérique dépendent de la qualification, "
                    "du texte national de transposition et des faits. Aucun montant n'est additionné ni chiffré ici."
                ),
            )
        )

    has_actionable_finding = any(
        item.is_legal_violation
        or item.verdict in {Verdict.CONDITIONAL_REJECT, Verdict.REVIEW_REQUIRED}
        for item in evaluations
    )
    civil_risk = CIVIL_RISK_TEXT.copy() if has_actionable_finding else []
    if has_actionable_finding:
        items.append(
            ExposureItem(
                category=ExposureCategory.CIVIL,
                title="Cessation, injonction ou concurrence déloyale",
                legal_basis="À déterminer selon le demandeur, le fondement et les faits; C. civ., art. 1240 le cas échéant",
                conditional=True,
                note="Aucun montant n'est calculé: dommage, recevabilité, preuve et mesure judiciaire relèvent d'une analyse distincte.",
            )
        )

    maximum_fixed = max(candidate_fixed) if candidate_fixed else None
    calculation_notes = [
        "Le score de risque est une priorité de revue, pas une probabilité d'infraction ni un montant d'amende.",
        "Les plafonds affichés ne sont ni automatiques ni nécessairement cumulables; l'outil retient le plus grand plafond fixe identifié, sans additionner les règles.",
        "Les risques civils et les sanctions générales du droit de la consommation ne sont pas chiffrés sans qualification factuelle et juridictionnelle.",
    ]
    if subject_is_legal_person:
        selected_fixed = [item.max_legal_person_eur for item in items if item.max_legal_person_eur is not None]
        max_selected = max((Decimal(str(x)) for x in selected_fixed), default=None)
        subject_label = "personne morale"
    else:
        selected_fixed = [item.max_natural_person_eur for item in items if item.max_natural_person_eur is not None]
        max_selected = max((Decimal(str(x)) for x in selected_fixed), default=None)
        subject_label = "personne physique"

    matrix = ExposureMatrix(
        items=items,
        max_known_fixed_fine_eur=max_selected,
        max_known_fixed_fine_for=subject_label,
        amount_is_cumulative=False,
        civil_risk=civil_risk,
        calculation_notes=calculation_notes,
    )

    if not items:
        summary = "Aucun plafond de sanction pécuniaire chiffré n'est rattaché aux règles évaluées; les contrôles non contraignants ne créent pas d'amende."
    elif max_selected is not None:
        bases = "; ".join(
            sorted(
                {
                    item.legal_basis or item.title
                    for item in items
                    if (item.max_legal_person_eur if subject_is_legal_person else item.max_natural_person_eur) is not None
                }
            )
        )
        summary = (
            f"Plafond fixe connu le plus élevé: jusqu'à {_eur(max_selected)} € pour une {subject_label} "
            f"({bases}). Ce n'est ni une sanction certaine ni un montant cumulatif; des risques généraux ou civils "
            "peuvent exister et nécessitent une analyse distincte."
        )
        if any(item.may_scale_to_advertising_spend for item in items):
            spend = context.operation_spend_eur or dossier.advertising_spend_eur
            if spend is not None:
                summary += f" Pour le régime concerné, l'exposition peut dépendre des dépenses de l'opération (déclarées: {_eur(spend)} €)."
            else:
                summary += " Un régime identifié peut permettre une hausse jusqu'aux dépenses de l'opération; ce montant n'a pas été fourni."
    else:
        summary = (
            "Un risque pécuniaire général est à examiner au titre des pratiques commerciales trompeuses, "
            "mais aucun montant n'est calculé sans vérifier le texte national, la qualification et les circonstances."
        )

    return matrix, summary
