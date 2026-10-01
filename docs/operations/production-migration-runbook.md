# Runbook — migration PostgreSQL de production

Ce runbook couvre la migration de la base Neon européenne `vericlaim`. Il est volontairement manuel et repose sur une approbation GitHub Environment `production`.

> Ne mettez jamais un DSN, une clé API ou un mot de passe dans ce document, dans une issue, dans un commit ou dans les logs d’un workflow.

## Responsabilités des identités PostgreSQL

| Identité | Usage autorisé | Usage interdit |
|---|---|---|
| `neondb_owner` | création initiale de base, rôles, récupération exceptionnelle | API, worker, migration régulière, GitHub Actions |
| `vericlaim_migrator` | Alembic et grants post-migration | API, worker, navigateur |
| `vericlaim_app` | API et workers | DDL, Alembic, rôle administrateur PostgreSQL |
| `vericlaim_backup` | **sauvegarde logique** (`pg_dump`) et lecture seule pour contrôle | API, worker, écriture, navigation |

### Pourquoi un quatrième rôle, et pourquoi il porte `BYPASSRLS`

Mesuré le 30/09/2026 sur PostgreSQL 17 avec le schéma réel : deux pièges se referment sur toute
sauvegarde faite avec les rôles existants.

1. **`pg_dump` avec `vericlaim_migrator` échoue.** Les politiques RLS sont `FORCE`, donc le
   propriétaire y est soumis :
   `pg_dump: error: query failed: ERROR: query would be affected by row-level security policy for table "analyses"`.
2. **`pg_dump --enable-row-security` réussit et sauvegarde une base amputée.** La sauvegarde
   passe, la restauration passe, et la base restaurée ne contient **aucun** document, version,
   segment ni événement d'audit. Les tables sont là, les organisations aussi : un contrôle
   superficiel conclut au succès.

Conséquence opérationnelle : sauvegarder ce schéma exige un rôle porteur de `BYPASSRLS`. Ce
privilège est réservé au rôle de sauvegarde — ni `vericlaim_app`, ni `vericlaim_migrator` ne le
portent — et ce rôle ne sert qu'à lire. La commande exacte, les droits requis (tables **et**
séquences) et l'exercice reproductible sont dans [`restore-runbook.md`](restore-runbook.md).

## Préconditions uniques

- le mot de passe `neondb_owner` a été rotaté après toute exposition ;
- la base cible est `vericlaim` dans le projet Neon européen ;
- les rôles `vericlaim_migrator` et `vericlaim_app` sont non-superuser et `NOBYPASSRLS` ;
- le rôle propriétaire ne figure dans aucun secret GitHub ou Vercel ;
- la branche `main` est protégée ;
- la CI GitHub `Intégration continue` a réussi sur la pull request avant son merge ;
- l’environnement GitHub `production` exige une validation humaine ;
- les environnements GitHub `staging` et `production` ont chacun leurs propres secrets :

  ```text
  DATABASE_URL_MIGRATOR
  DATABASE_URL_APP
  ```

Dans chaque environnement, les deux URLs visent `/vericlaim`, utilisent TLS et des mots de passe distincts. Les credentials de `staging` ne sont jamais réemployés en `production`. `DATABASE_URL_MIGRATOR` utilise le schéma SQLAlchemy `postgresql+psycopg://`; `DATABASE_URL_APP` fait de même.

## Activation initiale des identités

Depuis un poste d’administration sécurisé, avec le rôle propriétaire et sans enregistrer de mot de passe dans l’historique shell, générer deux mots de passe aléatoires distincts puis exécuter :

```text
infra/neon/01_bootstrap_roles.sql
```

Le script vérifie que la base effectivement connectée est `vericlaim`, refuse une base contenant des tables métier et refuse tout rôle existant avec `SUPERUSER` ou `BYPASSRLS`. Il accepte les rôles préprovisionnés seulement s’ils sont encore `NOLOGIN` : il définit alors leurs premiers mots de passe et active `LOGIN`. Dès qu’un rôle peut se connecter, une nouvelle exécution échoue plutôt que de risquer une rotation de credential non planifiée.

Après chaque activation, stocker les deux URLs uniquement dans l’environnement GitHub correspondant (`staging` ou `production`). Ne réemployez jamais une URL entre environnements et ne les envoyez pas dans la conversation.

## Validation staging obligatoire

1. Créer une branche Neon `staging` isolée depuis la baseline `production`, avec un compute lecture-écriture dédié.
2. Activer sur cette branche des credentials `vericlaim_migrator` et `vericlaim_app` distincts de ceux de production.
3. Configurer les deux URLs uniquement dans l’environnement GitHub `staging`.
4. Ouvrir **GitHub → Actions → Migration de base — staging**.
5. Choisir la branche `main`, puis `APPLY` dans le champ de confirmation.
6. Vérifier le succès de toutes les étapes :

   ```text
   alembic current
   alembic upgrade head
   post-migration runtime grants
   runtime RLS and least-privilege verification
   ```

7. Consigner le SHA Git, la révision Alembic et les contrôles RLS/RBAC/audit avant toute promotion.

## Déclenchement de migration production

Uniquement après validation humaine explicite de staging :

1. Vérifier que la pull request contenant les migrations a été revue et fusionnée dans `main`.
2. Ouvrir **GitHub → Actions → Migration de base — production**.
3. Choisir la branche `main`.
4. Choisir `APPLY` dans le champ de confirmation.
5. Valider l’exécution de l’environnement `production` lorsque GitHub la demande.
6. Vérifier le succès de toutes les étapes :

   ```text
   alembic current
   alembic upgrade head
   post-migration runtime grants
   runtime RLS and least-privilege verification
   ```

Le workflow ne possède que `contents: read`, n’écoute aucun événement `push` ou `pull_request`, ne s’exécute que depuis `main`, et sérialise les migrations de production.

## Résultats attendus à cette étape

```text
Alembic revision : tête lue dans backend/alembic/versions/ (f63c9a1b7d20 au 30/09/2026)

La révision n’est plus recopiée à la main dans ce document : `scripts/ci/check_migration_discipline.py` la lit dans la chaîne de migrations et **refuse** tout document qui cite une tête périmée. C’est le défaut qui laissait la CI rouge en permanence avant C24 : une valeur recopiée dans trois fichiers finit par être fausse dans deux.
Runtime role     : vericlaim_app
Runtime bypass   : false
RLS              : activé + forcé sur les tables tenant-scoped
Audit            : UPDATE/DELETE refusés au rôle runtime
```

## Échec, annulation et reprise

- Ne pas relancer un workflow échoué sans lire son premier message d’erreur.
- Ne pas utiliser `neondb_owner` pour contourner l’erreur.
- Ne pas lancer `alembic downgrade` en production sans plan de restauration validé : les migrations peuvent contenir des opérations destructrices ou des données de référence.
- En cas de migration partiellement appliquée, geler les déploiements applicatifs, documenter la révision réellement présente, puis restaurer/réparer via une procédure revue.
- Avant toute donnée client, exécuter et consigner un exercice de restauration Neon/PITR sur un environnement isolé. L’exercice est outillé et tourne à chaque pull request sur PostgreSQL (`scripts/ci/rehearse_restore.py`) ; sur Neon, il reste à exécuter avec les identifiants réels et à consigner — c’est la seule partie de cette phrase qu’aucune automatisation ne peut faire à votre place.
