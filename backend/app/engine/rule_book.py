"""Versioned, typed rule book for VeriClaim's deterministic audit engine.

A rule's `legal_force` is deliberately separate from its `severity`: an ISO
control or a legislative proposal can be high-risk operationally without being
misreported as enacted legislation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import FrozenSet

from app.models.legal_types import (
    ClaimType,
    LegalForce,
    SanctionProfile,
    Severity,
    Surface,
)


class RuleKind(str, Enum):
    ABSOLUTE_PROHIBITION = "ABSOLUTE_PROHIBITION"
    SAFE_HARBOR = "SAFE_HARBOR"
    DISCLOSURE_GATE = "DISCLOSURE_GATE"
    EVIDENCE_GATE = "EVIDENCE_GATE"
    ADVISORY_EVIDENCE_GATE = "ADVISORY_EVIDENCE_GATE"


@dataclass(frozen=True)
class SafeHarbor:
    safe_harbor_id: str
    title: str
    evidence_kind: str
    conditions: tuple[str, ...]


@dataclass(frozen=True)
class RegulatoryRule:
    rule_id: str
    title: str
    legal_reference: str
    source_urls: tuple[str, ...]
    claim_types: FrozenSet[ClaimType]
    legal_force: LegalForce
    severity: Severity
    rule_kind: RuleKind
    scope: str
    required_evidence: tuple[str, ...] = ()
    safe_harbors: tuple[SafeHarbor, ...] = ()
    surfaces: FrozenSet[Surface] | None = None
    effective_from: date | None = None
    sanction: SanctionProfile | None = None
    priority: int = 100
    notes: tuple[str, ...] = ()


AGEC_FINE = SanctionProfile(
    mechanism="Amende administrative",
    authority="Autorité administrative compétente (procédure du Code de la consommation)",
    legal_basis="Article L. 541-9-4-1 du Code de l'environnement (version en vigueur depuis le 10 juillet 2026)",
    max_natural_person_eur=3000,
    max_legal_person_eur=15000,
    amount_is_automatic=False,
    notes=(
        "Plafond légal, pas une amende automatique. L'agent et l'autorité apprécient les faits, la procédure et la gravité. "
        "Ne pas remplacer ce plafond AGEC par le plafond de 100 000 € de l'article L. 229-69, qui vise un autre régime."
    ),
)

CARBON_FINE = SanctionProfile(
    mechanism="Amende administrative",
    authority="Autorité administrative compétente dans les conditions réglementaires",
    legal_basis="Article L. 229-69 du Code de l'environnement",
    max_natural_person_eur=20000,
    max_legal_person_eur=100000,
    may_scale_to_advertising_spend=True,
    amount_is_automatic=False,
    notes=(
        "Les plafonds peuvent être portés jusqu'à la totalité des dépenses consacrées à l'opération illégale. "
        "Le montant dépend de la procédure et des faits; ce n'est pas un cumul automatique."
    ),
)

CONSUMER_DECEPTIVE_FINE = SanctionProfile(
    mechanism="Sanctions administratives et pénales (pratique commerciale trompeuse)",
    authority="DGCCRF / Juridictions judiciaires (Code de la consommation)",
    legal_basis="Articles L. 121-2 et L. 132-2 du Code de la consommation",
    max_natural_person_eur=300000,
    max_legal_person_eur=1500000,
    may_scale_to_advertising_spend=True,
    amount_is_automatic=False,
    notes=(
        "Plafond légal encouru pour pratique commerciale trompeuse : 300 000 € (personne physique), "
        "1 500 000 € (personne morale - quintuple en application de l'art. 131-38 CP). "
        "L'amende peut être portée de manière proportionnée à 10 % du chiffre d'affaires moyen annuel ou 50 % des dépenses engagées."
    ),
)


PRODUCT_OR_PACKAGING = frozenset({Surface.PRODUCT_LABEL, Surface.PACKAGING})
CONSUMER_MARKETING = frozenset(
    {
        Surface.PRODUCT_LABEL,
        Surface.PACKAGING,
        Surface.ADVERTISEMENT,
        Surface.ONLINE_STORE,
    }
)

RULES: tuple[RegulatoryRule, ...] = (
    RegulatoryRule(
        rule_id="RULE_AGEC_BIODEGRADABLE",
        title="Mention « biodégradable » sur produit ou emballage",
        legal_reference=(
            "Articles L. 541-9-1 et R. 541-230 du Code de l'environnement "
            "(produit ou emballage neuf destiné au consommateur)"
        ),
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041555718",
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000049389990",
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043959912/2026-09-24",
        ),
        claim_types=frozenset({ClaimType.BIODEGRADABLE}),
        legal_force=LegalForce.BINDING_FR,
        severity=Severity.CRITICAL,
        rule_kind=RuleKind.ABSOLUTE_PROHIBITION,
        scope="Mention figurant sur un produit ou emballage; contrôle renforcé pour les produits neufs destinés au consommateur.",
        surfaces=PRODUCT_OR_PACKAGING,
        effective_from=date(2022, 1, 1),
        sanction=AGEC_FINE,
        priority=1,
        notes=(
            "Aucune preuve technique ni certification ne neutralise l'interdiction AGEC.",
            "La référence réglementaire en vigueur est R. 541-230; R. 541-221 ne doit pas être cité comme numéro actuel de cette interdiction.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_AGEC_NATURE_FRIENDLY",
        title="Mention équivalente à « respectueux de l'environnement »",
        legal_reference=(
            "Articles L. 541-9-1 et R. 541-230 du Code de l'environnement "
            "(produit ou emballage neuf destiné au consommateur)"
        ),
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041555718",
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000049389990",
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043959912/2026-09-24",
        ),
        claim_types=frozenset({ClaimType.NATURE_FRIENDLY}),
        legal_force=LegalForce.BINDING_FR,
        severity=Severity.CRITICAL,
        rule_kind=RuleKind.ABSOLUTE_PROHIBITION,
        scope="Allégation environnementale équivalente imprimée sur un produit ou emballage; qualification de l'équivalence à valider en contexte.",
        surfaces=PRODUCT_OR_PACKAGING,
        effective_from=date(2022, 1, 1),
        sanction=AGEC_FINE,
        priority=2,
        notes=(
            "Pas de safe harbor de preuve pour l'interdiction AGEC; la qualification d'une formule comme équivalente dépend de son contexte.",
            "Pour un support publicitaire qui n'est pas le produit ou son emballage, examiner séparément le droit des pratiques commerciales trompeuses.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_EU_GENERIC_CLAIM",
        title="Allégation environnementale générique sans excellente performance reconnue pertinente",
        legal_reference=(
            "Directive (UE) 2024/825 modifiant la directive 2005/29/CE, notamment définition de l'allégation générique "
            "et annexe I, point 4a; application prévue à compter du 27 septembre 2026"
        ),
        source_urls=(
            "https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra",
            "https://eur-lex.europa.eu/eli/dir/2005/29/2026-09-27/fra",
        ),
        claim_types=frozenset(
            {
                ClaimType.NATURE_FRIENDLY,
                ClaimType.GENERIC_ENVIRONMENTAL,
            }
        ),
        legal_force=LegalForce.EU_DIRECTIVE_DATE_GATED,
        severity=Severity.HIGH,
        rule_kind=RuleKind.SAFE_HARBOR,
        scope="Communication commerciale B2C; l'allégation doit être générique et non spécifiée de manière claire et visible sur le même support.",
        required_evidence=(
            "Performance environnementale excellente reconnue et pertinente pour le sens exact de l'allégation.",
            "Preuve officielle, valide, liée au produit et au périmètre revendiqué.",
        ),
        safe_harbors=(
            SafeHarbor(
                safe_harbor_id="EU_ECOLABEL_RELEVANT",
                title="Label écologique de l'Union européenne",
                evidence_kind="ecolabel_certificate",
                conditions=(
                    "numéro de licence confirmé par un registre de confiance configuré côté serveur",
                    "certificat valide à la date de l'allégation",
                    "produit, catégorie et critère du label couvrant la revendication précise",
                ),
            ),
            SafeHarbor(
                safe_harbor_id="OFFICIALLY_RECOGNISED_TYPE_I",
                title="Écolabel EN ISO 14024 Type I officiellement reconnu",
                evidence_kind="ecolabel_certificate",
                conditions=(
                    "schéma officiellement reconnu dans l'État membre concerné",
                    "certificat valide et champ d'application correspondant au produit et à l'allégation",
                ),
            ),
        ),
        surfaces=CONSUMER_MARKETING,
        effective_from=date(2026, 9, 27),
        priority=10,
        notes=(
            "Un certificat saisi par l'utilisateur n'est pas réputé vérifié: le Safe Harbor exige un registre de confiance côté serveur.",
            "Un label ne couvre que les performances et catégories effectivement visées par ses critères; il ne lève jamais l'interdiction AGEC distincte.",
            "Le statut de transposition nationale doit être confirmé pour le marché choisi; la date d'application de la directive ne vaut pas à elle seule preuve d'une transposition française.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_EU_CARBON_NEUTRAL_COMPENSATION",
        title="Allégation produit de neutralité, réduction ou impact positif fondée sur la compensation carbone",
        legal_reference=(
            "Directive (UE) 2024/825, annexe modifiant l'annexe I de la directive 2005/29/CE, point 4c; "
            "application prévue à compter du 27 septembre 2026"
        ),
        source_urls=(
            "https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra",
            "https://eur-lex.europa.eu/eli/dir/2005/29/2026-09-27/fra",
        ),
        claim_types=frozenset({ClaimType.CARBON_NEUTRALITY}),
        legal_force=LegalForce.EU_DIRECTIVE_DATE_GATED,
        severity=Severity.CRITICAL,
        rule_kind=RuleKind.ABSOLUTE_PROHIBITION,
        scope="Allégation B2C attribuant au produit un impact climatique neutre, réduit ou positif sur la base de compensations hors chaîne de valeur.",
        required_evidence=(
            "Établir si l'allégation attribue l'impact au produit et si elle repose sur des compensations hors chaîne de valeur.",
        ),
        surfaces=CONSUMER_MARKETING,
        effective_from=date(2026, 9, 27),
        priority=5,
        notes=(
            "Les crédits carbone ne constituent pas un safe harbor pour une allégation d'impact produit neutre/réduit/positif.",
            "La directive n'interdit pas toute information factuelle, non trompeuse, sur le financement de projets carbone.",
            "Avant la date d'application, le régime français distinct de l'article L. 229-68 doit être contrôlé.",
            "L'activation comme règle nationale opposable est conditionnée à la vérification de la transposition dans le pays concerné.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_FR_CARBON_NEUTRAL_DISCLOSURE",
        title="Allégation publicitaire de neutralité carbone: dossier public exigé en droit français",
        legal_reference="Articles L. 229-68 et L. 229-69 du Code de l'environnement; textes réglementaires d'application",
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043960256",
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043960258",
            "https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006074220/LEGISCTA000043960254/",
        ),
        claim_types=frozenset({ClaimType.CARBON_NEUTRALITY}),
        legal_force=LegalForce.BINDING_FR,
        severity=Severity.HIGH,
        rule_kind=RuleKind.DISCLOSURE_GATE,
        scope="Publicité ou communication commerciale en France affirmant la neutralité carbone d'un produit ou service, ou une formule équivalente.",
        required_evidence=(
            "Bilan des émissions directes et indirectes du produit ou service, aisément accessible au public.",
            "Démarche d'évitement puis de réduction avant compensation; trajectoire avec objectifs annuels quantifiés.",
            "Modalités de compensation des émissions résiduelles conformes aux standards réglementaires applicables.",
            "Rapport et pièces justificatives publiquement accessibles selon les textes d'application.",
        ),
        surfaces=CONSUMER_MARKETING,
        effective_from=date(2023, 1, 1),
        sanction=CARBON_FINE,
        priority=4,
        notes=(
            "En droit français antérieur à l'application de la directive 2024/825, la neutralité carbone publicitaire est encadrée par des obligations de preuve et de publication; elle n'est pas codée ici comme une interdiction absolue générale.",
            "La présence de métadonnées ne vérifie ni le contenu du bilan ni la qualité réelle des crédits.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_EU_COMPARATIVE_LCA",
        title="Comparaison environnementale: contrôle de preuve par ACV comparative multicritère",
        legal_reference=(
            "COM(2023) 166 final, proposition de directive sur les allégations écologiques explicites "
            "(Green Claims Directive), non adoptée comme règle contraignante"
        ),
        source_urls=(
            "https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=COM%3A2023%3A166%3AFIN",
            "https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra",
        ),
        claim_types=frozenset({ClaimType.COMPARATIVE}),
        legal_force=LegalForce.PROPOSAL_ONLY,
        severity=Severity.MEDIUM,
        rule_kind=RuleKind.ADVISORY_EVIDENCE_GATE,
        scope="Comparaison de performances environnementales entre produits, marques ou générations de produits.",
        required_evidence=(
            "ACV couvrant plusieurs catégories d'impact, documentée selon ISO 14040/14044.",
            "Même unité fonctionnelle, frontières du système et règles de comparaison pour les produits comparés.",
            "Produit de référence, période, données et limites explicitement identifiés.",
        ),
        surfaces=CONSUMER_MARKETING,
        priority=30,
        notes=(
            "Ce contrôle constitue un garde-fou interne; il ne crée pas, à lui seul, une infraction ni une amende au titre d'une directive Green Claims adoptée.",
            "La proposition COM(2023) 166 n'est pas une directive en vigueur. Le dossier législatif a connu une annonce d'intention de retrait et une incertitude procédurale; revalider le statut avant toute mise à jour du Rule Book.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_ISO_RECYCLABLE_PERCENTAGE",
        title="Allégation « recyclable »: disponibilité réelle de collecte, tri et traitement",
        legal_reference=(
            "Contrôle d'alignement sur ISO 14021:2016 (édition demandée, retirée depuis la publication d'ISO 14021:2026); "
            "les obligations légales françaises de recyclabilité peuvent aussi s'appliquer selon la catégorie du produit"
        ),
        source_urls=(
            "https://www.iso.org/standard/66652.html",
            "https://www.iso.org/standard/14021",
            "https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006074220/LEGISCTA000045728452/",
        ),
        claim_types=frozenset({ClaimType.RECYCLABLE}),
        legal_force=LegalForce.VOLUNTARY_STANDARD,
        severity=Severity.MEDIUM,
        rule_kind=RuleKind.ADVISORY_EVIDENCE_GATE,
        scope="Auto-déclaration environnementale « recyclable » sur produit, emballage ou communication B2C.",
        required_evidence=(
            "Filière effective identifiée par territoire: collecte, accès consommateur, tri et traitement industriel.",
            "Périmètre exact du produit ou composant auquel la recyclabilité se rapporte.",
            "Vérification des critères réglementaires français spécifiques si le produit relève des catégories visées par R. 541-228 VI.",
        ),
        surfaces=CONSUMER_MARKETING,
        priority=40,
        notes=(
            "ISO est une norme volontaire, non une loi ni une certification légale universelle; l'absence de fiche de filière est un blocage de publication interne, pas une infraction automatiquement établie.",
            "ISO indique qu'ISO 14021:2016 a été retirée et remplacée par l'édition 2026; le détail de cette règle doit être revu avant production.",
            "Le droit français prévoit par ailleurs des critères détaillés de recyclabilité pour des catégories de produits déterminées (notamment R. 541-228 VI).",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_EVIDENCE_QUANTIFIED_CLAIM",
        title="Allégation climatique chiffrée: contrôle de preuve et traçabilité",
        legal_reference=(
            "Contrôle probatoire interne; Code de la consommation, articles L. 121-2 et L. 121-3 si la présentation est trompeuse. "
            "ISO 14044 est un seuil de preuve choisi par ce moteur, non une exigence légale générale automatique pour tout chiffre environnemental."
        ),
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000028748875/2026-09-20",
            "https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra",
        ),
        claim_types=frozenset({ClaimType.QUANTIFIED_CLIMATE}),
        legal_force=LegalForce.INTERNAL_EVIDENCE_CONTROL,
        severity=Severity.HIGH,
        rule_kind=RuleKind.EVIDENCE_GATE,
        scope="Allégation climatique quantitative détectée dans le texte fourni (ex. -40 % CO₂).",
        required_evidence=(
            "Rapport ACV identifié, avec ISO 14044, unité fonctionnelle, frontières et période de référence.",
            "Données correspondant au produit, au site, au marché et au chiffre revendiqué.",
            "Revue humaine du rapport et de la formulation avant diffusion.",
        ),
        surfaces=CONSUMER_MARKETING,
        priority=50,
        notes=(
            "L'absence d'ACV déclenche un rejet conditionnel de publication dans l'outil, mais ne suffit pas à conclure automatiquement à une infraction légale.",
            "Le moteur vérifie des métadonnées et ne valide pas la méthodologie, les jeux de données ni le contenu du rapport.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_AGEC_COMPOSTABLE",
        title="Mention « compostable » : encadrement strict et interdiction isolée",
        legal_reference=(
            "Articles L. 541-9-1 et R. 541-230 du Code de l'environnement "
            "(produits ou emballages neufs destinés au consommateur)"
        ),
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041555718",
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000049389990",
        ),
        claim_types=frozenset({ClaimType.COMPOSTABLE}),
        legal_force=LegalForce.BINDING_FR,
        severity=Severity.HIGH,
        rule_kind=RuleKind.EVIDENCE_GATE,
        scope="Emballage ou produit plastique neuf destiné au consommateur.",
        required_evidence=(
            "Preuve de compostabilité domestique selon la norme NF T 51-800 ou EN 13432.",
            "Pour les emballages en plastique, l'aptitude au compostage domestique est obligatoire pour revendiquer 'compostable'.",
            "Précision expresse de la modalité ('compostable en compostage domestique' ou 'compostable en installation industrielle').",
        ),
        surfaces=PRODUCT_OR_PACKAGING,
        effective_from=date(2022, 1, 1),
        sanction=AGEC_FINE,
        priority=3,
        notes=(
            "L'emploi de la mention 'compostable' sans préciser 'en compostage domestique' ou 'en installation industrielle' est interdit.",
            "Les emballages en plastique ne peuvent être qualifiés de compostables que s'ils sont compostables en compostage domestique.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_CONSUMER_CHEMICAL_FREE",
        title="Mention « sans produits chimiques » : allégation trompeuse par nature",
        legal_reference="Articles L. 121-2 et L. 132-2 du Code de la consommation; Guide DGCCRF des allégations environnementales",
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000028748875/2026-09-20",
            "https://www.economie.gouv.fr/dgccrf/guide-des-allegations-environnementales",
        ),
        claim_types=frozenset({ClaimType.CHEMICAL_FREE}),
        legal_force=LegalForce.BINDING_FR,
        severity=Severity.CRITICAL,
        rule_kind=RuleKind.ABSOLUTE_PROHIBITION,
        scope="Toute communication commerciale ou emballage destiné aux consommateurs.",
        surfaces=CONSUMER_MARKETING,
        effective_from=date(2020, 1, 1),
        sanction=CONSUMER_DECEPTIVE_FINE,
        priority=2,
        notes=(
            "Toute substance matérielle (naturelle ou de synthèse) est une composition chimique au sens scientifique.",
            "L'allégation générale 'sans produit chimique' ou 'sans chimie' est jugée trompeuse par nature par la DGCCRF.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_CONSUMER_ZERO_POLLUTION",
        title="Allégation « zéro déchet / zéro pollution / non polluant » : promesse globale infondée",
        legal_reference="Article L. 121-2 du Code de la consommation; Directive (UE) 2024/825 (annexe I, point 4b)",
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000028748875/2026-09-20",
            "https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra",
        ),
        claim_types=frozenset({ClaimType.ZERO_POLLUTION}),
        legal_force=LegalForce.BINDING_FR,
        severity=Severity.HIGH,
        rule_kind=RuleKind.EVIDENCE_GATE,
        scope="Communication commerciale ou étiquetage d'un produit manufacturé ou service.",
        required_evidence=(
            "Démonstration exhaustive sur l'ensemble du cycle de vie prouvant l'absence totale de déchet ou de pollution.",
            "Justification technique des étapes de fabrication, transport, usage et fin de vie.",
        ),
        surfaces=CONSUMER_MARKETING,
        effective_from=date(2021, 1, 1),
        sanction=CONSUMER_DECEPTIVE_FINE,
        priority=6,
        notes=(
            "Une allégation d'absence totale d'impact sur l'environnement pour un produit manufacturé est présumée trompeuse en l'absence de preuve scientifique absolue couvrant l'ensemble du cycle de vie.",
        ),
    ),
    RegulatoryRule(
        rule_id="RULE_AGEC_RECYCLED_UNQUANTIFIED",
        title="Mention « matière recyclée » sans proportion chiffrée obligatoire",
        legal_reference="Articles L. 541-9-1 et R. 541-227 du Code de l'environnement (loi AGEC)",
        source_urls=(
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000045728450/",
            "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043959912/2026-09-24",
        ),
        claim_types=frozenset({ClaimType.RECYCLED_CONTENT}),
        legal_force=LegalForce.BINDING_FR,
        severity=Severity.HIGH,
        rule_kind=RuleKind.EVIDENCE_GATE,
        scope="Produits ou emballages générateurs de déchets mis sur le marché français.",
        required_evidence=(
            "Formulation exacte normalisée obligatoire : « comporte au moins [X] % de matières recyclées ».",
            "Traçabilité documentaire et certification de la chaîne de contrôle (ex. GRS, EuCertPlast, ISO 14021).",
        ),
        surfaces=PRODUCT_OR_PACKAGING,
        effective_from=date(2022, 1, 1),
        sanction=AGEC_FINE,
        priority=8,
        notes=(
            "L'article R. 541-227 impose obligatoirement la mention de la proportion chiffrée minimale.",
            "Les mentions générales comme 'fabriqué avec du plastique recyclé' sans pourcentage sont prohibées sur les emballages et produits visés.",
        ),
    ),
)

RULE_BY_ID = {rule.rule_id: rule for rule in RULES}


def _canonical_rule(rule: RegulatoryRule) -> dict[str, object]:
    return {
        "rule_id": rule.rule_id,
        "title": rule.title,
        "law_reference": rule.legal_reference,
        "sources": list(rule.source_urls),
        "claim_types": sorted(item.value for item in rule.claim_types),
        "legal_force": rule.legal_force.value,
        "severity": rule.severity.value,
        "rule_kind": rule.rule_kind.value,
        "scope": rule.scope,
        "required_evidence": list(rule.required_evidence),
        "effective_from": rule.effective_from.isoformat() if rule.effective_from else None,
        "safe_harbors": [
            {
                "id": harbor.safe_harbor_id,
                "title": harbor.title,
                "evidence_kind": harbor.evidence_kind,
                "conditions": list(harbor.conditions),
            }
            for harbor in rule.safe_harbors
        ],
        "sanction": rule.sanction.model_dump(mode="json") if rule.sanction else None,
        "notes": list(rule.notes),
    }


RULEBOOK_VERSION = "2026-09-24+" + hashlib.sha256(
    json.dumps([_canonical_rule(rule) for rule in RULES], sort_keys=True, ensure_ascii=False).encode("utf-8")
).hexdigest()[:12]
