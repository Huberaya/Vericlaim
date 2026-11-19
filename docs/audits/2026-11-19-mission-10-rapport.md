# Mission 10 — lever les blocages de sécurité et préparer la fusion

**Date :** 19 novembre 2026
**Branche :** `arena/2d0b984d-vericlaim` · **PR :** #17
**Périmètre :** trois points seulement — RLS d'identité, blocage Vercel, revue et CI.
Aucune intervention sur `main`, sur la production, sur Neon production, sur Clerk,
ni sur les secrets. Aucune migration appliquée à une base réelle. PR non fusionnée.

---

## 1. Défauts corrigés

### 1.1 La RLS d'identité empêchait l'inscription et laissait fuiter les jetons orphelins

Les deux défauts ont été **mesurés** sur PostgreSQL 16.2 avec les rôles réels, pas
déduits de la lecture du code :

* l'inscription échouait sous `vericlaim_app` — « new row violates row-level
  security policy », `WITH CHECK` refusant une ligne rattachée sans contexte ;
* la politique permissive (`organization_id IS NULL OR …`) laissait l'organisation
  A lire le jeton de réinitialisation d'un utilisateur rattaché à B, dès lors que
  le jeton avait été émis avant le rattachement.

Le constat de départ (« sans contexte, les lignes d'autres organisations sont
visibles ») est en revanche **faux** : vérifié, non reproduit. Ce qui était
visible sans contexte, c'était les lignes sans organisation, plus les lignes de
l'organisation courante. La correction ne prétend donc pas avoir réparé un défaut
qui n'existait pas.

**Correction — séparation par identité, pas par exception**

| Élément | Rôle |
| --- | --- |
| `infra/neon/04_system_role.sql` | provisionne `vericlaim_system` (LOGIN, BYPASSRLS), refuse un mot de passe court ou une identité privilégiée |
| `c5f1a8e37b910_tenant_and_system_access_separation` | politique unique `FOR ALL`, `USING` **et** `WITH CHECK` stricts ; droits système par colonne |
| `app/core/database.py` | `SystemEngine`, `SystemSessionLocal`, `system_session()`, refus au démarrage si `SYSTEM_DATABASE_URL` manque hors développement |
| `app/identity/self_service.py` | quatre parcours transverses exécutés sur l'identité système |

Droits du rôle système, **découverts en exécutant** chaque parcours (chacun a
d'abord échoué, puis a conduit à un droit) :

* `SELECT` ciblé : `users`, `organizations`, `memberships`, `roles`,
  `auth_sessions`, `password_login_attempts`, `identity_tokens`, `email_messages`
  (sans corps, sujet ni destinataire), `billing_provider_events` ;
* `INSERT` : `users`, `organizations`, `memberships`, `identity_tokens`,
  `email_messages`, `billing_provider_events`, `password_login_attempts`,
  `audit_events` ;
* `UPDATE` : `identity_tokens`, `email_messages`, `billing_provider_events`,
  `users`, `memberships`, `password_login_attempts` ; et `revoked_at` **seul**
  sur `auth_sessions` — sans quoi un lien volé laisserait l'attaquant connecté.

Un point de méthode : PostgreSQL n'a pas de privilège `INSERT`/`UPDATE` par
colonne. La granularité réelle ne porte que sur les **lectures** ; l'énoncer
autrement aurait été une affirmation fausse.

### 1.2 L'acceptation d'invitation écrivait la session sans contexte

Défaut trouvé en écrivant le test, pas en relisant le code : l'activation de
l'adhésion écrivait `memberships` sans droit système (« permission denied »),
donc l'invité ne pouvait pas entrer.

La correction scinde le parcours. Consommation du jeton et activation de
l'adhésion sur l'identité système ; ouverture de session sur la session
applicative, `auth_sessions` étant hors RLS — c'est déjà ainsi que la connexion
par mot de passe ouvre la sienne. Accorder `INSERT` sur les sessions au rôle
système aurait été une capacité sans besoin, refusée.

### 1.3 Le blocage Vercel était une cadence de cron, pas un défaut de build

`vercel.json` déclarait trois `crons` en `* * * * *`. Vercel refuse plus d'une
exécution par jour sur un compte Hobby, **au déploiement**, avant compilation :
chaque Preview échouait en 0 s.

La régression vient de l'intégration de la PR #7 (`b30de3a`) : `main` ne
déclarait aucun `crons`. La cadence passe au quotidien (`0 3`, `15 3`, `30 3`).
La route appelée existe, est idempotente et **échoue visiblement** sans
`CRON_SECRET` (503) au lieu de ne rien faire.

Ce que cela ne corrige pas, et qui est écrit tel quel dans la documentation : un
environnement sans serveur n'exécute une fonction que pendant sa requête. La
planification est un rattrapage, pas un drainage continu ; A-06 reste ouverte
sur ce point, l'option recommandée pour un pilote restant le worker hors Vercel.

### 1.4 Deux tests dépendaient de l'ordre des lignes

`test_technical_sheet_tables.py` désignait une allégation par son rang
(`paragraph_claims[0]`). SQL ne garantit aucun ordre : le test échouait sous
PostgreSQL et passait sous SQLite. Il désigne désormais l'allégation par son
contenu et vérifie qu'il en existe exactement une — contrôle plus fort.

### 1.5 La répétition de restauration ne provisionnait pas l'identité système

Le job passait à `ba933b3` et échouait à `2afe0c6` : la migration exige le rôle
`vericlaim_system`, que ce job ne créait pas. Une étape exécute désormais le
script audité, avec le même appel `psql` qu'en production. Job **vert** depuis.

---

## 2. Commits de la mission

| Commit | Contenu |
| --- | --- |
| `2afe0c6` | séparation locataire / système, rôle dédié, migration, 9 preuves PostgreSQL, recette branchée sur le script audité |
| `e9a2765` | `vercel.json` en cadence quotidienne, documentation A-06, 01, 04, 08 |
| `e836431` | provisionnement de l'identité système dans la répétition de restauration |
| `d148be7` | test de revendications indépendant de l'ordre des lignes |

---

## 3. Tests exécutés et résultats

| Suite | Résultat |
| --- | --- |
| SQLite complet | **687 passed**, 0 failed — six exécutions consécutives le 19/11 au soir |
| PostgreSQL complet (rôles réels) | **746 passed**, 9 skipped, 0 failed |
| Preuves d'identité PostgreSQL (`test_postgres_identity_flows.py`) | **9/9** |
| Preuves d'isolation (`test_postgres_tenant_isolation.py`) | **15/15** |
| Recette de bout en bout | **54/54** |
| `tsc --noEmit` + `npm run build` | verts |
| Discipline des migrations | une seule tête, `c5f1a8e37b910` |
| Surface publique | 113 chemins inspectés, 20 sans identité toutes justifiées |

**Un incident d'exécution, mentionné plutôt que tu.** Une exécution de la suite
SQLite a échoué sur `test_a07_scanner_readiness.py` avec
`sqlite3.OperationalError: disk I/O error`. Le disque n'était pas plein (18 %), le
test passe seul, et six exécutions complètes consécutives ont ensuite été vertes.
Je le classe comme défaillance d'entrée-sortie de la machine de test, pas comme
défaut produit — mais je ne peux pas le prouver, seulement l'observer.

**Validation par mutation** — les tests échouent si l'on réintroduit le défaut :

| Mutation | Effet |
| --- | --- |
| Parcours repassés sur le rôle applicatif | **6 échecs** |
| Politique permissive rétablie | **1 échec** |
| `SELECT` global accordé au rôle système | **1 échec** |

---

## 4. État de la CI et de Vercel au SHA final

**Dernier commit de code : `6ca68e5`** — les corrections éditoriales qui suivent ne
changent aucun code.

| Contrôle | État |
| --- | --- |
| Backend verification | ✅ |
| Frontend verification | ✅ |
| PostgreSQL tenant isolation (RLS) | ✅ |
| Recette qualification (real data plane) | ✅ |
| Répétition de restauration | ✅ |
| Vercel (déploiement) | ✅ success — Preview READY |
| Chemins sensibles modifiés | ❌ **volontaire** (label absent) |

**Preview :** `https://vericlaim-3no3ylyw5-baya-ibraimmayoanouedu-8164s-projects.vercel.app`
— état Vercel `success`, SHA `6ca68e5` (dernier commit de code).

**Ce que cette Preview ne prouve pas.** Elle n'a pas été ouverte. Depuis cet
environnement, `curl` vers `*.vercel.app` échoue à la poignée de main TLS
(`SSL_ERROR_SYSCALL`, code 35) ; le connecteur Vercel répond « Access denied »
(token sans les permissions requises). Le statut `success` établit que le build
et le déploiement ont abouti, **pas** qu'un parcours utilisateur fonctionne.
Cette vérification reste à faire par une personne ayant accès à la Preview.

---

## 5. Revue de sécurité (`SECURITY.md` §5)

Chaque point, avec ce qui a été **exécuté** et ce qui reste une **affirmation**.

| Point de la checklist | Statut | Preuve ou réserve |
| --- | --- | --- |
| Aucun chemin ne crée d'identité sans identité vérifiée | prouvé | 113 chemins interrogés sans session ; 20 sans identité, toutes déclarées et justifiées (`check_public_surface.py`) |
| Route authentifiée **et** autorisée | prouvé | `test_public_surface_is_closed.py`, `test_catalog.py` (RBAC), `test_identity_tenancy.py` |
| Mutation protégée par CSRF | prouvé | `test_identity_tenancy.py:63`, `test_self_service_identity.py:511`, `test_data_subject_rights.py:338` (403 sans jeton) |
| Requêtes tenant scopées par `organization_id` | prouvé | 15 preuves RLS + revue des dépôts ; aucun accès inter-organisation mesuré |
| Nouvelle table tenant → politique RLS | prouvé | `verify_runtime.py` refuse une table oubliée ; tête `c5f1a8e37b910` validée |
| Pas de donnée sensible dans logs, erreurs, réponses | **partiellement prouvé** | tests de non-fuite sur les réponses d'API ; pas d'audit exhaustif de tous les `logger.*` |
| Aucun secret en dur | **partiellement prouvé** | scripts de provisionnement refusent un mot de passe court ; aucun scanner de secrets (gitleaks/trufflehog) dans la CI — **non couvert** |
| Entrées non fiables validées | prouvé | schémas Pydantic, `test_secure_documents.py`, verdicts ClamAV indéterminés |
| Échec de dépendance externe → échoue fermé | prouvé | `test_a07_scanner_readiness.py`, `test_clamav_indeterminate_verdicts.py`, route de cycle en 503 sans secret |
| Test de non-régression couvre la propriété | prouvé | 24 preuves PostgreSQL dédiées |
| Test éprouvé par mutation | prouvé | 3 mutations, 8 échecs au total (tableau §3) |

**Revue indépendante : non réalisée.** Aucun humain n'a relu ce lot. Le label
`security-reviewed` reste donc **absent**, et le contrôle « Chemins sensibles »
reste rouge **volontairement** : c'est le signal prévu par le dépôt, pas un
échec à contourner. Poser le label sans revue reviendrait à simuler une
approbation.

---

## 6. Blocages restants

1. **Revue humaine indépendante** — requise avant fusion. C'est le blocage
   principal, et il ne dépend pas de moi.
2. **Preview non ouverte** — statut Vercel `success`, contenu invérifiable
   depuis cet environnement (TLS réinitialisé, connecteur sans permission).
3. **Capacités résiduelles du rôle système** — `count(*)` sur `audit_events`,
   `INSERT` sur `organizations`. Nécessaires, mais plus larges que « identité ».
4. **A-06 (drainage des files)** — atténuée, non résolue : la planification
   quotidienne est un rattrapage, pas un traitement continu.
5. **Neon non vérifié** — toute la preuve RLS vient d'une base construite
   localement ; un écart de configuration avec Neon échapperait à ces tests.
6. **TLS non rejoué localement** — le binaire PostgreSQL disponible est compilé
   sans SSL, donc l'étape `rehearse_restore` n'a pas pu être rejouée ici ; elle
   est verte en CI, ce qui est la seule preuve dont je dispose.

---

## 7. Verdict

**NO-GO pour la fusion.**

Les trois points techniques de la mission sont traités et prouvés : la RLS est
corrigée par une séparation d'identité vérifiée par mutation, le déploiement
Vercel aboutit au SHA final, et la CI technique est verte. Mais la condition
explicite de la mission — une revue humaine réelle et documentée — n'est pas
remplie, et je ne la remplacerai pas par une affirmation.

**GO dès que** : un relecteur humain a examiné les commits `2afe0c6` à
`d148be7` et posé `security-reviewed` en connaissance de cause, et la Preview a
été ouverte une fois par une personne qui y a accès. Les deux sont des actions
humaines, pas des correctifs restants.
