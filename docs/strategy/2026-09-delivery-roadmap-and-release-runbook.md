# VeriClaim — feuille de route de livraison et runbook de release

- **Statut :** plan opérationnel actif
- **Périmètre :** terminer VeriClaim V1 en vue d’un pilote B2B contrôlé
- **Principe directeur :** fiable > explicable > traçable > sécurisé > simple > rapide

Ce document complète la stratégie fondatrice. Il décrit les chantiers restants et les procédures de migration et de publication du code.

## 1. Définition de terminé pour VeriClaim V1

VeriClaim V1 est prêt pour un pilote lorsque les conditions suivantes sont satisfaites :

```text
- isolation stricte des organisations au niveau applicatif et PostgreSQL ;
- OIDC SSO générique, sessions, CSRF, RBAC et audit sous autorité VeriClaim ;
- documents et preuves persistants, versionnés, hashés et traçables ;
- validation humaine avant tout effet métier important ;
- référentiel réglementaire versionné, sourcé et explicitement limité ;
- staging et production en Europe, sauvegardes et restauration testées ;
- pipeline de migrations contrôlé, reproductible et révisable ;
- aucune promesse de certification, verdict juridique ou avis juridique automatique.
```

Les fonctionnalités suivantes ne sont **pas** des objectifs V1 sans cadrage dédié : certificat officiel, portail public de certification, verdict juridique automatisé, scraping libre du web, surveillance autonome ou connecteurs tiers non vérifiés.

## 2. Chantiers restants

| Ordre | Chantier | But | Critère de sortie |
|---:|---|---|---|
| P0 | Rétablissement de l’intégrité Git et des secrets | Traiter les secrets exposés et la divergence de `main` | `main` contrôlé, branche divergente préservée en quarantaine, secrets rotatés |
| P1 | Plateforme Neon et release engineering | Base EU, rôles distincts, migrations, RLS, staging, restauration | Schéma C6.1 validé en EU avec runtime `NOBYPASSRLS` |
| C6.2 | Registre probatoire | Preuves, certificats déclarés, liens aux claims, provenance et versioning | Toute preuve est persistante et liée sans invention de source |
| C6.3 | Revue et demandes de pièces | Validation humaine, tâches, demandes fournisseurs et audit | Aucun effet métier sans décision humaine traçable |
| C7 | Gouvernance réglementaire | Sources officielles versionnées, dates d’effet, citations et revue | Toute règle importante est référencée, versionnée et revue |
| C8 | Pack pilote B2B | UX de suivi, reporting interne pré-audit et onboarding pilote | Une organisation pilote peut opérer un cycle complet contrôlé |
| C9 | Industrialisation entreprise | Exploitation, SSO avancé, observabilité, RGPD et échelle | Préparation à plusieurs organisations clientes sous SLA défini |

### P0 — Intégrité Git et secrets

Actions :

```text
1. Révoquer les tokens GitHub collés dans la conversation.
2. Rotater/révoquer les credentials Neon et Clerk exposés dans la branche distante rejetée.
3. Geler tout déploiement issu de cette branche distante.
4. Préserver le commit divergent dans une branche quarantine/.
5. Restaurer main vers la lignée contrôlée contenant C6.1 et le workflow de migration.
6. Configurer la protection de main : PR obligatoire, CI obligatoire, force-push interdit.
```

Sortie :

```text
- aucun secret actif dans Git ou un manifest de déploiement ;
- l’historique divergent reste consultable mais non déployable ;
- main redevient la seule baseline de livraison.
```

### P1 — Landing zone Neon, migrations et staging

Actions :

```text
1. Finaliser la base Neon européenne vericlaim.
2. Activer les rôles vericlaim_migrator et vericlaim_app avec secrets distincts.
3. Configurer les secrets de l’environnement GitHub production.
4. Publier le workflow manuel de migration.
5. Migrer staging, puis exécuter les contrôles RLS/RBAC/audit.
6. Créer un point de restauration et effectuer un exercice de restauration.
7. Migrer production après validation humaine.
8. Créer une branche Neon staging isolée à partir de la baseline validée.
```

Sortie :

```text
- schéma Alembic au head d3c8a6e1b409 ;
- runtime vericlaim_app non-superuser et sans BYPASSRLS ;
- RLS activée et forcée sur les tables tenant-scoped ;
- audit append-only testé ;
- staging et production séparés ;
- restauration documentée et répétée.
```

### C6.2 — Registre probatoire persistant

Livrables :

```text
- registre de preuves persistantes ;
- certificat déclaré comme assertion, jamais comme fait vérifié par défaut ;
- hashes de contenu, provenance, source, date et version ;
- liens preuve ↔ document ↔ produit/fournisseur ↔ claim ;
- état de couverture probatoire ;
- historique non écrasable.
```

Gates :

```text
- aucune preuve ou certification inventée ;
- aucune conclusion réglementaire définitive ;
- toutes les données isolées par organisation ;
- tests RLS, RBAC, idempotence et audit requis.
```

### C6.3 — Validation humaine et demandes de pièces

Livrables :

```text
- file de revue humaine ;
- validation, rejet, commentaire et justification ;
- demande de complément fournisseur ;
- échéance, relance et clôture ;
- journal append-only des décisions ;
- droits distincts lecture, gestion, validation et administration.
```

Gate :

```text
Aucun statut de preuve, risque ou décision de publication ne peut changer sans acteur humain autorisé.
```

### C7 — Gouvernance du référentiel réglementaire

Livrables :

```text
- sources officielles et URLs de provenance ;
- juridiction, date d’effet, statut adopté/proposition/abrogé ;
- citations exactes et versions ;
- workflow de revue conformité/juridique ;
- différentiel entre versions ;
- signalement clair de couverture incomplète ou de faible confiance.
```

### C8 — Pack pilote B2B

Livrables :

```text
- tableaux de bord fournisseurs, produits, claims et preuves ;
- rapport interne explicitement marqué pré-audit ;
- indicateurs de couverture et de revue ;
- import/onboarding contrôlé ;
- documentation utilisateur et support pilote ;
- politique de rétention et d’export des données pilote.
```

### C9 — Industrialisation entreprise

Livrables :

```text
- SSO entreprise et éventuel SCIM ;
- observabilité, alertes, métriques et traces ;
- API partenaire gouvernée et quotas ;
- tests de charge et plan de capacité ;
- gestion de rétention, suppression, e-discovery et incidents ;
- documentation RGPD, sécurité et exploitation.
```

## 3. Procédure de migration de base de données

### 3.1 Séparation stricte des identités

| Rôle | Utilisation autorisée | Interdictions |
|---|---|---|
| `neondb_owner` | Bootstrap exceptionnel, création initiale de base/rôles, récupération approuvée | Runtime API, worker, CI régulière, migrations régulières |
| `vericlaim_migrator` | Alembic et grants post-migration | API, worker, navigateur |
| `vericlaim_app` | API et workers | DDL, Alembic, gestion de rôles, contournement RLS |

Le rôle runtime doit être :

```text
NOSUPERUSER
NOCREATEDB
NOCREATEROLE
NOBYPASSRLS
```

### 3.2 Cycle normal d’une migration

```text
1. Une migration est créée dans une branche feature.
2. Les tests applicatifs, migration SQLite et SQL PostgreSQL offline sont exécutés.
3. La pull request est revue ; les conséquences sur RLS, audit et rollback sont documentées.
4. Le changement est mergé dans main après CI.
5. Le workflow manuel applique la migration sur staging avec vericlaim_migrator.
6. Le rôle vericlaim_app exécute les contrôles de lecture, RLS et privilèges.
7. Une approbation humaine autorise la migration production.
8. Le workflow production exécute alembic upgrade head avec vericlaim_migrator.
9. Les grants post-migration sont appliqués.
10. Les contrôles runtime, santé applicative et observabilité sont validés.
```

### 3.3 Contrôles obligatoires après migration

```text
- révision Alembic = head attendu ;
- vericlaim_app sans SUPERUSER, CREATEDB, CREATEROLE ou BYPASSRLS ;
- RLS activée et forcée sur les tables tenant-scoped ;
- policy présente sur chaque table tenant-scoped ;
- aucune donnée tenant visible sans contexte organisationnel ;
- audit_events et audit_records non modifiables/supprimables par le runtime ;
- API incapable de démarrer si la révision de schéma est incorrecte.
```

### 3.4 Interdictions de migration

```text
- pas de Base.metadata.create_all en staging ou production ;
- pas de migration au démarrage de l’API ;
- pas de fallback silencieux SQLite ;
- pas de rôle propriétaire dans DATABASE_URL runtime ;
- pas de downgrade production comme stratégie de récupération ;
- pas de migration depuis la chaîne distante rejetée 001–004.
```

En cas d’échec, la stratégie est :

```text
stopper le déploiement
→ consigner la révision atteinte
→ analyser l’état
→ réparer par migration revue ou restaurer via PITR
→ ne jamais contourner avec le rôle propriétaire
```

## 4. Procédure de push et de release

### 4.1 Changements normaux

```text
feature/*
→ pull request
→ CI
→ revue
→ merge dans main
→ preview non sensible
→ staging
→ approbation
→ production
```

Règles :

```text
- aucun push direct normal vers main ;
- aucun secret dans le code, les manifests, les logs ou les conversations ;
- CI et revue obligatoires avant merge ;
- aucune migration production déclenchée par un simple push ;
- les previews ne reçoivent pas de données ou secrets de production.
```

### 4.2 Restauration exceptionnelle de main

La restauration de sécurité suit impérativement cette séquence :

```text
1. git fetch origin main
2. créer quarantine/unreviewed-main-<sha> depuis le commit divergent
3. vérifier que la branche quarantine pointe sur le bon commit
4. restaurer main avec git push --force-with-lease
5. re-fetch et vérifier le SHA distant
6. protéger main contre les force-push ultérieurs
```

`--force-with-lease` est obligatoire : il évite d’écraser un changement distant arrivé après l’audit.

### 4.3 Authentification GitHub

Les pushs seront réalisés uniquement avec :

```text
- une connexion GitHub/OAuth sécurisée au workspace ;
- ou une GitHub App avec permissions minimales ;
- ou un terminal local déjà authentifié.
```

Ils ne seront jamais réalisés avec :

```text
- un token collé dans une conversation ;
- un token committé dans Git ;
- un token dans .git/config ;
- un token dans render.yaml, railway.json, Dockerfile ou workflow YAML.
```

### 4.4 Secrets de release

Les secrets de migration appartiennent uniquement à l’environnement GitHub `production` :

```text
DATABASE_URL_MIGRATOR
DATABASE_URL_APP
```

Le workflow de migration est :

```text
- manuel ;
- limité à main ;
- protégé par l’environnement production ;
- sérialisé ;
- exécuté avec permissions GitHub minimales ;
- incapable d’exécuter une migration sans confirmation explicite.
```

## 5. Séquence active

```text
P0 : intégrité Git et rotation des secrets
→ P1 : Neon EU, staging et migrations C6.1
→ C6.2 : registre probatoire
→ C6.3 : validation humaine
→ C7 : gouvernance réglementaire
→ C8 : pilote B2B
→ C9 : industrialisation
```

Aucun passage de chantier n’est automatique : chaque gate de sortie est vérifié avant la suite.
