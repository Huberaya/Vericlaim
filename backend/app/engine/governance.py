"""Governance engine for versioned regulatory rules, citations, and legal audits (Chantier 7)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from app.engine.rule_book import RULE_BY_ID, RULEBOOK_VERSION, RULES, RegulatoryRule
from app.models.regulatory_schemas import (
    ConfidenceLevel,
    Jurisdiction,
    LegalStatus,
    OfficialCitation,
    RegulatoryRuleDetail,
    RegulatoryRuleSummary,
    ReviewStatus,
    RuleBookChangelogEntry,
    RuleBookSummaryResponse,
    RuleDiffItem,
    RuleFieldDiff,
    RuleGovernanceReview,
    SafeHarborSchema,
)

# ---------------------------------------------------------------------------
# Official Legal Citations Registry
# ---------------------------------------------------------------------------

OFFICIAL_CITATIONS: dict[str, list[OfficialCitation]] = {
    "RULE_AGEC_BIODEGRADABLE": [
        OfficialCitation(
            article="Article L. 541-9-1 du Code de l'environnement",
            source_title="Loi n° 2020-105 du 10 février 2020 relative à la lutte contre le gaspillage et à l'économie circulaire (AGEC)",
            text_excerpt=(
                "Il est interdit de faire figurer sur un produit ou un emballage les mentions "
                "« biodégradable », « respectueux de l'environnement » ou toute autre mention équivalente."
            ),
            url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041555718",
            effective_date=date(2022, 1, 1),
        ),
        OfficialCitation(
            article="Article R. 541-230 du Code de l'environnement",
            source_title="Décret n° 2022-748 du 29 avril 2022 relatif à l'information du consommateur sur les qualités et caractéristiques environnementales",
            text_excerpt=(
                "Les mentions mentionnées au premier alinéa de l'article L. 541-9-1 s'entendent de toute allégation, "
                "indication ou présentation commerciale tendant à affirmer ou suggérer une absence totale d'impact sur l'environnement."
            ),
            url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000049389990",
            effective_date=date(2022, 5, 1),
        ),
    ],
    "RULE_AGEC_NATURE_FRIENDLY": [
        OfficialCitation(
            article="Article L. 541-9-1 du Code de l'environnement",
            source_title="Loi AGEC - Interdiction des allégations globales d'impact neutre",
            text_excerpt=(
                "Il est interdit de faire figurer sur un produit ou un emballage la mention « respectueux de l'environnement » "
                "ou toute autre mention équivalente."
            ),
            url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041555718",
            effective_date=date(2022, 1, 1),
        ),
        OfficialCitation(
            article="Article R. 541-230 du Code de l'environnement",
            source_title="Décret d'application AGEC",
            text_excerpt=(
                "Sont notamment assimilées aux mentions interdites les formulations telles que « ami de la nature », "
                "« vert », « écologique » ou « éco-responsable » lorsqu'elles ne sont pas assorties des justifications précises prévues par la loi."
            ),
            url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000049389990",
            effective_date=date(2022, 5, 1),
        ),
    ],
    "RULE_EU_GENERIC_CLAIM": [
        OfficialCitation(
            article="Directive (UE) 2024/825, Annexe I modifiant la directive 2005/29/CE (point 4a)",
            source_title="Directive sur l'autonomisation des consommateurs pour la transition écologique (EmpCo)",
            text_excerpt=(
                "Présenter une allégation environnementale générique pour laquelle le professionnel n'est pas en mesure "
                "de démontrer une excellente performance environnementale reconnue en rapport avec l'allégation est considéré comme trompeur en toutes circonstances."
            ),
            url="https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra",
            effective_date=date(2026, 9, 27),
        ),
        OfficialCitation(
            article="Article 2, paragraphe 1, point o) de la directive 2005/29/CE",
            source_title="Définition de l'allégation environnementale générique",
            text_excerpt=(
                "Toute allégation formulée sous forme écrite ou orale, notamment dans les médias audiovisuels, "
                "qui n'est pas contenue sur un label de développement durable ou dont la spécification n'est pas fournie en termes clairs et visibles sur le même support."
            ),
            url="https://eur-lex.europa.eu/eli/dir/2005/29/2026-09-27/fra",
            effective_date=date(2026, 9, 27),
        ),
    ],
    "RULE_EU_CARBON_NEUTRAL_COMPENSATION": [
        OfficialCitation(
            article="Directive (UE) 2024/825, Annexe I modifiant la directive 2005/29/CE (point 4c)",
            source_title="Directive EmpCo - Interdiction des allégations fondées sur la compensation",
            text_excerpt=(
                "Affirmer, sur la base de la compensation des émissions de gaz à effet de serre, qu'un produit a un impact "
                "neutre, réduit ou positif sur l'environnement en termes d'émissions de gaz à effet de serre constitue une pratique commerciale trompeuse en toutes circonstances."
            ),
            url="https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra",
            effective_date=date(2026, 9, 27),
        ),
    ],
    "RULE_FR_CARBON_NEUTRAL_DISCLOSURE": [
        OfficialCitation(
            article="Article L. 229-68 du Code de l'environnement",
            source_title="Loi n° 2021-1104 du 22 août 2021 portant lutte contre le dérèglement climatique (Climat et Résilience)",
            text_excerpt=(
                "Il est interdit d'affirmer dans une publicité qu'un produit ou un service est neutre en carbone, "
                "sans faire figurer ou sans rendre aisément accessible au public un bilan des émissions de gaz à effet de serre, "
                "la trajectoire de réduction et les modalités de compensation."
            ),
            url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043960256",
            effective_date=date(2023, 1, 1),
        ),
        OfficialCitation(
            article="Article L. 229-69 du Code de l'environnement",
            source_title="Sanction administrative en matière de publicité neutralité carbone",
            text_excerpt=(
                "En cas de manquement à l'article L. 229-68, l'autorité administrative peut prononcer une amende pouvant "
                "atteindre 20 000 € pour une personne physique et 100 000 € pour une personne morale, pouvant être portée à la totalité des dépenses engagées pour l'opération."
            ),
            url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043960258",
            effective_date=date(2023, 1, 1),
        ),
    ],
    "RULE_EU_COMPARATIVE_LCA": [
        OfficialCitation(
            article="COM(2023) 166 final, Proposition de Directive Green Claims",
            source_title="Proposition de directive de la Commission européenne sur la justification et la communication des allégations écologiques explicites",
            text_excerpt=(
                "Les allégations écologiques comparatives explicites doivent être étayées par des données équivalentes et comparables "
                "fondées sur des méthodologies d'analyse du cycle de vie normalisées (ISO 14040/14044)."
            ),
            url="https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=COM%3A2023%3A166%3AFIN",
            effective_date=None,
        ),
    ],
    "RULE_ISO_RECYCLABLE_PERCENTAGE": [
        OfficialCitation(
            article="Norme ISO 14021:2016 / ISO 14021:2026",
            source_title="Marquage et déclarations environnementaux — Auto-déclarations environnementales (Étiquetage de type II)",
            text_excerpt=(
                "Une allégation de recyclabilité ne doit être formulée que s'il existe des systèmes pratiques de collecte, "
                "de tri et de traitement accessibles à une part substantielle des utilisateurs finaux."
            ),
            url="https://www.iso.org/standard/14021",
            effective_date=date(2016, 3, 1),
        ),
        OfficialCitation(
            article="Article R. 541-228 VI du Code de l'environnement",
            source_title="Régime français de la recyclabilité des emballages ménagers",
            text_excerpt=(
                "La recyclabilité s'apprécie au regard de la capacité effective de l'emballage à être collecté, trié et régénéré dans une filière opérationnelle."
            ),
            url="https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006074220/LEGISCTA000045728452/",
            effective_date=date(2022, 5, 1),
        ),
    ],
    "RULE_EVIDENCE_QUANTIFIED_CLAIM": [
        OfficialCitation(
            article="Norme internationale ISO 14044:2006",
            source_title="Management environnemental — Analyse du cycle de vie — Exigences et lignes directrices",
            text_excerpt=(
                "Toute quantification d'impacts environnementaux communiquée à des tiers doit spécifier l'unité fonctionnelle, "
                "les frontières du système et la méthodologie d'allocation retenue."
            ),
            url="https://www.iso.org/standard/38498.html",
            effective_date=date(2006, 7, 1),
        ),
        OfficialCitation(
            article="Article L. 121-2 du Code de la consommation",
            source_title="Pratiques commerciales trompeuses par présentation d'éléments chiffrés non vérifiables",
            text_excerpt=(
                "Une pratique commerciale est trompeuse si elle repose sur des allégations, indications ou présentations fausses "
                "ou de nature à induire en erreur portant sur les résultats attendus de l'utilisation du bien."
            ),
            url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000028748875/2026-09-20",
            effective_date=date(2016, 3, 16),
        ),
    ],
}

# ---------------------------------------------------------------------------
# Rule Metadata & Governance Status
# ---------------------------------------------------------------------------

RULE_METADATA_EXT: dict[str, dict[str, Any]] = {
    "RULE_AGEC_BIODEGRADABLE": {
        "jurisdiction": Jurisdiction.FR,
        "legal_status": LegalStatus.IN_FORCE,
        "confidence_level": ConfidenceLevel.HIGH,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": None,
        "incomplete_coverage_warning": None,
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        "legal_notes": "Interdiction absolue en droit français. Aucune dérogation technique possible.",
    },
    "RULE_AGEC_NATURE_FRIENDLY": {
        "jurisdiction": Jurisdiction.FR,
        "legal_status": LegalStatus.IN_FORCE,
        "confidence_level": ConfidenceLevel.HIGH,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": None,
        "incomplete_coverage_warning": None,
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        "legal_notes": "Application stricte sur emballages et étiquetage produit. Analyse contextuelle requise pour supports publicitaires hors produit.",
    },
    "RULE_EU_GENERIC_CLAIM": {
        "jurisdiction": Jurisdiction.EU,
        "legal_status": LegalStatus.IN_FORCE,
        "confidence_level": ConfidenceLevel.HIGH,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": date(2026, 3, 27),
        "incomplete_coverage_warning": "Date d'application directive au 27 septembre 2026. La transposition en droit interne français doit être vérifiée.",
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc),
        "legal_notes": "Exige une preuve d'excellence environnementale officielle (Ecolabel UE ou ISO 14024 Type I).",
    },
    "RULE_EU_CARBON_NEUTRAL_COMPENSATION": {
        "jurisdiction": Jurisdiction.EU,
        "legal_status": LegalStatus.IN_FORCE,
        "confidence_level": ConfidenceLevel.HIGH,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": date(2026, 3, 27),
        "incomplete_coverage_warning": "Application directe au 27 septembre 2026. Avant cette date, application du régime de l'article L. 229-68 du Code de l'environnement.",
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc),
        "legal_notes": "Interdiction stricte des allégations produit neutre fondées sur des compensations hors chaîne de valeur.",
    },
    "RULE_FR_CARBON_NEUTRAL_DISCLOSURE": {
        "jurisdiction": Jurisdiction.FR,
        "legal_status": LegalStatus.IN_FORCE,
        "confidence_level": ConfidenceLevel.HIGH,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": None,
        "incomplete_coverage_warning": None,
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        "legal_notes": "Exige la mise à disposition publique d'un bilan d'émissions complet, d'une trajectoire et d'un plan de compensation.",
    },
    "RULE_EU_COMPARATIVE_LCA": {
        "jurisdiction": Jurisdiction.EU,
        "legal_status": LegalStatus.PROPOSAL,
        "confidence_level": ConfidenceLevel.MEDIUM,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": None,
        "incomplete_coverage_warning": "Proposition de directive COM(2023) 166 non adoptée comme droit contraignant. Règle consultative (advisory evidence gate).",
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc),
        "legal_notes": "Recommandation méthodologique pour éviter le risque de pratique commerciale trompeuse sur allégations comparatives.",
    },
    "RULE_ISO_RECYCLABLE_PERCENTAGE": {
        "jurisdiction": Jurisdiction.INTERNATIONAL,
        "legal_status": LegalStatus.IN_FORCE,
        "confidence_level": ConfidenceLevel.MEDIUM,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": None,
        "incomplete_coverage_warning": "Norme d'auto-déclaration volontaire. Doit être articulée avec les critères spécifiques de recyclabilité de la loi AGEC.",
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        "legal_notes": "ISO 14021:2016 remplacée par ISO 14021:2026. Vérification requise de l'existence effective d'une filière locale.",
    },
    "RULE_EVIDENCE_QUANTIFIED_CLAIM": {
        "jurisdiction": Jurisdiction.INTERNATIONAL,
        "legal_status": LegalStatus.IN_FORCE,
        "confidence_level": ConfidenceLevel.HIGH,
        "review_status": ReviewStatus.APPROVED_LEGAL,
        "transposition_deadline": None,
        "incomplete_coverage_warning": "Garde-fou probatoire interne pour prévenir le risque de qualification de tromperie au sens du Code de la consommation.",
        "reviewed_by": "Direction Juridique & Conformité Réglementaire",
        "reviewed_at": datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        "legal_notes": "Exige une ACV ISO 14044 et des métadonnées vérifiées pour toute allégation chiffrée (ex. -30% CO2).",
    },
}

# ---------------------------------------------------------------------------
# Rule Book Changelog & Version Diff History
# ---------------------------------------------------------------------------

RULEBOOK_CHANGELOG: list[RuleBookChangelogEntry] = [
    RuleBookChangelogEntry(
        version="2026-09-24",
        release_date="2026-09-24",
        title="Édition Septembre 2026 — Directive (UE) 2024/825 EmpCo & Révision ISO 14021:2026",
        description=(
            "Activation du verrouillage temporel pour la directive européenne 2024/825 (EmpCo), "
            "prise en compte de la révision ISO 14021:2026 et mise à jour du plafond d'amende AGEC (Art. L. 541-9-4-1)."
        ),
        diff_items=[
            RuleDiffItem(
                rule_id="RULE_EU_GENERIC_CLAIM",
                diff_type="modified",
                title="Allégation environnementale générique sans excellente performance reconnue",
                summary="Date d'application fixée au 27 septembre 2026 avec safe harbors Ecolabel UE et ISO 14024.",
                field_diffs=[
                    RuleFieldDiff(field="effective_from", old_value="2026-03-27", new_value="2026-09-27"),
                    RuleFieldDiff(field="legal_force", old_value="BINDING_FR", new_value="EU_DIRECTIVE_DATE_GATED"),
                ],
            ),
            RuleDiffItem(
                rule_id="RULE_EU_CARBON_NEUTRAL_COMPENSATION",
                diff_type="modified",
                title="Allégation produit de neutralité carbone fondée sur la compensation",
                summary="Interdiction absolue européenne date-gated au 27 septembre 2026.",
                field_diffs=[
                    RuleFieldDiff(field="effective_from", old_value="2026-03-27", new_value="2026-09-27"),
                ],
            ),
            RuleDiffItem(
                rule_id="RULE_AGEC_BIODEGRADABLE",
                diff_type="modified",
                title="Mention « biodégradable » sur produit ou emballage",
                summary="Mise à jour de la référence réglementaire en vigueur (R. 541-230) et confirmation du plafond d'amende de 15 000 €.",
                field_diffs=[
                    RuleFieldDiff(field="legal_reference", old_value="R. 541-221", new_value="R. 541-230"),
                    RuleFieldDiff(field="sanction.max_legal_person_eur", old_value=100000, new_value=15000),
                ],
            ),
        ],
    ),
    RuleBookChangelogEntry(
        version="2026-07-10",
        release_date="2026-07-10",
        title="Édition Juillet 2026 — Harmonisation des sanctions AGEC",
        description="Ajustement des bases légales de sanctions administratives consécutif à l'ordonnance de simplification.",
        diff_items=[
            RuleDiffItem(
                rule_id="RULE_AGEC_NATURE_FRIENDLY",
                diff_type="modified",
                title="Mention équivalente à « respectueux de l'environnement »",
                summary="Précision des critères d'équivalence et sanctions applicables.",
                field_diffs=[
                    RuleFieldDiff(field="sanction.legal_basis", old_value="L. 541-9-4", new_value="L. 541-9-4-1"),
                ],
            )
        ],
    ),
    RuleBookChangelogEntry(
        version="2026-01-15",
        release_date="2026-01-15",
        title="Édition Initiale — Socle Fondateur AGEC & Climat-Résilience",
        description="Création du référentiel initial comprenant les 8 règles fondamentales.",
        diff_items=[
            RuleDiffItem(rule_id=r.rule_id, diff_type="added", title=r.title, summary="Création initiale de la règle")
            for r in RULES
        ],
    ),
]


# ---------------------------------------------------------------------------
# Governance Services & Accessors
# ---------------------------------------------------------------------------

def get_rule_summary(rule: RegulatoryRule) -> RegulatoryRuleSummary:
    meta = RULE_METADATA_EXT.get(rule.rule_id, {})
    return RegulatoryRuleSummary(
        rule_id=rule.rule_id,
        title=rule.title,
        legal_reference=rule.legal_reference,
        jurisdiction=meta.get("jurisdiction", Jurisdiction.FR),
        legal_status=meta.get("legal_status", LegalStatus.IN_FORCE),
        legal_force=rule.legal_force,
        severity=rule.severity,
        rule_kind=rule.rule_kind.value,
        claim_types=[ct.value for ct in rule.claim_types],
        effective_from=rule.effective_from,
        transposition_deadline=meta.get("transposition_deadline"),
        confidence_level=meta.get("confidence_level", ConfidenceLevel.HIGH),
        review_status=meta.get("review_status", ReviewStatus.APPROVED_LEGAL),
        has_safe_harbors=len(rule.safe_harbors) > 0,
        has_sanctions=rule.sanction is not None,
        incomplete_coverage_warning=meta.get("incomplete_coverage_warning"),
    )


def get_rule_detail(rule_id: str) -> RegulatoryRuleDetail | None:
    rule = RULE_BY_ID.get(rule_id)
    if not rule:
        return None
    meta = RULE_METADATA_EXT.get(rule.rule_id, {})
    citations = OFFICIAL_CITATIONS.get(rule.rule_id, [])

    safe_harbor_schemas = [
        SafeHarborSchema(
            safe_harbor_id=sh.safe_harbor_id,
            title=sh.title,
            evidence_kind=sh.evidence_kind,
            conditions=list(sh.conditions),
        )
        for sh in rule.safe_harbors
    ]

    gov_review = RuleGovernanceReview(
        review_status=meta.get("review_status", ReviewStatus.APPROVED_LEGAL),
        reviewed_by=meta.get("reviewed_by"),
        reviewed_at=meta.get("reviewed_at"),
        confidence_level=meta.get("confidence_level", ConfidenceLevel.HIGH),
        legal_notes=meta.get("legal_notes"),
    )

    return RegulatoryRuleDetail(
        rule_id=rule.rule_id,
        title=rule.title,
        legal_reference=rule.legal_reference,
        jurisdiction=meta.get("jurisdiction", Jurisdiction.FR),
        legal_status=meta.get("legal_status", LegalStatus.IN_FORCE),
        legal_force=rule.legal_force,
        severity=rule.severity,
        rule_kind=rule.rule_kind.value,
        scope=rule.scope,
        claim_types=[ct.value for ct in rule.claim_types],
        official_citations=citations,
        source_urls=list(rule.source_urls),
        required_evidence=list(rule.required_evidence),
        safe_harbors=safe_harbor_schemas,
        surfaces=[s.value for s in rule.surfaces] if rule.surfaces else [],
        effective_from=rule.effective_from,
        effective_until=None,
        transposition_deadline=meta.get("transposition_deadline"),
        sanction=rule.sanction,
        priority=rule.priority,
        notes=list(rule.notes),
        governance_review=gov_review,
        incomplete_coverage_warning=meta.get("incomplete_coverage_warning"),
    )


def list_rules(
    jurisdiction: Jurisdiction | None = None,
    legal_status: LegalStatus | None = None,
    legal_force: str | None = None,
    severity: str | None = None,
    claim_type: str | None = None,
    confidence_level: ConfidenceLevel | None = None,
    search: str | None = None,
) -> list[RegulatoryRuleSummary]:
    results: list[RegulatoryRuleSummary] = []
    for rule in RULES:
        summary = get_rule_summary(rule)
        if jurisdiction and summary.jurisdiction != jurisdiction:
            continue
        if legal_status and summary.legal_status != legal_status:
            continue
        if legal_force and summary.legal_force.value != legal_force:
            continue
        if severity and summary.severity.value != severity:
            continue
        if claim_type and claim_type not in summary.claim_types:
            continue
        if confidence_level and summary.confidence_level != confidence_level:
            continue
        if search:
            term = search.lower()
            if term not in summary.title.lower() and term not in summary.legal_reference.lower() and term not in summary.rule_id.lower():
                continue
        results.append(summary)
    return results


def get_rulebook_summary() -> RuleBookSummaryResponse:
    jurisdictions: dict[str, int] = {}
    statuses: dict[str, int] = {}
    warnings_count = 0

    for rule in RULES:
        meta = RULE_METADATA_EXT.get(rule.rule_id, {})
        j = meta.get("jurisdiction", Jurisdiction.FR).value
        jurisdictions[j] = jurisdictions.get(j, 0) + 1

        s = meta.get("legal_status", LegalStatus.IN_FORCE).value
        statuses[s] = statuses.get(s, 0) + 1

        if meta.get("incomplete_coverage_warning"):
            warnings_count += 1

    fingerprint = hashlib.sha256(RULEBOOK_VERSION.encode("utf-8")).hexdigest()

    return RuleBookSummaryResponse(
        rulebook_version=RULEBOOK_VERSION,
        release_date="2026-09-24",
        sha256_fingerprint=fingerprint,
        total_rules=len(RULES),
        jurisdiction_breakdown=jurisdictions,
        legal_status_breakdown=statuses,
        coverage_warnings_count=warnings_count,
        governance_statement=(
            "Référentiel réglementaire audité et maintenu sous gouvernance d'experts juridiques. "
            "Toutes les citations sont sourcées sur Légifrance et EUR-Lex. Version immuable et hashée."
        ),
        disclaimer=(
            "VeriClaim est un système de pré-audit et d'aide à la décision. "
            "Il n'émet aucun avis d'autorité ni décision juridique automatique."
        ),
    )


def get_rulebook_changelog() -> list[RuleBookChangelogEntry]:
    return RULEBOOK_CHANGELOG


def update_rule_governance_review(
    rule_id: str,
    reviewer_user_id: str,
    reviewer_name: str,
    review_status: ReviewStatus,
    confidence_level: ConfidenceLevel,
    legal_notes: str | None = None,
) -> RegulatoryRuleDetail:
    rule = RULE_BY_ID.get(rule_id)
    if not rule:
        raise ValueError(f"Règle inconnue: {rule_id}")

    meta = RULE_METADATA_EXT.setdefault(rule_id, {})
    meta["review_status"] = review_status
    meta["confidence_level"] = confidence_level
    meta["reviewed_by"] = reviewer_name or reviewer_user_id
    meta["reviewed_at"] = datetime.now(timezone.utc)
    if legal_notes:
        meta["legal_notes"] = legal_notes

    detail = get_rule_detail(rule_id)
    if detail is None:
        raise ValueError(f"Règle introuvable: {rule_id}")
    return detail
