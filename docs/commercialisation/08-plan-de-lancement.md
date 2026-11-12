# Plan de levée des risques

| Priorité | Action et responsable à désigner | Critère de sortie |
|---|---|---|
| P0 | Produit + juridique : revoir règles et langage, définir l'usage et documents contractuels | Corpus et promesses approuvés, sources et dates traçables, revue humaine documentée. |
| P0 | Plateforme : déployer migrations, RLS, stockage, scanner, OCR, SSO et workers | Parcours réel, panne et isolation tenant testés, résultats conservés. |
| P0 | Sécurité + opérations : secrets, surveillance, réponse incidents, sauvegarde | Revue indépendante, alertes opérationnelles et restauration complète testée. |
| P0 | Confidentialité : traitement, durées, suppression, transferts | DPA et procédures approuvés et techniquement vérifiés, y compris sauvegardes. |
| P0 | Équipe intégration : vérifier la CI de branche et compatibilité clients | Jobs verts sur commit proposé et gestion explicite des changements d'API. |
| P1 | Produit + finance : définir offre et implémenter facturation | Parcours de paiement et exceptions testés, prix et conditions approuvés. |
| P1 | Ingénierie : sélection de travaux de PR #7 | Portage séparé, revue et tests sur base actuelle, sans fusion aveugle. |
| P1 | Qualité : E2E navigateur, charge, règles Ruff complémentaires | Couverture et seuils convenus, écarts résiduels suivis. |

La décision finale de lancement appartient aux responsables produit, juridique, sécurité et exploitation ; les résultats locaux ne la remplacent pas. En attendant, ne proposer qu'un pilote expressément encadré et conditionné aux prérequis de [03](03-pilote.md).
