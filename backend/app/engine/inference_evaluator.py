"""Deterministic legal inference and evidence decision tree."""

from __future__ import annotations

from datetime import date

from app.engine.fact_extractor import FactExtractor
from app.engine.legal_remediation import remediation_for
from app.engine.proof_validator import ProofValidator
from app.engine.rule_book import RULES, RULE_BY_ID, RegulatoryRule, RuleKind
from app.models.legal_types import (
    CarbonOffsetEvidence,
    ClaimType,
    DetectedClaim,
    EvidenceCheck,
    EvidenceDossier,
    EvidenceStatus,
    LegalAssessment,
    LegalForce,
    ReasoningStep,
    Severity,
    Surface,
    Verdict,
)
from app.models.schemas import AuditContext


EU_RULE_IDS = {"RULE_EU_GENERIC_CLAIM", "RULE_EU_CARBON_NEUTRAL_COMPENSATION"}


class InferenceEvaluator:
    """Run typed rules in a stable priority order and return a traceable result.

    `evaluate_claim` implements the requested single-result interface.
    `evaluate_claim_all` returns every matching rule finding (e.g. a
    biodegradable packaging assertion can trigger both AGEC and EU checks).
    """

    def __init__(
        self,
        proof_validator: ProofValidator | None = None,
        *,
        fr_2024_825_transposition_status: str = "unknown",
        fact_extractor: FactExtractor | None = None,
    ):
        self.proof_validator = proof_validator or ProofValidator()
        self.fr_2024_825_transposition_status = (
            fr_2024_825_transposition_status
            if fr_2024_825_transposition_status in {"unknown", "implemented", "not_implemented"}
            else "unknown"
        )
        self.fact_extractor = fact_extractor or FactExtractor()

    def evaluate_claim(
        self,
        claim: DetectedClaim,
        attached_evidence: EvidenceDossier,
        context: AuditContext | None = None,
    ) -> LegalAssessment:
        """Evaluate one detected claim against its highest-priority matched rule."""
        findings = self.evaluate_claim_all(claim, attached_evidence, context)
        if not findings:
            raise ValueError(f"Aucune règle configurée pour le type de claim {claim.claim_type.value}")
        return findings[0]

    def evaluate_claim_all(
        self,
        claim: DetectedClaim,
        attached_evidence: EvidenceDossier,
        context: AuditContext | None = None,
    ) -> list[LegalAssessment]:
        audit_context = context or AuditContext()
        rules = sorted(
            (rule for rule in RULES if claim.claim_type in rule.claim_types),
            key=lambda rule: (rule.priority, rule.rule_id),
        )
        return [self._evaluate_rule(rule, claim, attached_evidence, audit_context) for rule in rules]

    def evaluate_text(
        self,
        source_text: str,
        attached_evidence: EvidenceDossier,
        context: AuditContext | None = None,
    ) -> tuple[list[DetectedClaim], list[LegalAssessment]]:
        audit_context = context or AuditContext()
        claims = self.fact_extractor.extract(source_text)
        findings: list[LegalAssessment] = []
        for claim in claims:
            findings.extend(self.evaluate_claim_all(claim, attached_evidence, audit_context))
        return claims, findings

    def _evaluate_rule(
        self,
        rule: RegulatoryRule,
        claim: DetectedClaim,
        dossier: EvidenceDossier,
        context: AuditContext,
    ) -> LegalAssessment:
        steps: list[ReasoningStep] = []
        checks: list[EvidenceCheck] = []
        safe_harbor = False
        safe_harbor_reason: str | None = None
        is_violation = False
        caveat: str | None = None
        missing: list[str] = []

        def add_step(code: str, finding: str, significance: str) -> None:
            steps.append(
                ReasoningStep(
                    step=len(steps) + 1,
                    code=code,
                    finding=finding,
                    legal_significance=significance,
                )
            )

        add_step(
            "CLAIM_DETECTED",
            f"Déclencheur lexical déterministe « {claim.trigger_text} »; type={claim.claim_type.value}; assertion={'affirmative' if claim.affirmative else 'négative/niée'}.",
            "Une détection lexicale est un fait d'entrée, pas à elle seule une qualification juridique définitive.",
        )
        add_step(
            "RULE_SELECTED",
            f"Règle formelle sélectionnée: {rule.rule_id} ({rule.title}).",
            f"Référence principale: {rule.legal_reference}.",
        )

        if not claim.affirmative:
            verdict = Verdict.NOT_APPLICABLE
            caveat = "La formulation détectée paraît nier la caractéristique; vérifiez le contexte complet et la maquette réellement publiée."
            add_step("POLARITY_CHECK", f"Négation détectée: {claim.negation_cue or 'oui'}.", "Une assertion environnementale positive n'est pas établie par cette occurrence.")
            return self._build_assessment(rule, claim, verdict, steps, checks, safe_harbor, safe_harbor_reason, is_violation, missing, caveat, context)

        scope_result = self._scope_result(rule, context)
        add_step("SCOPE_CHECK", scope_result[1], scope_result[2])
        if scope_result[0] == "OUT_OF_SCOPE":
            return self._build_assessment(
                rule, claim, Verdict.NOT_APPLICABLE, steps, checks, safe_harbor,
                safe_harbor_reason, is_violation, missing,
                "La règle ne s'applique pas automatiquement au support ou au territoire indiqué; d'autres règles peuvent s'appliquer.", context,
            )
        if scope_result[0] == "UNCERTAIN":
            verdict = Verdict.REVIEW_REQUIRED
            caveat = scope_result[2]
            return self._build_assessment(rule, claim, verdict, steps, checks, safe_harbor, safe_harbor_reason, is_violation, missing, caveat, context)

        if rule.effective_from and context.as_of_date < rule.effective_from:
            verdict = Verdict.UPCOMING
            add_step(
                "TEMPORAL_CHECK",
                f"Date d'audit {context.as_of_date.isoformat()} antérieure à la date d'application {rule.effective_from.isoformat()}.",
                "Aucune violation au titre de cette règle date-gated n'est prononcée pour cette date; préparation recommandée.",
            )
            return self._build_assessment(rule, claim, verdict, steps, checks, safe_harbor, safe_harbor_reason, is_violation, missing, caveat, context)

        add_step("TEMPORAL_CHECK", f"Règle temporellement applicable à la date {context.as_of_date.isoformat()}.", "Poursuite de l'analyse de fond.")

        if rule.rule_id in {"RULE_AGEC_BIODEGRADABLE", "RULE_AGEC_NATURE_FRIENDLY"}:
            verdict = Verdict.STRICTLY_PROHIBITED
            is_violation = True
            add_step(
                "STRICT_PROHIBITION",
                "La mention positive correspond à une catégorie explicitement interdite sur ce support dans le champ contrôlé.",
                "Aucun justificatif, label ou Safe Harbor ne neutralise cette interdiction AGEC.",
            )

        elif rule.rule_id == "RULE_EU_GENERIC_CLAIM":
            if claim.has_specific_qualifier:
                verdict = Verdict.REVIEW_REQUIRED
                caveat = "Un détail spécifique apparaît dans la phrase, mais le moteur ne peut pas établir qu'il précise clairement et bien visiblement cette allégation sur le même support, ni vérifier son exactitude; revue requise."
                add_step("GENERICITY_CHECK", "Un détail spécifique a été détecté dans la phrase.", "La cooccurrence textuelle ne suffit pas à établir l'exception: vérifier la lisibilité, le lien avec l'allégation et les preuves sur le support réel.")
            else:
                check = self.proof_validator.validate_ecolabel(dossier, claim.claim_type, context)
                checks.append(check)
                add_step("SAFE_HARBOR_CHECK", check.detail, "Seule une performance excellente reconnue, valide et pertinente pour le produit et le sens de la claim peut ouvrir le Safe Harbor.")
                if check.status == EvidenceStatus.VERIFIED:
                    safe_harbor = True
                    safe_harbor_reason = check.detail
                    verdict = Verdict.COMPLIANT
                    add_step("SAFE_HARBOR_GRANTED", "Le registre serveur corroborre une licence valide et pertinente.", "Safe Harbor établi pour cette règle uniquement; il n'efface aucune interdiction française autonome.")
                elif check.status == EvidenceStatus.NOT_PROVIDED:
                    verdict = Verdict.STRICTLY_PROHIBITED
                    is_violation = True
                    missing = list(check.missing_fields)
                    add_step("SAFE_HARBOR_DENIED", "Aucune preuve de performance excellente reconnue n'est fournie.", "La claim générique ne satisfait pas la condition prévue par la directive à sa date d'application.")
                else:
                    verdict = Verdict.REVIEW_REQUIRED
                    missing = list(check.missing_fields)
                    caveat = "Le certificat est déclaré mais n'est pas corroboré par un registre serveur ou son périmètre n'est pas confirmé; bloquer la publication jusqu'à revue."
                    add_step("SAFE_HARBOR_UNVERIFIED", check.detail, "Un numéro communiqué par le demandeur ne suffit pas à établir le Safe Harbor.")

        elif rule.rule_id == "RULE_EU_CARBON_NEUTRAL_COMPENSATION":
            offset_items = [item for item in dossier.items if isinstance(item, CarbonOffsetEvidence)]
            offsetting_basis = claim.has_offsetting_signal or bool(offset_items)
            if not offsetting_basis:
                verdict = Verdict.REVIEW_REQUIRED
                caveat = "Le texte ne démontre pas à lui seul que la claim repose sur une compensation hors chaîne de valeur; vérifier la méthode réelle et le dossier avant publication."
                add_step("OFFSET_BASIS_CHECK", "Aucun indice lexical ou justificatif de crédit carbone n'est associé à la claim.", "Le point 4c vise les claims fondées sur l'offsetting; la base factuelle doit être établie.")
            else:
                verdict = Verdict.STRICTLY_PROHIBITED
                is_violation = True
                add_step("OFFSET_BASIS_CHECK", "Indice de compensation / crédit carbone rattaché à une claim d'impact produit.", "La compensation hors chaîne de valeur ne constitue pas un Safe Harbor pour une allégation produit neutre, réduite ou positive.")

        elif rule.rule_id == "RULE_FR_CARBON_NEUTRAL_DISCLOSURE":
            checks.extend(self.proof_validator.validate_french_carbon_neutrality(dossier, offsetting_asserted=True))
            missing = sorted({field for check in checks for field in check.missing_fields})
            incomplete = any(check.status in {EvidenceStatus.NOT_PROVIDED, EvidenceStatus.INCOMPLETE} for check in checks)
            if incomplete:
                verdict = Verdict.CONDITIONAL_REJECT
                add_step("PUBLIC_DISCLOSURE_CHECK", "Le dossier français de neutralité ne présente pas tous les éléments de bilan, trajectoire et compensation requis.", "La claim ne passe pas le contrôle probatoire préalable; suspendre jusqu'à production/revue des pièces.")
            else:
                verdict = Verdict.REVIEW_REQUIRED
                caveat = "Les métadonnées semblent complètes, mais le contenu du bilan, la trajectoire, les crédits et la publication n'ont pas été audités par le moteur."
                add_step("PUBLIC_DISCLOSURE_CHECK", "Les métadonnées déclarées couvrent les catégories de pièces attendues.", "Une validation juridique et technique du contenu demeure nécessaire; aucune conformité substantielle n'est attestée.")

        elif rule.rule_id == "RULE_EU_COMPARATIVE_LCA":
            check = self.proof_validator.validate_lca(dossier, context, comparative=True)
            checks.append(check)
            missing = list(check.missing_fields)
            if check.status in {EvidenceStatus.NOT_PROVIDED, EvidenceStatus.INCOMPLETE}:
                verdict = Verdict.CONDITIONAL_REJECT
                add_step("COMPARABILITY_CHECK", check.detail, "Blocage de publication selon le seuil interne; la proposition Green Claims citée n'est pas une règle adoptée.")
            else:
                verdict = Verdict.REVIEW_REQUIRED
                caveat = "La proposition COM(2023) 166 n'est pas traitée comme une loi en vigueur; les métadonnées d'ACV ne remplacent pas l'examen du contenu et du droit de la consommation applicable."
                add_step("COMPARABILITY_CHECK", check.detail, "Métadonnées de comparaison présentes; validation technique et juridique humaine requise.")

        elif rule.rule_id == "RULE_ISO_RECYCLABLE_PERCENTAGE":
            check = self.proof_validator.validate_recycling_route(dossier)
            checks.append(check)
            missing = list(check.missing_fields)
            if check.status in {EvidenceStatus.NOT_PROVIDED, EvidenceStatus.INCOMPLETE}:
                verdict = Verdict.CONDITIONAL_REJECT
                add_step("ROUTE_CHECK", check.detail, "Blocage selon le seuil de preuve interne; ce contrôle ISO volontaire ne suffit pas à établir une infraction légale.")
            else:
                verdict = Verdict.REVIEW_REQUIRED
                caveat = "Les données de filière sont déclaratives; l'édition ISO 14021:2016 a été retirée en 2026 et le contenu doit être vérifié au regard de l'édition courante et des règles françaises de catégorie."
                add_step("ROUTE_CHECK", check.detail, "Métadonnées présentes mais filière et catégorie réglementaire non vérifiées indépendamment.")

        elif rule.rule_id == "RULE_EVIDENCE_QUANTIFIED_CLAIM":
            check = self.proof_validator.validate_lca(dossier, context, comparative=False)
            checks.append(check)
            missing = list(check.missing_fields)
            if check.status in {EvidenceStatus.NOT_PROVIDED, EvidenceStatus.INCOMPLETE}:
                verdict = Verdict.CONDITIONAL_REJECT
                add_step("QUANTIFIED_PROOF_CHECK", check.detail, "Blocage préventif interne; l'absence d'ISO 14044 ne démontre pas à elle seule une infraction.")
            else:
                verdict = Verdict.REVIEW_REQUIRED
                caveat = "Le moteur ne vérifie pas le contenu du rapport ni l'exactitude du chiffre; revue humaine obligatoire avant publication."
                add_step("QUANTIFIED_PROOF_CHECK", check.detail, "Les métadonnées sont présentes, mais la preuve de fond reste à auditer.")

        else:
            verdict = Verdict.REVIEW_REQUIRED
            caveat = "Aucun évaluateur spécialisé n'est configuré pour cette combinaison de faits et de règle."
            add_step("FALLBACK", "La règle a été trouvée, mais aucune branche d'inférence spécialisée n'a été configurée.", "Ne pas publier sans revue juridique.")

        # A directive does not, by itself, prove that a French transposition is
        # in force. Keep the substantive EU test visible, but avoid claiming a
        # French legal violation when the national implementation state is unknown.
        if rule.rule_id in EU_RULE_IDS and context.jurisdiction == "FR":
            status = self.fr_2024_825_transposition_status
            if status != "implemented" and verdict not in {Verdict.NOT_APPLICABLE, Verdict.UPCOMING}:
                prior_verdict = verdict
                verdict = Verdict.REVIEW_REQUIRED
                is_violation = False
                caveat = (
                    "La date d'application de la directive est atteinte, mais l'état de sa transposition française n'est pas confirmé "
                    f"par la configuration du moteur (état={status}). La branche substantielle a conclu à {prior_verdict.value}; "
                    "bloquer la diffusion et vérifier le texte français applicable."
                )
                add_step(
                    "NATIONAL_TRANSPOSITION_CHECK",
                    f"État de transposition FR configuré: {status}.",
                    "Pas de conclusion de violation nationale automatique à partir de la seule directive; confirmation du texte français requise.",
                )

        return self._build_assessment(rule, claim, verdict, steps, checks, safe_harbor, safe_harbor_reason, is_violation, missing, caveat, context)

    def _scope_result(self, rule: RegulatoryRule, context: AuditContext) -> tuple[str, str, str]:
        if rule.legal_force == LegalForce.BINDING_FR and context.jurisdiction != "FR":
            return "OUT_OF_SCOPE", f"Juridiction déclarée: {context.jurisdiction}.", "La règle française n'est pas appliquée comme telle hors de France."
        if rule.surfaces and context.surface not in rule.surfaces:
            if context.surface == Surface.UNKNOWN:
                return "UNCERTAIN", "Le support de diffusion n'est pas précisé.", "Le champ de la règle ne peut pas être tranché sans connaître le support; compléter le contexte."
            return "OUT_OF_SCOPE", f"Support déclaré: {context.surface.value}.", "Le support déclaré ne correspond pas au champ de cette règle."
        if rule.rule_id in {"RULE_EU_GENERIC_CLAIM", "RULE_EU_CARBON_NEUTRAL_COMPENSATION"} and not context.consumer_facing:
            return "OUT_OF_SCOPE", "Communication déclarée B2B/non destinée aux consommateurs.", "La directive ciblée modifie le droit des pratiques commerciales B2C; d'autres règles peuvent s'appliquer."
        if rule.rule_id in {"RULE_AGEC_BIODEGRADABLE", "RULE_AGEC_NATURE_FRIENDLY"} and not context.consumer_facing:
            return "UNCERTAIN", "Le produit ou l'emballage est déclaré hors circuit consommateur.", "R. 541-230 vise le produit neuf destiné au consommateur; examiner le champ exact de L. 541-9-1 et la chaîne de mise sur le marché avant de conclure."
        return "IN_SCOPE", f"Juridiction={context.jurisdiction}; support={context.surface.value}; public_consommateur={context.consumer_facing}.", "La règle passe les filtres de champ déclarés par l'utilisateur."

    def _build_assessment(
        self,
        rule: RegulatoryRule,
        claim: DetectedClaim,
        verdict: Verdict,
        steps: list[ReasoningStep],
        checks: list[EvidenceCheck],
        safe_harbor: bool,
        safe_harbor_reason: str | None,
        is_violation: bool,
        missing: list[str],
        caveat: str | None,
        context: AuditContext,
    ) -> LegalAssessment:
        if not steps or steps[-1].code not in {"VERDICT"}:
            steps.append(
                ReasoningStep(
                    step=len(steps) + 1,
                    code="VERDICT",
                    finding=f"Verdict déterministe: {verdict.value}.",
                    legal_significance=(
                        "Une violation juridique est retenue dans le champ et à la date déclarés."
                        if is_violation
                        else "Ce verdict ne vaut pas constat officiel ni décision d'une autorité ou d'une juridiction."
                    ),
                )
            )
        return LegalAssessment(
            claim_id=claim.claim_id,
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            start_offset=claim.start_offset,
            end_offset=claim.end_offset,
            trigger_text=claim.trigger_text,
            rule_id=rule.rule_id,
            rule_title=rule.title,
            law_reference=rule.legal_reference,
            source_urls=list(rule.source_urls),
            legal_force=rule.legal_force,
            severity=rule.severity,
            verdict=verdict,
            is_legal_violation=is_violation,
            safe_harbor_applicable=safe_harbor,
            safe_harbor_reason=safe_harbor_reason,
            required_evidence=list(rule.required_evidence),
            evidence_checks=checks,
            reasoning_steps=steps,
            remediation=remediation_for(rule.rule_id, claim.claim_text, missing, as_of_date=context.as_of_date),
            sanction=rule.sanction,
            legal_caveat=caveat,
        )
