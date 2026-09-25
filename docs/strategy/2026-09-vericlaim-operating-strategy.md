# Stratégie opératoire VeriClaim — 2026

> **Statut : stratégie fondatrice active**
>
> VeriClaim est un SaaS B2B RegTech de pré-audit, de gestion documentaire probatoire et de réduction du risque sur les allégations environnementales fournisseurs. Il n’est ni un cabinet d’avocats, ni une autorité, ni un organisme de certification.

## 1. Ambition

Devenir la plateforme de référence pour les équipes achats, conformité, qualité et durabilité qui doivent démontrer, à tout moment, que leurs allégations fournisseurs reposent sur des sources, des contrôles et des décisions humaines traçables.

La promesse n’est pas « une IA qui déclare la conformité ». La promesse est :

> **Chaque affirmation importante peut être retrouvée, citée, reliée à une source, revue par une personne autorisée et expliquée dans le temps.**

## 2. Décisions fondatrices

| Sujet | Décision |
|---|---|
| Positionnement | Système d’exploitation probatoire pour le pré-audit B2B, pas moteur d’avis juridique. |
| Marché initial | Entreprises européennes exposées aux chaînes fournisseurs, aux déclarations environnementales et aux exigences de traçabilité. |
| Données | Résidence européenne par défaut pour production ; séparation stricte développement, staging et production. |
| Base de données | Neon PostgreSQL dédié à VeriClaim, projet propre, runtime `NOBYPASSRLS`, migrations séparées. |
| Identité | Contrat OIDC générique, fournisseur-neutre, compatible SSO entreprise. |
| Clerk | Aucune dépendance architecturale. |
| Neon Auth | Différé comme identité primaire de production ; admissible à des expérimentations isolées seulement. |
| Autorisation | Organisations, memberships, RBAC, CSRF, sessions et audit restent sous autorité VeriClaim. |
| IA | Assistance déterministe et explicable aujourd’hui ; aucune conclusion réglementaire importante ne repose sur un LLM seul. |
| Preuve | Hash, provenance, version, source et validation humaine avant automatisation métier. |

## 3. Principes de décision

L’ordre de priorité est immuable :

```text
Fiable → Explicable → Traçable → Sécurisé → Simple → Rapide
```

Toute nouvelle fonctionnalité doit améliorer au moins un de ces axes sans dégrader les précédents.

### Ce qui est interdit

- inventer une source, un certificat, une preuve, un score ou une conclusion juridique ;
- associer automatiquement une preuve à un claim avec effet métier ;
- exposer des données inter-tenant ;
- utiliser un rôle PostgreSQL `BYPASSRLS` au runtime ;
- publier des secrets, DSN, tokens ou exports de données dans Git, le chat ou les logs ;
- faire passer un prototype éphémère pour une preuve persistante ;
- présenter une détection ou suggestion comme une validation humaine.

### Ce qui est différé

- LLM/RAG appliqué aux décisions réglementaires ;
- scores juridiques ou recommandations automatiques ;
- enrichissement fournisseur externe non vérifié ;
- création automatique de liens claim → preuve ;
- rapport PDF à valeur prétendument probatoire ;
- authentification Neon Auth comme frontière primaire de production.

## 4. Architecture de production cible

```text
Utilisateurs entreprise
        │
        ▼
IdP OIDC compatible entreprise
        │ Authorization Code + PKCE
        ▼
app.vericlaim.<domaine>  ─── Next.js / BFF
        │ /api/*
        ▼
FastAPI privé + workers PostgreSQL
        │
        ├── PostgreSQL Neon EU
        │     ├── vericlaim_migrator : migrations uniquement
        │     └── vericlaim_app      : NOBYPASSRLS, runtime uniquement
        │
        └── Stockage objet privé EU + antivirus + OCR isolé
```

### Invariants techniques

- navigateur sur un origin public unique ;
- API et workers sans port public inutile ;
- cookies sécurisés, CSRF et CORS explicites ;
- RLS activé et forcé sur les tables tenant ;
- transactions courtes et contexte organisationnel transactionnel ;
- migrations Alembic comme seule source de vérité de schéma ;
- sauvegardes, PITR et exercice de restauration avant go-live ;
- journal d’audit append-only et hash-chaîné.

## 5. Stratégie de données

### Résidence et séparation

| Environnement | Données | Région cible | Finalité |
|---|---|---|---|
| development | synthétiques uniquement | EU | développement local/intégration |
| staging | synthétiques ou anonymisées | EU | recette et migration rehearsal |
| production | données client | EU | service contractuel |

La base legacy existante ne fait pas partie de l’architecture cible. Elle n’est ni migrée, ni stampée, ni utilisée pour les tests de production.

### Modèle de confiance

- le fournisseur d’identité authentifie une personne ;
- VeriClaim résout son utilisateur local ;
- VeriClaim résout le membership et l’organisation active ;
- PostgreSQL limite les lignes via RLS ;
- chaque mutation ajoute un événement d’audit ;
- une validation humaine est append-only ;
- les analyses et leurs résultats ne sont jamais écrasés.

## 6. Stratégie produit

### Wedge initial

Le premier produit complet est le cycle :

```text
Catalogue fournisseur/produit
→ pièce sécurisée
→ extraction durable
→ claims citables
→ contexte probatoire
→ revue humaine
→ demande de pièce
→ audit exploitable
```

Cette chaîne est plus défendable qu’un score opaque ou une promesse de conformité automatique.

### Étapes produit

| Horizon | Décision |
|---|---|
| Maintenant | Stabiliser C1–C6.1, landing zone EU, qualité et sécurité. |
| Après validation C6.1 | C6.2 : registre probatoire persistant, certificats déclarés, liens manuels claim → preuve. |
| Après validation C6.2 | C6.3 : validations append-only, suggestions déterministes en lecture, demandes de pièces. |
| Ensuite | Référentiel réglementaire versionné, workflows entreprises, intégrations achats/ERP et reporting contrôlé. |

Aucun passage au chantier suivant n’est automatique : chaque étape possède ses critères de sécurité, de données, de tests et d’UX.

## 7. Stratégie identité entreprise

Le choix du fournisseur OIDC de production sera soumis à une revue documentée :

- OIDC standard avec JWKS, PKCE, nonce et rotation de clés ;
- SAML et SCIM sur trajectoire entreprise ;
- MFA et politiques de récupération ;
- DPA, SLA, notification d’incident et résidence des données ;
- environnements de preview/test ;
- réversibilité et export ;
- coût prévisible à l’échelle B2B.

Le fournisseur n’est jamais la source de vérité des permissions métier. Le backend reste compatible avec un changement futur de fournisseur.

## 8. Qualité et sécurité comme produit

Avant toute ouverture client :

1. revue de menace documentée ;
2. tests RLS inter-tenant positifs et négatifs ;
3. tests RBAC et CSRF ;
4. validation de migration sur staging vierge ;
5. test de restauration documenté ;
6. scan de secrets dans CI ;
7. SBOM et mises à jour de dépendances critiques ;
8. politique de journalisation sans JWT, cookie, DSN ou PII excessive ;
9. revue manuelle des flux documentaires et de la suppression/archivage ;
10. revue d’accessibilité et de parcours utilisateur.

## 9. Indicateurs de pilotage

Les métriques ne doivent pas simuler une opinion juridique. Les indicateurs prioritaires sont :

| Domaine | Indicateur |
|---|---|
| Traçabilité | taux de claims avec passage, page, offsets et hash source |
| Preuve | taux de claims reliés à une preuve déclarée et revue humainement |
| Productivité | délai médian document → claim → revue |
| Qualité | taux de corrections humaines après suggestion déterministe |
| Sécurité | taux de tests RLS/RBAC/CSRF verts et incidents d’accès |
| Exploitabilité | succès des restaurations, des migrations et des jobs worker |
| Adoption | fournisseurs/produits actifs, pièces complètes, organisations actives |

## 10. Cadence d’exécution

### Chaque changement

- ticket avec objectif, risque, invariant de sécurité et test attendu ;
- revue de code ;
- tests ciblés et suite appropriée ;
- migration examinée séparément ;
- documentation synchronisée ;
- aucun secret dans le diff.

### Chaque livraison

- démonstration sur données synthétiques ;
- contrôle des exclusions de périmètre ;
- validation humaine avant activation ;
- décision explicite de passage au chantier suivant.

## 11. Prochaines actions exécutives

1. créer le projet Neon européen vierge ;
2. provisionner roles, branches et migrations sur ce projet uniquement ;
3. valider C6.1 fonctionnellement et en sécurité ;
4. effectuer la revue fournisseur OIDC ;
5. définir le déploiement single-origin ;
6. lancer C6.2 seulement après les gates précédents ;
7. recruter les premiers design partners B2B plutôt que chercher immédiatement la couverture fonctionnelle maximale.

## 12. Délégation et gates

L’équipe technique peut décider librement du code, de la dette technique, des tests, de l’observabilité et de la structure interne tant que les invariants sont respectés.

Les décisions suivantes nécessitent une approbation explicite :

- dépense contractuelle ou fournisseur payant ;
- création/suppression de ressources de production ;
- migration irréversible ou traitement de données réelles ;
- politique d’identité, MFA et récupération de compte ;
- changement de résidence des données ;
- publication d’une promesse réglementaire ou commerciale engageante.
