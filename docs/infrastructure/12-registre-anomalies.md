# 12 — Registre des anomalies

**Date :** 12 novembre 2026
**Base :** `c73b12fd663e744dcda153bc35b0166fb977feaa` (branche `main`, après fusion de la PR #14)
**SHA de correction :** mission 3 `fa31e0a8`/`a73e1d5` (A-13) ; mission 4 sur la branche `arena/64dc27b3-vericlaim`

**Règle de lecture de ce registre.** Trois états seulement, et jamais confondus :
**corrigé** (défaut reproduit, correction testée, preuve exécutée),
**partiel** (une partie est prouvée, le reste dépend d'une ressource externe),
**ouvert** (rien n'est prouvé). Un statut « partiel » n'est pas une manière polie
de dire « vert » : il nomme précisément ce qui manque.
**Périmètre :** recette technique uniquement, aucune donnée client

Chaque anomalie indique : comment elle a été **observée**, sa cause vérifiée, ce
qui a été corrigé, et ce qui **reste ouvert**. Aucune anomalie n'est close sur la
base d'un raisonnement : elle est close sur une observation.

## Synthèse

| ID | Titre | Sévérité | État |
|---|---|---|---|
| A-01 | Import documentaire impossible sur PostgreSQL (HTTP 500) | **P0** | **corrigé** |
| A-02 | Rapport PDF d'une analyse persistée en erreur 500 | P1 | **corrigé** |
| A-03 | Dossier probatoire ZIP en erreur 500 | P1 | **corrigé** |
| A-04 | La suite de tests ne voyait pas les défauts ci-dessus | P1 | **corrigé** |
| A-05 | Preview sans `DATABASE_URL` servie par un SQLite jetable | P1 | **corrigé** |
| A-06 | Aucun worker/cron sur Vercel : les files ne sont jamais drainées | **P1** | **corrigé côté code** — déploiement du runtime : **à faire par l'équipe** |
| A-07 | Aucun antivirus réel n'a pu être exécuté | **P0 pour un pilote** | **partiel** — code fail-closed + readiness corrigés ; **moteur réel non déployé** |
| A-08 | Aucune sauvegarde automatisée, stockage objet non couvert | **P0 pour un pilote** | **partiel** — script + tests réels ; **planification et objets non couverts** |
| A-09 | Aucun monitoring proactif (métriques, alertes) | P1 | **partiel** — instrumentation + règles livrées ; **alertes jamais observées** |
| A-10 | `vericlaim` : TLS PostgreSQL non vérifiable, `verify_runtime.py` jamais exécuté avec succès | P1 | **partiel** — vérificateur livré et testé ; **preuve TLS sur vraie URL non exécutée** |
| A-11 | Import d'un PDF volumineux refusé par la limite par défaut (2 Mo) | P2 | **atténué** |
| A-12 | Le job CI de recette ne pouvait pas s'installer (python 3.13 vs wheels `pgserver`) | P1 | **corrigé** |
| A-14 | Le harnais laissait un serveur Uvicorn orphelin après toute exception | P1 | **corrigé** |
| A-13 | Échec CI intermittent du parcours de recette, avec un cas établi de **succès d'import non durable** | **P0** | **corrigée** — cause première : commit postérieur à la réponse (§ voir `15`) |

---

## A-01 — Import documentaire impossible sur PostgreSQL (HTTP 500)

**Sévérité : P0 (bloquant : le parcours principal du produit).**
**Statut : corrigé.**

**Observation.** Sur la pile réelle (PostgreSQL 16 + S3 + clamd contractuel), tout
import de PDF valide échouait à la complétion avec **HTTP 500**. Le parcours
n'avait jamais été exercé sur PostgreSQL : la suite tournait sur SQLite, où le
défaut est invisible.

**Cause vérifiée.** `complete_document_upload` renseignait
`document_uploads.finalized_document_version_id` **avant** l'`INSERT` de
`document_versions`. Aucune relation ORM ne déclare cette dépendance, donc
SQLAlchemy ordonnait l'`UPDATE` d'abord. PostgreSQL refuse (violation de clé
étrangère) ; SQLite, lui, n'applique pas les clés étrangères par défaut.

**Correction.** `flush()` explicite entre la création de la version et
l'affectation de la référence (`backend/app/documents/service.py`).

**Preuve.** Étape 23 du parcours E2E : **HTTP 201**, avec `sha256` et
`size_bytes` persistés. Test de non-régression :
`test_recette_defect_regressions.py`.

**Leçon retenue et appliquée.** `PRAGMA foreign_keys=ON` est désormais posé dans
`conftest.py` : une suite qui tourne sur une base permissive doit être rendue
aussi stricte que la production.

---

## A-02 — Rapport PDF d'une analyse persistée en erreur 500

**Sévérité : P1 (livrable inutilisable).**
**Statut : corrigé.**

**Observation.** `GET /api/v1/analyses/{id}/report.pdf` renvoyait **500** dès que
l'analyse était une version **persistée** ; les analyses calculées à la volée
fonctionnaient, ce qui masquait le défaut en démonstration.

**Cause vérifiée.** `reports.py` lisait `AnalysisVersion.input_manifest`,
attribut inexistant (`AttributeError`). Le manifeste vit dans
`result_json['input_manifest']`, et son intégrité est garantie par
`input_manifest_sha256`.

**Correction.** Helper unique `_persisted_source_text(version)` qui lit le
manifeste persisté et vérifie son empreinte.

**Preuve.** Étape 38 : **HTTP 200**, 7 943 octets.

---

## A-03 — Dossier probatoire ZIP en erreur 500

**Sévérité : P1.**
**Statut : corrigé.**

Même cause qu'A-02, chemin distinct (`/dossier.zip`). Corrigé par le même helper.
Preuve : étape 39, **HTTP 200**, 6 717 octets.

Ces deux anomalies avaient la même racine : **un livrable que personne n'avait
ouvert depuis la dernière refonte du modèle**. Elles ne pouvaient pas être vues en
lecture de code seule, ni par les tests existants — d'où A-04.

---

## A-04 — La suite de tests ne voyait pas ces défauts

**Sévérité : P1 (c'est le défaut de méthode).**
**Statut : corrigé.**

**Observation.** 255 tests verts, et néanmoins un 500 sur le parcours principal.
Trois causes cumulées :

1. **SQLite partout** : clés étrangères non appliquées → A-01 invisible ;
2. **base non PostgreSQL** : 27 tests RLS étaient `skipped` silencieusement ;
3. **aucun test de bout en bout** sur les livrables réels.

**Correction.**
- `PRAGMA foreign_keys=ON` sur SQLite ;
- `VERICLAIM_TEST_POSTGRES_URL` permet d'exécuter réellement les 27 tests RLS
  (PostgreSQL 16 obtenu localement pour ce chantier) ;
- 8 tests de non-régression (`test_recette_defect_regressions.py`), dont 3 sur le
  refus d'une base SQLite sur plateforme managée ;
- matrice d'authentification (12 tests) contre un **vrai** fournisseur OIDC HTTP ;
- 9 tests OCR contre un **vrai** moteur Tesseract ;
- parcours E2E de 54 étapes, exécuté en CI (`recette-qualification`).

**Résultat.** **269 passed, 2 skipped** avec PostgreSQL, contre 255 avant — et les
2 restants sont explicitement justifiés (chemin `pytesseract` et un cas de
signature qui exige un binaire absent).

**Validation en intégration continue.** Run `43325846167` sur la PR #14 : les
4 jobs sont `success`, y compris le parcours complet et la restauration exercée
sur un runner propre. Les preuves de ce dossier ne sont donc plus seulement
locales.

---

## A-05 — Preview sans `DATABASE_URL` servie par un SQLite jetable

**Sévérité : P1 (faux sentiment de sécurité ; en pratique, démarrage en erreur).**
**Statut : corrigé.**

**Observation.** Reproduit en lançant réellement l'application avec `VERCEL=1` et
sans `DATABASE_URL` :

```
sqlite3.OperationalError: unable to open database file
```

`get_settings()` retombait sur `sqlite:///./vericlaim.db` dès que `APP_ENV` valait
`development`. Sur Vercel, cela signifie au mieux une base éphémère **sans RLS**,
au pire un plantage de démarrage — et dans les deux cas un déploiement qui ne dit
pas qu'il est mal configuré.

**Cause vérifiée.** Le repli SQLite ne distinguait pas le poste de développement
d'une plateforme managée.

**Correction.** `_running_on_managed_platform()` (`VERCEL`, `K_SERVICE`, `DYNO`) :
refus explicite d'une `DATABASE_URL` absente **et** d'une `DATABASE_URL` SQLite
sur ces plateformes. Développement local inchangé.

**Preuve.** 4 cas vérifiés : `VERCEL=1` sans URL → refus ; `VERCEL=1` + URL SQLite
→ refus ; local sans URL → SQLite accepté ; local + URL PostgreSQL → accepté.
3 tests de non-régression ajoutés.

---

## A-06 — Aucun worker ni cron sur Vercel : les files ne sont jamais drainées

**Sévérité : P1 (bloque le parcours métier hors Compose).**
**Statut : CORRIGÉ côté code — le déploiement du runtime reste à faire par l'équipe.**

**Défaut constaté.** Prouvé par contraste sur la pile réelle : workers lancés →
`job=completed`, `segments=1`, `claims=3` ; workers non lancés → `pending` /
`queued` indéfiniment, **aucune** transition. `vercel.json` n'a aucune clé
`crons`. Les workers n'exposaient que `run_forever()` : pas d'arrêt propre, pas
de battement de santé, pas de moyen de savoir si un worker était vivant, bloqué
ou absent.

**Correction réalisée.**

| Élément | Ce qui a été ajouté |
|---|---|
| `app/workers/runtime.py` | `WorkerRuntime` : boucle bornée (`max_iterations`), arrêt sur SIGTERM/SIGINT, drain à délai borné, concurrence avec `worker_id` **distincts** (`base#1…`, exigés par le fencing) |
| santé | fichier JSON atomique : `heartbeat_at`, `iterations`, `jobs_processed`, `last_error`, `stopping` |
| sonde | `python -m app.worker_healthcheck --file …` → sortie 0/1, utilisable en `HEALTHCHECK` Docker ou `ExecStartPost` systemd |
| workers | les **deux** workers (`document_extraction`, `analysis_detection`) construisent leur runtime et sortent via `SystemExit(runtime.run())` |
| robustesse | une itération en erreur est journalisée puis la boucle continue ; elle ne tue pas le worker |

**Tests exécutés.** `backend/tests/test_worker_runtime_lifecycle.py` — **8/8**.
Mordant vérifié : en retirant l'initialisation `did_work = False`, le test
d'itération en erreur échoue (1 failed, 7 passed) — le test détecte donc bien le
défaut qu'il vise.

**Preuve.** `pytest backend/tests/test_worker_runtime_lifecycle.py` → 8 passed.
Suite complète : **290 passed, 32 skipped**.

**Ce qui reste bloqué.** Le code ne déploie rien. Pour que la file soit drainée
en recette, l'équipe doit :

1. exécuter les images `document-worker` / `analysis-worker` sur un runtime à
   processus long (conteneur, VM, service managé) — **pas** un environnement
   serverless ;
2. monter le fichier de santé sur un volume et brancher la sonde
   (`app.worker_healthcheck`) sur le superviseur ;
3. si l'hébergement reste Vercel seul : **choisir** l'option cron planifié
   appelant une route idempotente. Cette route **n'existe pas** et n'est pas
   ajoutée ici — l'ajouter sans décision d'architecture aurait été une surface
   HTTP non demandée.

**Options d'architecture, toujours non tranchées** (aucune décidée dans ce
chantier) : (1) cron Vercel appelant une route idempotente ; (2) service worker
hors Vercel ; (3) file managée + runtime adapté. Recommandation inchangée :
option 2 pour un pilote.

---

## A-07 — Aucun antivirus réel n'a pu être exécuté

**Sévérité : P0 pour un pilote ingérant des pièces tierces.**
**Statut : PARTIEL — code corrigé et testé, moteur réel NON déployé.**

**Défaut constaté.** Deux défauts distincts, tous deux réels :

1. **aucun moyen de savoir si le scanner répondait.** `/readyz` exposait
   `malware_scanner: configured` — c'est-à-dire « une variable est définie », pas
   « un moteur analysera vos fichiers ». Une configuration correcte avec un
   clamd mort produisait exactement la même readiness qu'un système sain ;
2. `apt` indisponible, root absent, dépôts injoignables : `clamd`,
   `freshclam` et les signatures ne sont pas installables dans l'environnement
   d'exécution de l'agent. Aucune détection par un vrai moteur n'a donc pu être
   exercée ici.

**Correction réalisée.**

| Élément | Correction |
|---|---|
| `documents/scanner.py` | `healthcheck()` au Protocol et aux 3 implémentations. `ClamAvMalwareScanner` envoie un vrai **`PING`** et attend `PONG` (avec le NUL terminal et la fermeture de connexion du protocole clamd) |
| verdicts | `None` = joignable ; `scanner_unreachable` ; `scanner_unexpected_reply` ; `scanner_disabled` |
| `/readyz` | expose `malware_scanner_reachable` et `malware_scanner_reason`, **sans renommer** `malware_scanner` (`configured`/`disabled`) : les runbooks existants continuent de fonctionner |
| `document_import` | passe à `refused` si le scanner n'est **pas joignable** — le fail-closed ne dépend plus d'une simple variable |
| readiness du processus | `/readyz` **reste 200** si seul le scanner manque : la base conditionne la readiness, l'antivirus conditionne l'import. Décision explicite, documentée en test |

**Tests exécutés.** `backend/tests/test_a07_scanner_readiness.py` — **8/8**,
contre un faux démon clamd en TCP local : EICAR refusé, fichier sain accepté,
service absent, réponse inattendue, réseau coupé.

Un défaut réel a été trouvé **par** ces tests : `bytes.strip()` ne retire pas
les NUL, donc `PONG\x00` n'était jamais reconnu comme `PONG`. Corrigé
(`strip(b"\x00 \r\n")`). Sans le test, la sonde aurait déclaré tout clamd sain
comme injoignable — ou l'inverse selon la tolérance.

**Preuve.** `pytest backend/tests/test_a07_scanner_readiness.py` → 8 passed.

**Limite explicite, écrite dans le test lui-même.** Le verdict EICAR vient d'un
**serveur de test**, pas du moteur ClamAV. Ce qui est prouvé : le protocole, le
fail-closed, la readiness. Ce qui **n'est pas** prouvé : qu'un vrai ClamAV
détecte un vrai EICAR avec des signatures à jour.

**Ce qui reste bloqué.** Déployer un vrai `clamd`, vérifier
`/readyz → malware_scanner_reachable: true`, rejouer l'EICAR **sur la vraie
URL**, renseigner `scan_signature_version` (toujours jamais rempli — la preuve
« analysé avec la base X » reste non auditable), documenter la quarantaine.
**Ne pas communiquer « antivirus opérationnel » avant cela.**

---

## A-08 — Aucune sauvegarde automatisée ; stockage objet non couvert

**Sévérité : P0 pour un pilote avec données réelles.**
**Statut : PARTIEL — script et tests réels ; planification et objets non couverts.**

**Défaut constaté.** Aucun script, aucune tâche, aucun workflow de sauvegarde ;
aucune politique de rétention ; aucun moyen de vérifier qu'une archive était
intègre avant de compter dessus. Le dépôt savait **restaurer**
(`tools/recette/restore.py`) mais pas **produire une archive vérifiable**.

**Correction réalisée.** `tools/backup/vericlaim_backup.py` :

| Sous-commande | Ce qu'elle fait |
|---|---|
| `backup` | `pg_dump` format custom + inventaire du stockage objet → `manifest.json` (taille et SHA-256 de **chaque** pièce, révision Alembic, compteurs par table) |
| `verify` | recalcule taille et SHA-256, sortie **2** si l'archive est tronquée ou altérée |
| `prune` | `--keep-last`, `--max-age-days` ; le dernier jeu n'est **jamais** supprimé |
| `restore` | crée une base **neuve** `vericlaim_restore_<horodatage>`, refuse une base existante, compare révision et compteurs au manifeste |

Aucune chaîne de connexion n'est acceptée en argument : elle passe par une
variable d'environnement. Les rapports passent par `redact_dsn` et ne
contiennent ni mot de passe ni DSN.

**Tests exécutés.** `backend/tests/test_a08_backup_restore.py` — **8/8** contre
un **vrai** PostgreSQL 16 jetable (`pgserver`), pas une doublure :

| Test | Ce qu'il verrouille |
|---|---|
| manifeste ↔ archive | SHA-256 et taille enregistrés = valeurs recalculées |
| archive tronquée | `verify` sort 2 avec un motif explicite |
| archive altérée (même taille) | détectée par le SHA-256 |
| restauration | base neuve, révision préservée, compteurs identiques, source intacte |
| cible absente | aucune restauration tentée (`SystemExit`) |
| rétention | ne vide jamais le magasin, garde bien les N derniers |
| `redact_dsn` | ni mot de passe ni DSN dans le rapport |

Un défaut relevé et corrigé pendant ce chantier : le calcul de `kept` dans
`prune` était maladroit (reconstruction des chemins depuis les noms). Remplacé
par un calcul direct sur les noms.

**Preuve.** `pytest backend/tests/test_a08_backup_restore.py` → 8 passed
(2,8 s, cluster réel inclus).

**RPO/RTO — mesure, pas promesse.**

| Grandeur | Valeur mesurée / statut |
|---|---|
| RTO mesuré (restauration d'un schéma vide) | **~3 s** — sans valeur pour la production |
| RTO attendu à l'échelle réelle | **non mesuré** : aucune base de taille réaliste n'a été sauvegardée ici |
| RPO | **dépend entièrement de la planification**, qui n'existe pas : aujourd'hui le RPO réel est « depuis la dernière exécution manuelle » |

Le runbook `docs/runbooks/incident-sauvegarde.md` documente la procédure
complète, y compris ce qu'il ne couvre pas.

**Ce qui reste bloqué.** (a) **planification** : aucune tâche cron/workflow
n'appelle le script — c'est une décision d'hébergement, pas de code ;
(b) **stockage objet** : seul l'*inventaire* est enregistré, les objets ne sont
**pas** rechargés — une disparition est constatable, pas réparable ;
(c) **Neon** (PITR, rétention, chiffrement, responsable) : hors de portée de
l'agent (voir `03` §0) ; (d) les rôles ne sont pas dans le dump et doivent être
recréés, puis les grants réappliqués — c'est dans le runbook.

---

## A-09 — Aucun monitoring proactif

**Sévérité : P1 pour un pilote.**
**Statut : PARTIEL — instrumentation et règles livrées ; aucune alerte observée.**

**Défaut constaté.** Aucune métrique, aucune alerte. Un worker arrêté (A-06) ou
une file qui gonfle ne se voyait que si quelqu'un regardait. `/readyz` répond
« prêt / pas prêt » et ne dit jamais *pourquoi*.

**Correction réalisée.**

| Pièce | Contenu |
|---|---|
| `app/core/metrics.py` | registre Prometheus sans dépendance, **verrouillé** (les routes FastAPI tournent en pool), histogramme de latence, compteurs par gabarit de route |
| `app/core/metrics_middleware.py` | mesure HTTP ; `/healthz`, `/readyz`, `/metrics` **exclus** (sinon le superviseur fausserait le taux d'erreur) ; le **gabarit** de route est exporté, jamais l'URL reçue |
| `app/core/queue_metrics.py` | profondeur par état et **âge du plus vieux job en attente**, lus **en base** |
| `GET /metrics` | export Prometheus, y compris `database_up`, `scanner_reachable`, files |
| `infra/monitoring/alerts.yml` | 9 règles, chacune avec cause probable et runbook |
| `infra/monitoring/worker_heartbeat_exporter.py` | convertit le battement du worker en métrique d'âge |
| `docs/runbooks/` | 5 runbooks, tous référencés par les règles |

**Décision de conception : les métriques de file sont lues en base, pas tenues
par le worker.** Un compteur en mémoire du worker vaudrait zéro quand le worker
est mort — précisément l'état que l'alerte doit détecter. Même raison pour
l'exportateur de battement : un âge calculé par un tiers est sans ambiguïté,
alors qu'une métrique « poussée » disparaît avec le processus bloqué.

**Tests exécutés.** `backend/tests/test_a09_metrics_and_alerts.py` — **8/8** :
séries réellement présentes, routes de santé exclues, aucun identifiant dans
les labels, compteur sans perte sous 8 threads × 500 incréments, règles YAML
valides, **chaque runbook existe réellement sur disque**, seuils testés
juste-au-dessus / juste-en-dessous, exportateur rendant `+Inf` quand le
fichier manque (et non `0`, qui se lirait « parfaitement sain »), profondeur de
file lue en base.

Deux défauts trouvés par ces tests : une alerte sans `cause` (ajoutée) et une
assertion de seuil que j'avais écrite de façon erronée (corrigée).

**Preuve.** `pytest backend/tests/test_a09_metrics_and_alerts.py` → 8 passed.
`GET /metrics` retourne 200 avec les séries attendues (vérifié en TestClient).

**Distinction exigée par la mission, tenue explicitement :**

| Niveau | État réel |
|---|---|
| **Instrumentation présente** | **OUI** — `/metrics` sert des séries réelles, testées |
| **Alertes configurées** | **OUI** — `alerts.yml`, valide et vérifié par test |
| **Alertes observées** | **NON** — aucun Prometheus ni Alertmanager n'a tourné ; aucune alerte n'a été reçue, aucune n'a été **vue** se déclencher |

Les tests de seuils rejouent la logique de comparaison sur des valeurs
choisies ; ce n'est **pas** un Prometheus réel. Le dernier niveau reste donc
non atteint, et c'est écrit ici plutôt que sous-entendu.

**Ce qui reste bloqué.** Déployer un collecteur (Prometheus ou équivalent),
brancher l'exportateur de battement, router les alertes vers un canal réel
(e-mail/astreinte), et **provoquer** au moins une alerte en recette pour
prouver le chemin de bout en bout. Aucun de ces trois points n'est fait.

---

## A-10 — TLS PostgreSQL et `verify_runtime.py` non validés

**Sévérité : P1 (preuve manquante, pas défaut produit connu).**
**Statut : PARTIEL — vérificateur livré et testé ; preuve TLS sur vraie URL non exécutée.**

**Défaut constaté.** Deux problèmes, dont un documentaire qui comptait autant que
le technique :

1. `infra/neon/verify_runtime.py` exige un TLS actif et n'avait **jamais**
   réussi : le PostgreSQL local est compilé sans SSL, toutes les tentatives
   s'arrêtaient sur `RuntimeError: Refusing database operation: PostgreSQL TLS
   is not active.` C'était une preuve négative utile, pas la validation
   attendue ;
2. la documentation attribuait la responsabilité de Neon à un « assistant » et
   laissait entendre que **l'équipe n'avait pas accès à Neon**. C'est faux :
   l'équipe de pilotage est propriétaire du projet. La limite réelle est celle
   de **l'environnement d'exécution de l'agent** (`api.neon.tech` injoignable),
   pas celle de l'équipe. **Corrigé** : nouvelle section §0 de
   `docs/infrastructure/03-neon-et-runtime.md`, avec la commande exacte que
   l'équipe peut lancer.

**Correction réalisée.** `tools/runtime_check/verify_runtime_identity.py`,
**lecture seule** (aucune écriture de schéma, de rôle ou de donnée) :

| Contrôle | Écart signalé |
|---|---|
| TLS réellement actif | `tls_inactive` (connexion refusée si TLS exigé) |
| rôle runtime ≠ superutilisateur | `runtime_role_is_superuser` |
| rôle runtime sans droit DDL | `runtime_role_can_create_schema_objects` |
| rôle attendu | `unexpected_role:<nom>` |
| révision Alembic | `alembic_version_missing` |
| RLS activé + forcé + policy, sur 9 tables | `rls_disabled:*`, `rls_not_forced:*`, `rls_no_policy:*` |

Le DSN n'est **jamais** un argument : il vient d'une variable d'environnement,
donc il ne finit pas dans l'historique du shell. Le rapport ne contient ni DSN,
ni mot de passe, ni hôte.

**Tests exécutés.** `backend/tests/test_a10_runtime_identity.py` — **9 passed,
1 skipped**, sur un cluster PostgreSQL 16 réel avec rôles jetables :

| Test | Résultat |
|---|---|
| schéma conforme → aucun écart RLS | passé |
| rôle superutilisateur détecté | passé |
| rôle applicatif sans privilège DDL | passé |
| rôle inattendu signalé | passé |
| base non migrée → écart, **pas** de plantage | passé |
| TLS exigé par défaut (port fermé) | passé |
| DSN absent → aucune cible inventée | passé |
| DSN jamais présent dans le rapport | passé |
| production refuse une URL non chiffrée | passé |
| **TLS réel sur vraie URL Neon** | **SKIPPED** — `VERICLAIM_STAGING_RUNTIME_URL` absente |

Deux défauts trouvés par ces tests, tous deux réels : une base sans
`alembic_version` faisait **planter** le vérificateur (trace brute au lieu d'un
rapport d'écart), et une base injoignable produisait également une trace. Les
deux sont corrigés : la connexion est en autocommit et les erreurs deviennent
des écarts nommés.

**Preuve.** `pytest backend/tests/test_a10_runtime_identity.py` → 9 passed,
1 skipped. Le skip **est** le résultat honnête : la preuve qui compte n'est pas
exécutable ici.

**Ce qui reste bloqué.** Exécuter, **par un membre de l'équipe** (qui a l'accès
Neon) :

```bash
export DATABASE_RUNTIME_URL='postgresql://vericlaim_app@<hôte>/<base>?sslmode=require'
python tools/runtime_check/verify_runtime_identity.py --expected-role vericlaim_app --json
```

Tant que la sortie n'est pas rapportée, la ligne « TLS réel actif » reste
**non prouvée**. Aucun rôle, privilège ou policy Neon n'a été modifié — voir
`03` §0 : l'accès appartient à l'équipe, pas à l'agent.

---

## A-11 — Import d'un PDF volumineux refusé par la limite par défaut

**Sévérité : P2 (paramétrage, pas défaut).**
**Statut : atténué (limite rendue explicite).**

**Observation.** Un PDF scanné de bonne qualité dépassait `MAX_UPLOAD_BYTES`
(2 Mo par défaut) et l'import échouait en **422** — comportement correct, mais
surprenant : un utilisateur qui photographie un document reçoit « fichier trop
volumineux » sans indication de la limite.

**Traitement.** Le contrat `GET /api/v1/auth/status` publie désormais
`document_upload_max_bytes`, afin que l'interface affiche la limite **avant** le
dépôt. Le harnais relève la limite à 8 Mo pour ses fixtures.

**Reste à faire.** Fixer la limite de recette en fonction des usages réels
(téléphone, 300 dpi) et l'afficher dans l'interface.

---

---

## A-12 — Le job CI de recette ne pouvait pas s'installer

**Sévérité : P1 (la preuve n'était pas reproductible en intégration continue).**
**Statut : corrigé.**

**Observation.** Premier run de `recette-qualification` sur la PR #14 : **échec**
à l'étape « Install the application and the recette harness », donc *avant* tout
test. Les étapes suivantes ont été `skipped` et l'artefact n'a pas été produit.

**Accès aux logs.** L'API GitHub ne m'a **pas** rendu les logs de ce run
(`EOF` sur les trois endpoints de téléchargement). Je n'ai donc pas lu le message
d'erreur. La cause a été établie autrement, et de façon certaine, à partir de
l'index PyPI :

```
pgserver 0.1.4 → wheels cp39, cp310, cp311, cp312 (16 fichiers)
                 aucune source distribution
job CI        → python-version: "3.13"
```

Il n'existe **aucun** fichier installable pour cp313 : la résolution échoue, par
construction, quel que soit l'état du code applicatif. La même requête montre que
`tesserocr` publie bien du cp313 — le problème est propre à `pgserver`.

**Correction.**
1. `python-version: "3.12"` pour ce job — **vérifié cohérent avec
   `backend/Dockerfile` (`FROM python:3.12-slim`)** : le job teste donc exactement
   l'interpréteur du runtime déployé. Les deux autres jobs restent en 3.13, ce qui
   est sans risque : ils n'installent pas `pgserver` (vérifié — seule l'étape de
   recette l'ajoute) ;
2. une étape « Verify the harness dependencies really imported » qui importe
   `pgserver` et `moto.server` explicitement : un échec de résolution est
   rapporté avec un message clair au lieu de faire tomber toutes les étapes
   suivantes sur un `ImportError` indistinct.

**Preuve.** Run `43325846167` : les 4 jobs sont `success`, et pour le job de
recette les étapes 5 (« Verify the harness dependencies… »),
6 (« Run the end-to-end recette journey ») et 7 (« Prove a backup can actually
be restored ») sont **toutes `success`**.

**Ce que cela change dans ce dossier.** Jusqu'à ce run, « 54/54 » et la
restauration étaient des preuves **locales**. Elles sont maintenant exécutées
aussi en intégration continue, sur un runner propre. C'est exactement la raison
d'être de ce job — et il a immédiatement trouvé un défaut, dans le harnais
lui-même.

---

## A-13 — Échec CI intermittent du parcours de recette

**Sévérité : P0 (décision propriétaire, 13 novembre 2026).**
**Statut : CORRIGÉE le 17 novembre 2026 — cause première démontrée.**
**Preuve et détail : `15-a13-cause-premiere-et-correctif.md`.**

**Observation.** Sur six runs du job `recette-qualification` au même commit
applicatif, **deux ont échoué** et quatre ont réussi. Les deux échecs se
produisent à l'étape « Run the end-to-end recette journey ».

**Ce que les horodatages prouvent.** En comparant les étapes :

| Run | Étape 6 (parcours) | Résultat |
|---|---|---|
| `43327153607` | 23:15:19 → 23:15:38 (**19 s**) | success |
| `43326500112` | 23:10:47 → 23:11:04 (**17 s**) | success |
| `43326928526` | 23:13:37 → 23:13:44 (**7 s**) | **failure** |
| `43326090494` | 23:08:06 → 23:08:15 (**9 s**) | **failure** |

Un parcours complet dure 17–19 s. Les échecs s'arrêtent après 7–9 s : ce n'est
donc **pas** une expiration de délai, c'est une **exception levée tôt** — et le
harnais n'en publiait que « exit code 1 ».

**Ce qui est exclu par l'observation.**
- Ce n'est pas le code applicatif : les runs verts et rouges portent des SHA
  dont les seules différences sont documentaires, et trois exécutions locales
  consécutives donnent 54/54.
- Ce n'est pas la contention CPU : deux exécutions locales avec deux boucles
  occupant les deux cœurs donnent 54/54.
- Ce n'est pas le défaut A-12 (installation) : l'installation réussit dans tous
  les runs concernés, et l'étape 5 de contrôle d'import passe.
- Ce n'est pas un manque de moteur OCR : le parcours accepte explicitement
  l'échec contrôlé de l'étape 31.

**Ce que je ne peux pas faire.** Lire les logs : les URL signées des blobs
GitHub Actions sont refusées depuis mon environnement (`EOF`), et `gh run
rerun` / `workflow_dispatch` sont à 403. Je ne peux donc pas voir la trace
Python de l'échec.

**Ce qui a été fait à la place** — rendre le prochain échec explicite :
1. le parcours dépose des **marqueurs horodatés** à chaque étape et à chaque
   phase non vérifiée ;
2. une exception hors étape est nommée (type + message) **et** localisée par la
   dernière progression, en annotation ;
3. la console est capturée (`tee`) et sa fin réémise en annotations ;
4. les étapes en échec sont répétées en annotations.

Vérification de l'instrumentation par injection volontaire d'une exception :
`RuntimeError: connexion réinitialisée par le pair | dernière progression:
fixtures generated > healthz répond`.

**Conséquence sur nos affirmations.** Tant que A-13 n'est pas expliqué, je ne dis
pas « la CI est verte » : je dis « la CI a été verte sur les runs cités, et elle
échoue encore de façon intermittente sur le job de recette ».

**Décision du propriétaire (13 novembre 2026) : NE PAS FUSIONNER.** A-13 passe
en **P0** : au-delà de l'instabilité du test, un import a répondu **HTTP 201
sans écriture durable** (run `43344842829`, aucune ligne même pour le rôle admin).
Détail et voies de déblocage : `14-diagnostic-a13.md` §7. Les documents
`11` et `13` portent cette nuance.

**Résolution (17 novembre 2026) — cause première démontrée, corrigée, testée.**

La cause n'était ni le RLS, ni le contexte tenant, ni la lenteur : **le code HTTP
était envoyé avant la validation de la transaction**. Avec FastAPI 0.163.1 /
Starlette 1.10.0, une dépendance `yield` est démontée *après* l'envoi de la
réponse ; le commit de `get_db` s'exécutait donc une fois le client déjà servi.
Un échec de validation ne pouvait plus changer le 201.

Correctif : `Depends(get_db, scope="function")` sur toutes les routes, ce qui
rétablit l'ordre `routeur → commit → réponse`. Le même défaut expliquait les 409
« Aucune organisation active », 404 « Produit introuvable » et 401 après
connexion SSO, reproduits **systématiquement** en limitant l'exécution à un seul
cœur. `resolve_active_membership` n'efface plus l'organisation active sur une
lecture en erreur ou incohérente, et trace les causes durables.

Preuves : tests de non-régression échouant sans correctif, E2E 54/54 sous
contention d'un cœur (4 séries), suite backend 249 passed, PostgreSQL réel 27
passed, CI 4/4 verte sur `a73e1d5` (run `44081515104`). Détail complet :
`15-a13-cause-premiere-et-correctif.md`.

**Analyse complémentaire (13 novembre) — corrélation temporelle.** Une exécution
locale instrumentée situe les phases du parcours :

| Phase | Fenêtre locale |
|---|---|
| démarrage de la pile, fixtures, serveur d'API | T+0 → T+5,2 s |
| imports (dont EICAR, 9 Mo refusés) | T+5,2 → T+5,5 s |
| **worker d'extraction (étape 29)** | **T+5,5 → T+7,0 s** |
| segments, **puis PDF scanné / OCR (étape 31)** | T+7,0 → **T+8,7 s** |
| téléchargements, analyse, rapports | T+8,7 → T+13 s |
| redémarrage et contrôle final | T+13 → T+16,8 s |

Les deux échecs CI s'arrêtent à **7 s** et **9 s**, c'est-à-dire **exactement**
sur ces deux phases-là, et sur aucune autre. La corrélation est donc forte :
l'échec se produit au moment où un processus worker est lancé puis arrêté, et à
l'étape qui dépend du moteur OCR.

Deux précisions honnêtes :
- le runner GitHub n'a probablement **pas** de binaire `tesseract` (aucune étape
  ne l'installe), donc l'étape 31 doit passer par la branche « échec contrôlé ».
  Le harnais **accepte explicitement** cet échec, donc il ne devrait pas lever ;
- une corrélation n'est pas une cause : rien ici n'explique encore *pourquoi* ces
  deux phases lèveraient.

**Prochaines étapes.** Le prochain échec produira une annotation contenant le
type d'exception et l'étape exacte (instrumentation vérifiée par injection), ce
qui tranchera entre : port pris entre réservation et écoute, worker sorti en
erreur, ou exception dans `_job_state` (accès base pendant la transition).

---

## A-14 — Le harnais laissait un serveur orphelin après toute exception

**Sévérité : P1 (état reporté d'un run à l'autre).**
**Statut : corrigé, avec preuve.**

**Observation.** Sur quatre exécutions locales, un processus `uvicorn` restait
vivant après chacune. Mesure après nettoyage : **zéro** orphelin pour deux
exécutions successives ; avant correctif, exactement **un par exécution**.

**Cause vérifiée.** `RecetteRun.execute` n'avait **aucun `try/finally`** autour du
serveur : `self.server.stop()` n'était appelé que sur le chemin heureux. Une
exception — dans une étape, dans un worker, dans un rendu de rapport — laissait
donc le serveur en vie, avec sa connexion PostgreSQL ouverte et son port occupé.
`ApiServer.stop()` ne fermait pas non plus le descripteur du journal.

**Pourquoi c'est grave.** C'est exactement la classe d'état qui fait échouer un
run **ultérieur** de façon apparemment aléatoire : port occupé par un processus
d'un run précédent, connexions résiduelles, files de jobs partagées.

**Corrections.**
1. `try/finally` dans `execute()` : serveur arrêté et navigateurs fermés quelle
   que soit la sortie ;
2. `stop()` ferme le descripteur du journal ;
3. `stop()` **attend la libération effective du port** et échoue sinon, au lieu de
   supposer que `terminate()` suffit ;
4. `running()` expose l'état du processus.

**Preuves.**
- `backend/tests/test_recette_harness_hygiene.py` (2 tests). Le plus important
  injecte une exception en milieu de parcours après avoir démarré un vrai serveur,
  puis vérifie que le processus est arrêté **et** que le port est re-libérable.
  **Vérifié en échec sans le `finally`** (assertion sur `run.server is None`), vert
  avec.
- 10 parcours consécutifs : **54/54** à chaque fois, **0 orphelin**.

**Lien avec A-13, énoncé sans le surestimer.** Ce défaut est un **mécanisme
plausible** de l'intermittence : un orphelin d'un run peut gêner le suivant. Il est
désormais impossible, et 10 runs consécutifs passent. Cela ne **prouve** pas que
A-13 était causée par là — les échecs CI observés étaient sur des runners neufs,
où aucun orphelin préalable n'existe. A-13 reste donc ouverte.

## Ce qui n'a pas été testé du tout

Pour éviter toute ambiguïté :

- parcours navigateur réel sur la Preview (SSO + réseau) ;
- vrai fournisseur OIDC externe ;
- vrai antivirus ;
- vrai Neon, vraies sauvegardes Neon, vrai Vercel à l'exécution ;
- charge, performance, latence, montée en charge ;
- comportement multi-workers concurrents sur la même file ;
- restauration d'une base de production.

Ces éléments sont repris comme limites dans `13-verdict-recette-exploitable.md`.
