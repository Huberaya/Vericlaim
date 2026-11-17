# 08 — Workers et traitements asynchrones

**Date :** 12 novembre 2026
**SHA testé :** `c73b12fd663e744dcda153bc35b0166fb977feaa` (+ branche mission 4 pour A-06)

## 1. Deux traitements asynchrones, tous deux durables

| Traitement | Module | Table de file | Point d'entrée |
|---|---|---|---|
| Extraction documentaire (PDF, OCR) | `app/workers/document_extraction_worker.py` | `document_extraction_jobs` | `build_document_extraction_runtime()` → `WorkerRuntime.run()` |
| Détection déterministe d'allégations | `app/workers/analysis_detection_worker.py` | `analysis_detection_jobs` | `build_analysis_detection_runtime()` → `WorkerRuntime.run()` |

`run_forever()` est conservé pour compatibilité mais **délègue** au runtime : il
n'y a plus deux façons de faire tourner un worker.

**Aucune file externe** : pas de Redis, pas de RabbitMQ, pas de Celery. La file est
en PostgreSQL, ce qui rend l'`enqueue` transactionnel avec la promotion du
document. C'est un choix assumé et cohérent ; il n'y a donc pas de DLQ externe.

### Propriétés implémentées

- **verrou par bail** (`lease`) avec échéance, pas de verrou de ligne longue ;
- **fencing** : un worker qui a perdu son bail ne peut pas publier de résultat
  (`DocumentExtractionConflictError` / `AnalysisConflictError` → rollback) ;
- **tentatives bornées** (`*_MAX_ATTEMPTS`) avec **backoff** exponentiel
  (`*_RETRY_BASE_SECONDS`) ;
- **idempotence** : clé d'idempotence par tenant, empreinte de requête, rejeu
  explicite détecté et compté ;
- **contexte tenant obligatoire** avant toute lecture (RLS force le filtre) ;
- **répartition équitable** entre organisations (`round_robin_organization_ids`) ;
- **reprise après redémarrage** : l'état vit en base, un job interrompu est
  récupérable à l'expiration de son bail ;
- **nettoyage d'uploads abandonnés** dans la passe d'extraction (borné, intervalle
  minimal par organisation) ;
- **événements d'audit** append-only avec acteur `NULL` pour un worker.

Le code documente finement ces points, notamment la récupération de bail et
l'interdiction d'écraser une décision publiée par un autre worker.

## 2. Preuves exécutées

Le harnais lance les workers comme **processus séparés** (comme Compose), observe
l'état en base et les arrête une fois le travail terminé.

| # | Test | Attendu | Observé |
|---|---|---|---|
| 1 | lancement d'un job d'extraction | job réclamé et terminé | `job=completed`, `extraction_status=completed`, `segments=1` |
| 2 | succès complet | document lu | conforme |
| 3 | échec terminal (OCR absent) | échec explicite, pas de faux succès | `failed`, `extraction_unusable` |
| 4 | retry | job rejouable | `POST /document-versions/{id}/extraction/retry` (couvert par la suite) |
| 5 | worker indisponible | la file ne bouge pas | **`pending` / `queued` conservés après 3 s sans worker** |
| 6 | worker arrêté | aucun succès fabriqué | idem — aucune transition |
| 7 | contexte tenant | worker ne voit que l'organisation courante | lecture sans contexte → **0 ligne visible** |
| 8 | analysis worker | allégations détectées et persistées | `job=completed`, **3 claims**, `status=completed` |
| 9 | deux organisations servies | round-robin | A puis B traités dans la même exécution |
| 10 | redémarrage complet | état conservé | document et session intacts après relance |

### Preuve textuelle

```
worker d'extraction : statut=completed segments=1 job=completed
worker de détection : job=completed claims=3 statut=completed
worker arrêté       : statut conservé=pending job=queued (aucune promotion)
lecture sans contexte tenant : 0 ligne visible
```

Le point 3 et le point 6 sont la démonstration demandée par la mission : **un
traitement asynchrone ne produit jamais un résultat sans worker, et un échec est
un échec.**

## 3. Le point dur : aucune exécution périodique en Preview

`vercel.json` ne déclare **aucun `crons`**. Les deux workers n'exposent que
`run_forever()`. `docker-compose.yml` les lance comme services séparés (c'est le
déploiement de référence, celui qui fonctionne). Un déploiement Vercel, en
revanche, ne peut pas faire tourner de processus long.

**Conséquence** : sur la Preview, un import reste `pending` et une analyse reste
`queued`. Le parcours métier s'arrête après l'upload. C'est l'anomalie **A-06**
(P1) du registre.

Trois options, à trancher :

1. **exécution planifiée** (`crons` Vercel ou planificateur externe) appelant une
   route idempotente qui exécute `run_once()` un nombre borné de fois ;
2. **service worker** hors Vercel (conteneur, Fly/Render/ECS) avec la même image ;
3. **worker asynchrone managé** (file externe + runtime adapté).

L'option 1 est la moins intrusive pour une recette, mais elle impose de modifier le
contrat de l'application (une route de traitement, protégée et idempotente). Ce
n'est pas une décision que ce chantier peut prendre seul : elle touche
l'architecture et le périmètre de l'agent 1.

## 4. Isolation entre organisations

Deux barrières indépendantes :

1. les workers posent `app.current_organization_id` avant chaque lecture, donc le
   RLS PostgreSQL filtre ;
2. les requêtes applicatives portent `organization_id` explicitement.

Vérifié : une lecture des tables tenant **sans** contexte ne renvoie aucune ligne
(`rows_visible_without_tenant_context: 0`), y compris pour `document_versions` et
`analysis_detection_jobs`. Un job de A ne peut donc pas lire les segments de B.

## 5. Limites

| Limite | Priorité | Commentaire |
|---|---|---|
| Aucun déclencheur sur Vercel | **P1** | voir §3 |
| Aucune métrique de file (profondeur, âge du plus vieux job) | P2 | seulement des logs |
| Aucune alerte si un worker s'arrête | **P1** pour un pilote | rien ne signale l'arrêt |
| Pas de test de concurrence à deux workers sur la même file dans ce harnais | P2 | couvert par `test_postgres_purge_concurrency.py` sur les purges, pas sur l'extraction |
| Pas de reprise testée après *kill -9* en cours d'extraction | P2 | le mécanisme de bail le permet, non exécuté ici |

## 6. Verdict

- Les traitements asynchrones **fonctionnent réellement** quand un processus worker
  tourne : jobs réclamés, travail fait, état persisté, contexte tenant respecté.
- Ils **ne fonctionnent pas** sur un hébergement sans processus long — c'est un fait
  d'architecture, vérifié par contraste, et c'est le principal blocage du pilote sur
  la Preview.
- Un worker absent **ne fabrique pas** de succès : la file reste en attente.

## 7. Mode de déploiement de recette (A-06)

### 7.1 Commande

```bash
# worker documentaire (OCR, PDF)
python -m app.workers.document_extraction_worker

# worker d'analyse déterministe
python -m app.workers.analysis_detection_worker
```

Les deux processus sont **distincts** : une extraction OCR longue ne doit pas
retarder la détection d'allégations, et inversement.

### 7.2 Variables

| Variable | Effet |
|---|---|
| `DOCUMENT_WORKER_POLL_SECONDS` / `ANALYSIS_WORKER_POLL_SECONDS` | intervalle d'inactivité |
| `DOCUMENT_WORKER_CONCURRENCY` / `ANALYSIS_WORKER_CONCURRENCY` | boucles parallèles ; chaque boucle prend un `worker_id` **distinct** (`base#1`, `base#2`…), exigé par le fencing |
| `*_WORKER_HEALTH_FILE` | chemin du fichier de santé publié |
| `*_LEASE_SECONDS` | durée du bail ; un job abandonné redevient réclamable après expiration |

### 7.3 Santé et supervision

Le worker publie atomiquement un JSON (`heartbeat_at`, `iterations`,
`jobs_processed`, `last_error`, `stopping`). Deux consommateurs :

```bash
# sonde de conteneur (sortie 0 = sain, 1 = non sain)
python -m app.worker_healthcheck --file /run/vericlaim/document-worker.json --max-age 120

# métrique d'âge, pour l'alerte VeriClaimWorkerHeartbeatStale
python infra/monitoring/worker_heartbeat_exporter.py /run/vericlaim/document-worker.json
```

Docker :

```yaml
healthcheck:
  test: ["CMD", "python", "-m", "app.worker_healthcheck", "--file", "/run/vericlaim/document-worker.json"]
  interval: 30s
  timeout: 5s
  retries: 3
```

systemd : `ExecStartPost` la sonde, `Restart=always`, `KillSignal=SIGTERM` — le
runtime intercepte SIGTERM, cesse de réclamer, laisse finir les itérations en
cours (borné par le délai de drain) puis sort en 0.

### 7.4 Ce qui est **prouvé**, et ce qui reste à faire

**Prouvé (8 tests, `backend/tests/test_worker_runtime_lifecycle.py`)** : arrêt
sur signal, drain borné, battement publié et lu, battement périmé détecté, sonde
sortant 0/1, concurrence avec `worker_id` distincts, itération en erreur non
fatale, `max_iterations` pour un usage ponctuel.

**Reste à faire, par l'équipe — le code ne déploie rien :**

1. exécuter les deux workers sur un runtime à **processus long** (conteneur, VM,
   service managé). Un environnement serverless ne peut pas les héberger ;
2. monter `/run/vericlaim` sur un volume partagé avec le superviseur ;
3. décider du cas « Vercel seul ». Trois options, **aucune tranchée** : cron
   appelant une route idempotente (qui **n'existe pas** et n'a pas été ajoutée),
   worker hors Vercel (recommandé pour un pilote), ou file managée.

### 7.5 Sur `vercel.json`

Aucune clé `crons` n'a été ajoutée. Ajouter une planification vers une route
inexistante aurait produit un échec silencieux toutes les N minutes — pire que
l'absence actuelle, qui est au moins visible.
