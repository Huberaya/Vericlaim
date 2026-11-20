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
3. ~~**Capacités résiduelles du rôle système**~~ — **traité au §9.** Droits
   réécrits au niveau colonne ; `memberships.organization_id`/`.role_id` et les
   colonnes d'identité de `users` ne sont plus modifiables. Deux capacités
   restent, assumées et documentées : `INSERT` sur `billing_provider_events`
   (webhook sans contexte de tenant) et `status` sur `users` (activation d'un
   compte invité).
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
Vercel aboutit au SHA final, et la CI technique est verte. Le contrôle instable
qui faisait douter de cette CI est élucidé : c'était un défaut de déterminisme
du rejeu, corrigé avec un test qui échoue sans le correctif (§8). Mais la condition
explicite de la mission — une revue humaine réelle et documentée — n'est pas
remplie, et je ne la remplacerai pas par une affirmation.

**GO dès que** : un relecteur humain a examiné les commits `2afe0c6` à
`d148be7` (plus le correctif de déterminisme du §8) et posé `security-reviewed`
en connaissance de cause, et la Preview a été ouverte une fois par une personne
qui y a accès. Les deux sont des actions humaines, pas des correctifs restants.

---

## 8. Un contrôle instable, élucidé et corrigé

`Backend verification` a échoué trois fois, dont deux sur des commits **de
documentation** (`508c162`, `47bc0f4`) qui ne touchent ni le moteur ni les
règles. La relance passait. Le diagnostic tient en trois mesures.

**Assertion réelle.** Les annotations GitHub — et non l'extrait tronqué du
rapport JUnit — donnent le message complet :
`rejouer la version persistée ne reproduit pas les verdicts stockés`,
`tests/test_analysis_regulatory_verdicts.py:365`. Une exécution antérieure avait
échoué sur `assert '62 %' in 'Son emballage est entièrement recyclable et pèse 24 g.'`,
soit une autre assertion : deux tests distincts, pas un unique coupable.

**Cause, reproduite.** La version est publiée dans **une seule transaction** :
toutes ses allégations partagent donc le même `created_at`, à la résolution que
la base conserve. Le rejeu ordonnait ces allégations par
`(created_at, id)` — or `id` est un `uuid4`. À horodatages égaux, l'ordre de
rejeu se décidait donc sur un tirage aléatoire, alors que les verdicts stockés
portent le `sequence_number` de la publication. Reproduction délibérée, en
forçant un horodatage unique et des UUID réattribués : les mêmes verdicts
ressortent **dans un autre ordre**.

**Correctif.** L'ordre de détection est désormais écrit avec l'allégation
(`attributes_json.detection_sequence`, dans `app/analyses/service.py`), et le
rejeu trie sur cette clé (`_persisted_claim_order`). Aucune migration : la clé
vit dans la provenance déjà persistée. Les versions antérieures, qui n'en ont
pas, retombent explicitement sur l'ordre historique — le repli est écrit, pas
supposé.

**Preuves.** Une base où tous les horodatages coïncident donne des ordres
identiques après correctif, divergents avant. Le nouveau test
`test_the_replay_order_survives_identical_timestamps` échoue sans le correctif
et passe avec. Suites complètes : **688 passed / 68 skipped** sur SQLite et
**688 passed / 68 skipped** sur PostgreSQL.

**Ce que cela change pour la fusion.** La CI redevient une preuve : l'échec
n'était pas un aléa de plateforme mais un défaut de déterminisme du rejeu, qui
pouvait aussi faire échouer une vérification d'audit en production. Il reste
qu'un voyant vert obtenu par relance ne prouvait rien — c'est précisément ce qui
a retardé le diagnostic.

---

## 9. Dernier bloqueur de la revue : les privilèges du rôle système

Le point 2 des blocages (§6, « capacités résiduelles du rôle système ») est
traité. Le périmètre touché est celui des `GRANT` uniquement : ni la RLS, ni
`BYPASSRLS`, ni les politiques n'ont été modifiés, et le resserrement a été
vérifié par lecture des attributs du rôle, pas seulement du SQL.

### Ce qui était accordé, mesuré et non déduit

Interrogation de PostgreSQL (`has_table_privilege`, `has_column_privilege`) sur
une base construite par la chaîne Alembic réelle, avec les trois rôles de
production :

| Table | Avant | Après |
| --- | --- | --- |
| `users` | `SELECT` table, `INSERT`, **`UPDATE` table** | `SELECT` table, `INSERT`, `UPDATE` sur 6 colonnes |
| `memberships` | `SELECT` table, `INSERT`, **`UPDATE` table** | `SELECT` table, `INSERT`, `UPDATE` sur 3 colonnes |
| `identity_tokens` | `UPDATE` table | `UPDATE` sur 3 colonnes |
| `email_messages` | `UPDATE` table | `UPDATE` sur 5 colonnes |
| `billing_provider_events` | `UPDATE` table | **aucun** |
| `password_login_attempts` | `SELECT`/`INSERT`/`UPDATE` table | `SELECT`/`UPDATE` table, plus d'`INSERT` |
| `roles` | `SELECT` table | `SELECT` sur `id`, `code` |
| `organizations` | `SELECT` table | `SELECT` sur 7 colonnes |

Les colonnes retirées sont celles qui permettaient une élévation de privilège :
`memberships.organization_id` et `.role_id` (déplacer une adhésion, changer son
rôle), `users.email`, `.identity_provider`, `.external_subject` (faire passer un
compte pour un autre), `identity_tokens.user_id`, `.purpose`, `.token_hash`,
`.expires_at` (détourner ou prolonger un lien).

### Trois constats que l'audit a produits

1. **`GRANT UPDATE` de table ne se resserre pas par un `GRANT` de colonne.**
   PostgreSQL ajoute les privilèges, il ne les réduit jamais. La migration
   commence donc par `REVOKE ALL`, sans quoi elle aurait l'air de resserrer en
   laissant tout ouvert.
2. **La lecture d'entité entière était le vrai obstacle, pas le privilège.**
   Trois lectures chargeaient `organizations` ou `roles` pour n'utiliser qu'un
   identifiant. Les borner par colonne faisait échouer l'inscription sur
   « permission denied for table organizations ». Le correctif est dans le code
   (projections minimales), pas dans un élargissement du droit.
3. **Le webhook de facturation ne peut pas fonctionner sous le rôle
   applicatif.** Mesuré : `INSERT` d'un événement sans organisation →
   `new row violates row-level security policy`. Le droit d'insertion du rôle
   système sur `billing_provider_events` est donc conservé, et le défaut de
   routage consigné comme **défaut distinct, non corrigé** : le corriger suppose
   de router le webhook vers l'identité système, ce qui est un changement de
   parcours et un chantier de facturation exclu par la mission.

### Ce que ces droits ne garantissent pas

Le column-level dit quelles colonnes, jamais quelles valeurs ni quelles lignes.
`GRANT UPDATE (status) ON users` autorise le rôle système à écrire n'importe
quelle valeur de `status` sur n'importe quel utilisateur. Les parcours ne le font
que sur la ligne du jeton consommé, mais la base ne l'impose pas. Restreindre ce
point demanderait des politiques conditionnelles par parcours : c'est un
durcissement séparé, et il est écrit dans la migration plutôt que passé sous
silence.

### Preuves

| Vérification | Résultat |
| --- | --- |
| Parcours d'identité sous les rôles réels (`test_postgres_identity_flows.py`) | **9 passed** |
| Privilèges effectifs, positif et négatif (`test_postgres_system_role_privileges.py`) | **40 passed** |
| Isolation tenant (`test_postgres_tenant_isolation.py`) | **15 passed** |
| Suite complète SQLite | **688 passed / 108 skipped** |
| Suite complète PostgreSQL | **768 passed / 28 skipped** |
| Job CI « PostgreSQL tenant isolation (RLS) » | **success**, 80 tests |
| Downgrade → upgrade | droits de table restaurés puis retirés |

Mutations vérifiées, chacune fait échouer au moins un test : `UPDATE` de table
rétabli sur `users`/`memberships` (11 échecs) ; tous les `UPDATE` de colonne
retirés (20 échecs) ; `GRANT SELECT` global rétabli sur `roles`/`organizations`
(1 échec) ; `password_hash` retiré des colonnes accordées (1 échec).

### Effet sur le verdict

Ce bloqueur est levé. Le verdict reste **NO-GO** pour les raisons humaines du
§7 — relecture indépendante, label `security-reviewed`, ouverture de la Preview —
et pour le défaut de routage du webhook signalé ci-dessus, qui est un défaut de
parcours, pas de privilège.

### Un angle mort de plus, trouvé en branchant la preuve

En ajoutant ces fichiers au job CI qui possède le service PostgreSQL, deux
défauts sont apparus, tous deux invisibles jusque-là :

1. **Les tests d'identité n'étaient exécutés nulle part en CI.**
   `test_postgres_identity_flows.py` (9 tests, existant depuis la mission 10) et
   `test_postgres_system_role_privileges.py` (40) sont ignorés sans
   `VERICLAIM_TEST_POSTGRES_URL`, que seul le job PostgreSQL définit — et ce job
   ne les listait pas. Ils étaient « skipped », donc verts sans rien prouver :
   exactement l'angle mort qui avait laissé passer les trois défauts d'identité
   corrigés en mission 10.
2. **Leur fixture forçait un DSN d'administration sans mot de passe**
   (`password=""`), ce qui ne fonctionne que sur un serveur en `trust`. En CI,
   `postgres:16` exige `scram-sha-256` : les neuf tests d'identité échouaient en
   erreur de fixture (`fe_sendauth: no password supplied`). Reproduit localement
   avec un serveur `initdb --auth-host=scram-sha-256` : 9 erreurs avant,
   49 passed après.

Le premier défaut explique le second : tant que les tests n'étaient pas
exécutés, leur configuration de connexion n'était jamais confrontée à un serveur
réel. Le job est désormais **vert** avec les deux fichiers branchés, et il publie
un rapport JUnit ainsi que les noms de tests en échec dans les annotations — les
journaux de ce job renvoyant `EOF` depuis l'environnement d'audit, comme ceux du
job Backend.
