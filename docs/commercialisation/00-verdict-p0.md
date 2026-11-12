# VERDICT P0 — NO-GO commercialisation en production

**Date : 11 novembre 2026.** État du dépôt évalué : branche `arena/c5488624-vericlaim`, base `b289652a383bfe385c531ff4fb25a5f1535f74c7` de `main`, avec les corrections P0 de cette branche. Décision recommandée : **pilote encadré uniquement**, sur environnement validé, données autorisées et revues humaines, après levée des prérequis ci-dessous. Les tests locaux ne constituent pas une validation de production, juridique ou de sécurité indépendante.

## Ce qui a été vérifié et corrigé

- Les exports de rapports d'analyses persistantes refusent avec 409 en l'absence de résultats réglementaires persistés ; ils ne fabriquent plus un rapport de conformité à partir du texte. Le parcours d'évaluation éphémère est distinct.
- Le pilote est un inventaire des détections, décisions et liens déclarés, pas un taux de conformité ni une appréciation de suffisance de preuve. La priorité fournisseur est une heuristique de liens manquants, rattachée aux relations d'analyse persistées ; les versions historiques comptent séparément.
- La gouvernance affiche une empreinte du contenu non signée, des dates et validations inconnues au lieu de valeurs inventées. Les indicateurs d'exploitation non mesurés sont `null` ou `not_probed` ; l'absence d'alertes n'est pas une preuve de santé.
- Les empreintes d'audit sont présentées comme une vérification de cohérence interne sans signature ni ancrage externe ; le champ historique `merkle_digest` n'est pas une racine de Merkle. Les CSV neutralisent les cellules interprétables comme formules. Le conteneur backend est configuré pour tourner en UID 10001 ; les images acceptées sont limitées aux formats pris en charge.
- Transactions corrigées sur les opérations enterprise ; garde des endpoints OIDC contre les cibles non sûres ; dépendances verrouillées par hashes, audit de vulnérabilités et contrôle Ruff en CI.

## Preuves locales

| Contrôle | Résultat | Limites |
|---|---|---|
| Pytest complet avec PostgreSQL 16.2 local | 264 passés, 0 ignoré | Inclut les suites PostgreSQL ; ne vérifie pas l'infrastructure réelle. |
| Environnement créé depuis le lock hashé | 237 passés, 27 ignorés | Sans URL PostgreSQL ; pas la même couverture que la ligne précédente. |
| Ruff `E4,E7,E9,F,B` | OK | Un ensemble élargi de règles reste à traiter. |
| Frontend `npm run typecheck` et `npm run build` | OK | Pas de test navigateur E2E en production. |
| `pip-audit` sur lock / `npm audit --audit-level=moderate` | 0 vulnérabilité connue au moment de l'exécution | Dépend des bases de vulnérabilités et de la date. |
| `pip check` dans les deux environnements | OK | Ne garantit pas la compatibilité métier. |

Exécution reproductible et conditions : [validation](07-validation-et-limites.md). Les journaux détaillés locaux de cette session sont sous `/home/user/p0-work/`, hors dépôt et non garantis persistants après la session.

## Bloquants avant vente / mise en production

1. Revue juridique indépendante et publication contrôlée du corpus, des conditions contractuelles et du traitement des données ; aucune certification ou couverture réglementaire exhaustive n'est établie.
2. Déploiement de bout en bout avec vrais PostgreSQL/RLS, stockage S3, antivirus, OCR, SSO et deux workers ; tests de panne, isolation inter-tenant, charge, observabilité et reprise sur sauvegarde. Docker et ces services externes n'étaient pas disponibles dans le sandbox.
3. Décider et implémenter les politiques de rétention, suppression, demandes RGPD, conservation des sauvegardes, sous-traitants et région d'hébergement ; les valeurs actuelles sont non configurées.
4. Aucun module de facturation opérationnel ni parcours d'achat démontré ; clés API modélisées mais non démontrées comme moyen d'authentification. Ne pas commercialiser ces fonctions.
5. Vérifier la CI sur cette branche et le déploiement réel. Vercel : lecture des projets refusée par les permissions du compte ; aucun projet ni déploiement n'a été vérifié ou modifié. Les migrations, secrets et workers ne sont pas validés sur une cible hébergée.
6. Revue des modifications et compatibilité des clients API, notamment champs nullables, statuts d'inventaire et statut d'instantané d'audit `CHAIN_CONSISTENT`.

## Branches et intégration

`main` était à `b289652` au début de l'audit. La PR #7 est ouverte, issue d'une base ancienne et présente 15 conflits dans une simulation `git merge-tree` ; elle n'a pas été fusionnée. Son succès CI ancien ne valide ni cette branche ni son intégration actuelle. Les éléments intéressants (facturation, confidentialité, rapports et documentation juridique) nécessitent un portage sélectif, une revue et des tests séparés ; ne pas les considérer livrés ici.

## Livrables

[Positionnement](01-positionnement.md) · [Capacités et limites](02-capacites-et-limites.md) · [Pilote](03-pilote.md) · [Sécurité et opérations](04-securite-et-operations.md) · [Données et juridique](05-donnees-et-juridique.md) · [Offre et facturation](06-offre-et-facturation.md) · [Validation](07-validation-et-limites.md) · [Plan de levée des risques](08-plan-de-lancement.md).
