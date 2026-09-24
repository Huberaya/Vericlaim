"""Deterministic, template-based legal remediation; no generated facts or metrics."""

from __future__ import annotations

from datetime import date

from app.models.legal_types import Remediation


TEMPLATES: dict[str, tuple[str, str, str, list[str]]] = {
    "RULE_AGEC_BIODEGRADABLE": (
        "Sur un produit ou emballage neuf destiné au consommateur, la mention « biodégradable » est interdite par le Code de l'environnement. Une ACV, un essai de biodégradation ou un label ne peut pas régulariser cette mention au titre de l'AGEC.",
        "Supprimer « biodégradable ». Ne décrire que des caractéristiques matérielles précises, légalement utilisables et prouvées; fournir, le cas échéant, la consigne de tri réellement applicable au produit et au territoire concernés.",
        "Le Fournisseur garantit qu'aucun Produit ni Emballage livré au Client ne comporte les mentions « biodégradable », « respectueux de l'environnement » ou une allégation équivalente lorsque leur apposition est interdite par les articles L. 541-9-1 et R. 541-230 du Code de l'environnement. Avant toute modification d'étiquetage ou de support, le Fournisseur soumet au Client le texte exact, le périmètre du produit et les justificatifs correspondants. Le Fournisseur retire ou corrige sans délai tout support non conforme, sous réserve des procédures contractuelles applicables.",
        ["Retirer la mention du produit et de l'emballage.", "Vérifier les supports de vente réutilisant le même texte.", "Ne pas présenter une preuve technique comme une dérogation à l'interdiction."],
    ),
    "RULE_AGEC_NATURE_FRIENDLY": (
        "Le Code de l'environnement interdit sur certains produits et emballages les formules « respectueux de l'environnement » et les allégations qui leur sont équivalentes. Une preuve environnementale ne crée pas de dérogation à cette interdiction spécifique.",
        "Remplacer la formule générale par une caractéristique étroite, mesurable et documentée, en indiquant le produit ou la partie concernée, l'indicateur, la période et le périmètre. Faire valider la qualification avant impression.",
        "Le Fournisseur s'interdit d'apposer sur les produits, emballages ou documents soumis à l'interdiction AGEC toute allégation équivalente à « respectueux de l'environnement ». Toute allégation alternative doit être précise, circonscrite au bénéfice démontré, accompagnée de son périmètre et approuvée par écrit par le Client avant diffusion.",
        ["Retirer la formule vague.", "Faire qualifier juridiquement la nouvelle formule au regard du support.", "Conserver les justificatifs de l'indicateur et du périmètre."],
    ),
    "RULE_EU_GENERIC_CLAIM": (
        "À compter du 27 septembre 2026, une allégation environnementale générique adressée aux consommateurs est interdite si le professionnel ne peut démontrer une performance environnementale excellente reconnue et pertinente pour le sens de l'allégation. Un numéro de certificat simplement saisi n'est pas une vérification indépendante.",
        "Soit supprimer le terme générique, soit le remplacer sur le même support par une caractéristique précise, clairement visible et étayée. Si un label est invoqué, vérifier sa validité, le produit licencié et le critère exact couvert; cette démarche ne lève pas une interdiction AGEC autonome.",
        "Le Fournisseur ne peut utiliser une allégation environnementale générique qu'après remise au Client d'une preuve de performance excellente reconnue, valide à la date de diffusion et pertinente pour le produit et le sens précis de l'allégation. Tout numéro de licence doit être vérifié auprès du registre officiel ou d'un registre de confiance désigné par le Client. La validation au titre du présent article ne déroge à aucune interdiction nationale plus stricte.",
        ["Retirer ou spécifier le terme sur le même support.", "Vérifier le numéro de licence par un registre fiable.", "Vérifier que le critère du label porte bien sur l'allégation exacte.", "Faire confirmer la transposition nationale applicable."],
    ),
    "RULE_EU_CARBON_NEUTRAL_COMPENSATION": (
        "La directive (UE) 2024/825 prévoit l'interdiction, à compter de sa date d'application, des allégations attribuant à un produit un impact climatique neutre, réduit ou positif lorsqu'elles reposent sur des compensations d'émissions hors de sa chaîne de valeur. Des crédits carbone ne constituent pas un sauf-conduit pour cette allégation.",
        "Ne pas revendiquer la neutralité, la réduction ou un impact positif du produit sur la base de crédits carbone. Décrire séparément les émissions brutes et les réductions réelles mesurées dans la chaîne de valeur, avec période, périmètre et méthode. Une contribution à un projet carbone peut être décrite factuellement sans l'assimiler à une neutralité du produit.",
        "Le Fournisseur s'interdit d'attribuer au Produit un impact climatique « neutre », « réduit » ou « positif » sur la base de crédits carbone, de compensations ou d'achats de quotas hors de la chaîne de valeur. Toute information relative à une contribution climatique doit être présentée séparément, avec son montant, son bénéficiaire, son registre et sa preuve de retrait, sans suggérer qu'elle efface les émissions du Produit. Toute allégation de réduction réelle doit préciser son année de référence, son périmètre, son unité fonctionnelle et ses justificatifs.",
        ["Retirer l'assertion de neutralité fondée sur des crédits.", "Séparer les réductions réelles des contributions de compensation.", "Contrôler la date d'application et la transposition nationale."],
    ),
    "RULE_FR_CARBON_NEUTRAL_DISCLOSURE": (
        "En droit français, la publicité affirmant la neutralité carbone d'un produit ou service est encadrée par des obligations de bilan, de trajectoire priorisant l'évitement et la réduction, de compensation des résiduels et de mise à disposition du public. Le seul achat de crédits ne suffit pas à documenter ces obligations.",
        "En l'absence d'un dossier public complet, suspendre la formule de neutralité. À défaut, communiquer des valeurs absolues et des réductions réellement mesurées, avec leur périmètre et leur période, sans déclarer que la compensation rend le produit neutre.",
        "Avant toute publicité affirmant la neutralité carbone du Produit ou du Service, le Fournisseur remet au Client le bilan des émissions directes et indirectes, le périmètre retenu, la trajectoire priorisant l'évitement puis la réduction, les objectifs annuels quantifiés, les modalités de compensation des émissions résiduelles et les liens de publication requis par l'article L. 229-68 du Code de l'environnement et ses textes d'application. La diffusion est suspendue jusqu'à validation du dossier complet.",
        ["Publier les éléments prévus par l'article L. 229-68 et ses textes d'application.", "Contrôler la hiérarchie évitement / réduction / compensation.", "Faire vérifier le bilan et les crédits par un examinateur humain compétent."],
    ),
    "RULE_EU_COMPARATIVE_LCA": (
        "Le moteur bloque la comparaison comme contrôle de prudence, mais COM(2023) 166 est une proposition non adoptée: cette règle seule ne permet pas de conclure à une infraction. Une comparaison peut néanmoins être trompeuse au regard du droit de la consommation si elle est inexacte, non comparable ou non étayée.",
        "Ne publier « deux fois moins polluant » qu'après une comparaison documentée: même fonction, unité fonctionnelle, frontières du système, période, catégories d'impact et produit de référence. Présenter les limites et les différences méthodologiques visibles.",
        "Le Fournisseur ne diffuse aucune comparaison environnementale relative au Produit sans remettre au Client l'étude comparative, le produit de référence, les données et la méthode. Le contrôle contractuel exige, à titre de seuil interne, une ACV multicritère selon ISO 14040/14044 avec unité fonctionnelle, frontières du système et période comparables. Cette exigence contractuelle ne constitue pas une affirmation selon laquelle la proposition COM(2023) 166 serait une loi en vigueur.",
        ["Identifier le comparateur et la date de référence.", "Fournir l'ACV multicritère et ses principales hypothèses.", "Faire vérifier la formulation et la symétrie de comparaison."],
    ),
    "RULE_ISO_RECYCLABLE_PERCENTAGE": (
        "Le mot « recyclable » peut donner au consommateur l'impression qu'une filière concrète existe. Ce contrôle exige des éléments sur la collecte, l'accès, le tri et le traitement dans les territoires visés. ISO 14021:2016 est une édition retirée en 2026; la règle devra être rapprochée de l'édition 2026 et du droit français applicable à la catégorie du produit.",
        "Si la filière n'est pas démontrée, supprimer « recyclable ». Si elle existe, préciser le composant concerné, le territoire et la consigne de tri réellement accessible; éviter « partout » ou toute généralisation non prouvée.",
        "Le Fournisseur ne qualifie un Produit ou un Emballage de « recyclable » qu'après identification documentée des composants concernés, des territoires couverts, de l'accès du consommateur à la collecte, du tri effectif et d'un traitement industriel opérationnel. Il fournit les justificatifs correspondant aux catégories réglementaires françaises éventuellement applicables et actualise l'information en cas de changement de filière.",
        ["Nommer les territoires et le composant concerné.", "Documenter collecte, accès, tri et traitement.", "Vérifier les obligations spécifiques de R. 541-228 VI et la version courante d'ISO 14021."],
    ),
    "RULE_EVIDENCE_QUANTIFIED_CLAIM": (
        "Le moteur applique un seuil de publication prudent: un pourcentage de réduction carbone sans rapport source ni périmètre vérifiable est bloqué. L'absence d'ACV ISO 14044 n'est pas, à elle seule, une infraction automatique; une allégation chiffrée fausse ou trompeuse peut toutefois relever du droit de la consommation.",
        "Ne conserver le pourcentage qu'après revue du rapport source. Indiquer l'année de référence, le site, la frontière du système, l'unité fonctionnelle, la méthode et la partie du cycle de vie couverte. À défaut, retirer le chiffre.",
        "Le Fournisseur ne communique aucun pourcentage de réduction des émissions sans transmettre les données sources, le rapport identifié, l'année de référence, le site, l'unité fonctionnelle, les frontières du système et les hypothèses de calcul. À titre de seuil interne, le Client exige une ACV documentée selon ISO 14044. Toute valeur reste suspendue jusqu'à revue humaine et approbation écrite du périmètre de la revendication.",
        ["Joindre le rapport source et identifier le fichier.", "Documenter l'indicateur, la période et le périmètre.", "Faire relire la conclusion et la formulation par un humain."],
    ),
    "RULE_AGEC_COMPOSTABLE": (
        "L'article R. 541-230 du Code de l'environnement encadre strictement la mention « compostable ». Pour les emballages en plastique neufs, l'aptitude au compostage domestique (norme NF T 51-800) est obligatoire et la modalité doit être expressément indiquée (« à domicile » ou « en installation industrielle »). L'emploi isolé du terme « compostable » est prohibé.",
        "Remplacer l'allégation isolée par la mention certifiée : « compostable en compostage domestique » (si conformité NF T 51-800 établie) avec les consignes de tri adaptées, ou supprimer l'allégation.",
        "Le Fournisseur garantit que toute mention « compostable » apposée sur le Produit ou son Emballage respecte strictement les articles L. 541-9-1 et R. 541-230 du Code de l'environnement, est étayée par une certification NF T 51-800 en cours de validité pour le compostage domestique, et comporte la mention expresse de la modalité autorisée.",
        ["Vérifier la certification de compostabilité domestique NF T 51-800.", "Préciser expressément 'en compostage domestique' ou 'en installation industrielle'.", "Ne pas employer la mention 'compostable' de manière isolée sur un emballage plastique."],
    ),
    "RULE_CONSUMER_CHEMICAL_FREE": (
        "Au sens physico-chimique et selon la doctrine constante de la DGCCRF, toute matière (naturelle, végétale, minérale ou de synthèse, y compris l'eau) est chimiquement constituée. L'allégation générale « sans produit chimique » ou « zéro chimie » est trompeuse par nature au sens de l'article L. 121-2 du Code de la consommation.",
        "Supprimer « sans produits chimiques ». Mentionner uniquement l'absence ciblée d'une substance précise et controversée (ex: « sans solvants chlorés », « formulé sans parabènes ») sous réserve que cette substance ne fasse pas déjà l'objet d'une interdiction légale générale.",
        "Le Fournisseur s'interdit formellement d'utiliser les allégations « sans produit chimique », « zéro chimie », « chemical-free » ou toute formule analogue sur les emballages, notices ou supports promotionnels. Seules des allégations négatives ciblées, vérifiables et non trompeuses sur une substance spécifique pourront être autorisées après accord écrit préalable du Client.",
        ["Supprimer l'allégation globale 'sans produit chimique'.", "Remplacer par l'exclusion vérifiée et licite d'une substance chimique précise.", "S'assurer que la substance exclue n'est pas déjà obligatoirement bannie par la loi."],
    ),
    "RULE_CONSUMER_ZERO_POLLUTION": (
        "Affirmer qu'un produit manufacturé est « zéro déchet », « non polluant » ou « sans aucun impact » sur l'environnement constitue une allégation globale trompeuse au sens du Code de la consommation et de la Directive (UE) 2024/825, chaque cycle de vie générant des impacts mesurables (matières premières, transport, fin de vie).",
        "Remplacer les formules absolues (« zéro déchet », « non polluant ») par des données relatives mesurables et limitées à une étape précise (ex: « flacon rechargeable permettant d'éviter 80 % de déchet plastique à l'usage comparé au format standard »).",
        "Le Fournisseur garantit qu'aucune allégation d'impact environnemental nul (« zéro déchet », « zéro pollution », « non polluant ») n'est apposée sans démonstration scientifique absolue et complète sur l'ensemble du cycle de vie du Produit, validée préalablement par un tiers indépendant.",
        ["Bannir les promesses absolues 'zéro déchet' ou 'non polluant'.", "Décrire uniquement les actions réelles de réduction avec indicateur chiffré.", "Documenter l'impact résiduel sur l'ensemble du cycle de vie."],
    ),
    "RULE_AGEC_RECYCLED_UNQUANTIFIED": (
        "L'article R. 541-227 du Code de l'environnement interdit les mentions vagues relatives à l'incorporation de matières recyclées. La formule réglementaire obligatoire est « comporte au moins [X] % de matières recyclées ».",
        "Remplacer la mention vague par la formule réglementaire exacte : « Emballage comportant au moins [X] % de matières recyclées », adossée à une traçabilité matière certifiée.",
        "Le Fournisseur garantit que toute référence à l'incorporation de matières recyclées sur le Produit ou l'Emballage utilise strictement la formule réglementaire « comporte au moins [X] % de matières recyclées » conformément à l'article R. 541-227 du Code de l'environnement, et remet au Client les certificats de chaîne de contrôle de matière recyclée correspondants.",
        ["Calculer et certifier le pourcentage exact de matière recyclée incorporée.", "Employer la formule légale exacte 'comporte au moins [X] % de matières recyclées'.", "Fournir les certificats de chaîne de contrôle matière."],
    ),
}


def remediation_for(
    rule_id: str,
    claim_text: str,
    missing_evidence: list[str] | None = None,
    *,
    as_of_date: date | None = None,
) -> Remediation:
    buyer, rewrite, clause, actions = TEMPLATES.get(
        rule_id,
        (
            "Le moteur a identifié un point nécessitant une vérification juridique et probatoire.",
            "Suspendre la diffusion jusqu'à clarification du périmètre et des preuves.",
            "Le Fournisseur remet au Client le texte exact de l'allégation, le périmètre du produit, les pièces justificatives et les dates de validité avant toute diffusion.",
            ["Faire examiner le point par un juriste ou un responsable conformité."],
        ),
    )
    # Do not insert inferred facts or numbers into customer-facing copy.
    actions = list(actions)
    if missing_evidence:
        actions.append("Pièces ou champs manquants: " + "; ".join(missing_evidence) + ".")
    if rule_id.startswith("RULE_EU_") and as_of_date and as_of_date.isoformat() < "2026-09-27":
        actions.append("Préparer la mise en conformité avant le 27 septembre 2026; cette règle UE est date-gated.")
    if claim_text.strip():
        actions.append("Formulation détectée: « " + claim_text.strip()[:240] + " ».")
    return Remediation(
        buyer_explanation=buyer,
        recommended_rewrite=rewrite,
        supplier_contract_clause=clause,
        required_actions=actions,
    )


def generate_supplier_contract_addendum(
    supplier_name: str,
    evaluations: list[Any],
    *,
    buyer_name: str = "Le Client",
    contract_reference: str = "Contrat Cadre de Fourniture",
    effective_date: str | None = None,
) -> dict[str, Any]:
    """
    Génère un Avenant Contractuel Juridique complet (« Clause Verte & Anti-Greenwashing »)
    opposable au fournisseur, structuré en articles exécutoires (garantie d'éviction,
    prise en charge intégrale des amendes DGCCRF, pénalités forfaitaires et refonte des allégations).
    """
    today_str = effective_date or date.today().isoformat()
    doc_id = f"AVN-{date.today().strftime('%Y%m')}-{abs(hash(supplier_name)) % 10000:04d}"

    # Extraction des constats d'audits
    violations_items: list[dict[str, str]] = []
    remediation_clauses: list[str] = []
    total_fines_exposure = 0

    for rep in evaluations:
        eval_items = getattr(rep, "evaluations", [])
        exp_matrix = getattr(rep, "exposure_matrix", None)
        if exp_matrix and getattr(exp_matrix, "max_known_fixed_fine_eur", None):
            total_fines_exposure += int(exp_matrix.max_known_fixed_fine_eur)

        for ev in eval_items:
            is_viol = getattr(ev, "is_legal_violation", False)
            rule_id = getattr(ev, "rule_id", "")
            rule_title = getattr(ev, "rule_title", "")
            claim_text = getattr(ev, "trigger_text", "")
            remed = getattr(ev, "remediation", None)

            if is_viol:
                violations_items.append({
                    "rule_id": rule_id,
                    "rule_title": rule_title,
                    "claim_text": claim_text,
                    "legal_basis": getattr(ev, "legal_reference", "Code de la consommation / Code de l'environnement"),
                    "proposed_rewrite": remed.recommended_rewrite if remed else "Suppression de l'allégation",
                })
            if remed and remed.supplier_contract_clause:
                remediation_clauses.append(remed.supplier_contract_clause)

    # Déduplication des clauses
    unique_clauses = list(dict.fromkeys(remediation_clauses))

    # Rédaction intégrale en Markdown
    lines = [
        f"# AVENANT N° {doc_id} AU {contract_reference.upper()}",
        "## RELATIF À LA CONFORMITÉ RÉGLEMENTAIRE DES ALLÉGATIONS ENVIRONNEMENTALES ET À LA PRÉVENTION DU GREENWASHING",
        "",
        f"**Date d'effet :** {today_str}  ",
        f"**Entre :** **{buyer_name}**, ci-après dénommé *« Le Client »*, d'une part,  ",
        f"**Et :** **{supplier_name}**, ci-après dénommé *« Le Fournisseur »*, d'autre part.",
        "",
        "---",
        "",
        "### PRÉAMBULE",
        "Considérant les exigences impératives issues de la Loi n° 2020-105 relative à la lutte contre le gaspillage et à l'économie circulaire (AGEC), de la Loi n° 2021-1104 portant lutte contre le dérèglement climatique (Climat et Résilience), de la Directive (UE) 2024/825 (Empowering Consumers for the Green Transition) et de l'interdiction des pratiques commerciales trompeuses au sens de l'article L. 121-2 du Code de la consommation ;",
        "Considérant que le Client entend garantir une loyauté absolue des informations écologiques délivrées aux consommateurs et sécuriser ses approvisionnements contre tout risque d'amende administrative, pénale ou d'atteinte réputationnelle ;",
        "",
        "**IL A ÉTÉ CONVENU CE QUI SUIT :**",
        "",
        "### ARTICLE 1 — OBJET DE L'AVENANT",
        "Le présent Avenant a pour objet de définir les obligations impératives du Fournisseur quant à la loyauté, la vérification scientifique préalable et la légalité des allégations environnementales, mentions d'éco-conception, réductions carbone, recyclabilité et certifications apposées sur les Produits et leurs Emballages livrés au Client.",
        "",
        "### ARTICLE 2 — CONSTAT DES MANQUEMENTS ET ALLÉGATIONS PROHIBÉES",
    ]

    if violations_items:
        lines.append("Les audits réglementaires d'entrée ont révélé les non-conformités suivantes, que le Fournisseur s'engage à régulariser sans délai :")
        lines.append("")
        lines.append("| Règle Enfreinte | Allégation Litigieuse Détectée | Base Légale | Substitution Obligatoire |")
        lines.append("|---|---|---|---|")
        for v in violations_items:
            lines.append(f"| **{v['rule_title']}** | « {v['claim_text']} » | {v['legal_basis']} | {v['proposed_rewrite']} |")
        lines.append("")
    else:
        lines.append("Aucune infraction critique n'a été retenue lors des contrôles préalables. Le Fournisseur s'engage au maintien permanent de cette conformité.")
        lines.append("")

    lines.extend([
        "### ARTICLE 3 — ENGAGEMENTS D'EXCLUSION ET STIPULATIONS PARTICULIÈRES",
        "Le Fournisseur souscrit expressément aux engagements contractuels d'interdiction suivants :",
    ])

    if unique_clauses:
        for idx, clause in enumerate(unique_clauses, start=1):
            lines.append(f"**3.{idx}.** {clause}\n")
    else:
        lines.append("Le Fournisseur garantit l'exactitude des informations et le bannissement total de toute formule floue ou trompeuse.\n")

    fine_cap_str = f"{total_fines_exposure:,} €".replace(",", " ") if total_fines_exposure > 0 else "1 500 000 €"

    lines.extend([
        "### ARTICLE 4 — GARANTIE D'INDEMNISATION INTÉGRALE ET PASSAGE DU RISQUE",
        "Le Fournisseur garantit et indemnisera intégralement le Client de l'ensemble des préjudices, condamnations, astreintes et frais (notamment honoraires d'avocats, frais de rappel ou de ré-étiquetage des stocks, publication d'injonction DGCCRF) résultant d'une allégation environnementale mensongère, imprécise ou interdite apposée sur les Produits ou Emballages.",
        f"Cette garantie couvre sans restriction l'exposition financière maximale fixée par les textes applicables (pouvant atteindre {fine_cap_str} ou jusqu'à 10 % du chiffre d'affaires annuel selon l'article L. 132-2 du Code de la consommation).",
        "",
        "### ARTICLE 5 — PÉNALITÉS CONTRACTUELLES ET CLAUSE RÉSOLUTOIRE",
        "Tout manquement non rectifié dans un délai de quinze (15) jours ouvrés suivant mise en demeure par lettre recommandée ou notification électronique certifiée ouvrira droit, au choix discrétionnaire du Client :",
        "1. À l'application d'une pénalité forfaitaire de 15 000 € par référence non conforme ;",
        "2. Au refus des livraisons et au retour des marchandises aux frais exclusifs du Fournisseur ;",
        "3. À la résiliation immédiate de plein droit du Contrat Cadre sans indemnité au bénéfice du Fournisseur.",
        "",
        "### ARTICLE 6 — ENTRÉE EN VIGUEUR ET DROIT APPLICABLE",
        "Le présent Avenant prend effet immédiatement à compter de sa signature par les deux parties et prévaut sur toute condition générale d'achat ou de vente contraire.",
        "",
        "Fait en deux (2) exemplaires originaux à Nantes / Paris.",
        "",
        f"**Pour le Client ({buyer_name}) :** _______________________  ",
        f"**Pour le Fournisseur ({supplier_name}) :** _______________________  ",
    ])

    full_markdown = "\n".join(lines)

    return {
        "addendum_id": doc_id,
        "effective_date": today_str,
        "buyer_name": buyer_name,
        "supplier_name": supplier_name,
        "violations_count": len(violations_items),
        "total_exposure_eur": total_fines_exposure,
        "articles_count": 6,
        "markdown_content": full_markdown,
    }
