# Chantier 7 — Gouvernance du Référentiel Réglementaire (Rule Book Engine)

## Objet et Principes Directeurs

Le **Chantier 7** établit la gouvernance, l'explicabilité et la traçabilité intégrale du référentiel juridique de VeriClaim.

Conformément à la directive européenne 2024/825 (EmpCo), à la loi AGEC, aux normes ISO et aux exigences de conformité du futur *AI Act* :
1. **Zéro Hallucination & Citations Exactes** : Chaque règle s'appuie sur des citations littérales d'articles de loi et des liens hypertextes officiels vérifiables (Légifrance, EUR-Lex, ISO).
2. **Distinction Stricte de la Force Juridique** :
   - `BINDING_FR` : Droit français en vigueur opposable (ex. Art. L. 541-9-1 & R. 541-230).
   - `EU_DIRECTIVE_DATE_GATED` : Directive européenne avec date d'application fixée (ex. 27 septembre 2026 pour EmpCo 2024/825) et condition de vérification de la transposition nationale.
   - `PROPOSAL_ONLY` : Proposition de directive non adoptée (ex. Green Claims COM(2023) 166), signalée explicitement comme garde-fou consultatif (*advisory evidence gate*).
   - `VOLUNTARY_STANDARD` : Norme volontaire d'auto-déclaration (ISO 14021:2016 / ISO 14021:2026).
   - `INTERNAL_EVIDENCE_CONTROL` : Contrôle probatoire interne de rigueur méthodologique (ISO 14044).
3. **Immuabilité & Empreinte Cryptographique** : Le Rule Book est identifié par une version horodatée et une empreinte SHA-256 calculée sur l'ensemble des règles canoniques.
4. **Différentiel Structuré & Traçabilité des Évolutions** : Un moteur de changelog suit les ajouts, modifications de seuils, ajustements de plafonds d'amendes et évolutions des textes réglementaires.

---

## Modèle de Données & Schémas

### 1. `RegulatoryRule` & `OfficialCitation`
- `rule_id` : Identifiant immuable de la règle (ex. `RULE_AGEC_BIODEGRADABLE`).
- `jurisdiction` : `FR` | `EU` | `INTERNATIONAL`.
- `legal_status` : `in_force` | `pending_transposition` | `proposal` | `superseded` | `repealed`.
- `legal_force` : Force juridique normée.
- `severity` : `CRITICAL` | `HIGH` | `MEDIUM` | `LOW`.
- `official_citations` : Liste de citations contenant l'article, le titre officiel de la loi/décret/directive, l'extrait textuel authentique et l'URL source vérifiée.
- `safe_harbors` : Conditions précises permettant d'établir une conformité ou une exemption légale.
- `sanction` : Plafonds d'amendes administratives (personnes physiques et morales), autorité compétente et base légale.
- `incomplete_coverage_warning` : Avertissement de prudence juridique en cas de texte en cours de transposition ou de statut consultatif.

### 2. Gouvernance & Revue Juridique (`RuleGovernanceReview`)
- `review_status` : `approved_legal` | `under_review` | `draft` | `deprecated`.
- `confidence_level` : `high` | `medium` | `low`.
- `reviewed_by` / `reviewed_at` : Traçabilité du sign-off par un juriste ou expert conformité.
- `legal_notes` : Commentaire d'expertise et analyse contextuelle.

---

## API REST du Référentiel Réglementaire

| Méthode | Route | Permission | Description |
|---|---|---|---|
| `GET` | `/api/v1/regulatory/rulebook` | `rules:read` | Vue d'ensemble, version, empreinte SHA-256, statistiques de juridiction et avertissements |
| `GET` | `/api/v1/regulatory/rules` | `rules:read` | Liste filtrable par juridiction, statut, sévérité, type d'allégation et recherche textuelle |
| `GET` | `/api/v1/regulatory/rules/{rule_id}` | `rules:read` | Fiche complète avec citations Légifrance/EUR-Lex, safe harbors et sanctions |
| `GET` | `/api/v1/regulatory/changelog` | `rules:read` | Historique des versions et différentiels détaillés |
| `POST` | `/api/v1/regulatory/rules/{rule_id}/review` | `rules:manage` (CSRF) | Enregistrement d'un visa de revue juridique sur une règle |

---

## Interface Utilisateur & Dashboard

1. **`RegulatoryRulebookPanel.tsx`** :
   - Cartes KPI synthétiques (Total règles, répartition 🇫🇷 France / 🇪🇺 UE / 🌐 International, points de vigilance).
   - Barre de recherche et filtres par juridiction, niveau de sévérité et statut d'application.
   - Tableau interactif des règles avec badges de force juridique, drapeaux et avertissements.
2. **`RuleDetailModal.tsx`** :
   - Fiche d'audit détaillée affichant les citations textuelles législatives avec liens directs, les régimes d'exemption (*safe harbors*), les sanctions administratives maximales et l'historique de gouvernance.
   - Formulaire d'audit et de validation pour les juristes autorisés.
3. **`RuleDiffModal.tsx`** :
   - Visualiseur de *changelog* retraçant l'évolution des règles entre éditions (Édition Septembre 2026, Juillet 2026, Janvier 2026).
