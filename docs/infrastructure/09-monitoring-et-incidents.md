# 09 — Monitoring, logs et gestion d'incidents

**Date :** 12 novembre 2026
**SHA testé :** `c73b12fd663e744dcda153bc35b0166fb977feaa` (+ branche mission 4 pour A-09)

## 1. Ce qui existe réellement

| Mécanisme | État | Détail |
|---|---|---|
| `/healthz` | **existe** | prouve que le processus répond, rien de plus (le code le dit explicitement) |
| `/readyz` | **existe, utile** | atteint réellement PostgreSQL, expose `document_storage` et `malware_scanner` comme `configured`/`disabled`, **`malware_scanner_reachable`** (sonde `PING` réelle depuis A-07) et `document_import: enabled/refused` |
| handlers d'erreur ASGI | **existe** | base injoignable → **503** neutre, sans texte de driver ; traces côté serveur |
| logs applicatifs | **existe** | `logging` standard, `LOG_LEVEL`, messages structurés avec `extra` (job, organisation) |
| journal d'audit | **existe, robuste** | `audit_events` append-only, chaîne de hachage, `GET /api/v1/audit/verify` |
| métriques (latence, profondeur de file, taux d'erreur) | **présentes depuis A-09** | `GET /metrics` (format Prometheus), middleware HTTP, profondeur de file lue **en base** |
| alerting | **configuré, jamais observé** | 9 règles dans `infra/monitoring/alerts.yml`, 5 runbooks ; **aucun** Prometheus/Alertmanager n'a tourné, **aucune** alerte n'a été vue se déclencher |
| suivi d'erreurs frontend | **absent** | aucun Sentry/équivalent |
| agrégateur de logs | **absent du dépôt** | dépend de la plateforme (Vercel/Docker) |

**Conséquence, corrigée en partie seulement.** Un incident est *traçable*
(logs + audit + `/readyz`) et, depuis A-09, *mesurable* (`/metrics`) et
*descriptible* (règles d'alerte avec cause et runbook). Il n'est toujours **pas
alerté** : le chemin « métrique → collecteur → notification » n'existe nulle
part dans cet environnement. Personne n'est encore réveillé par un worker
arrêté — mais la métrique qui permettrait de le réveiller existe et est testée.

## 2. Preuves

| # | Contrôle | Observation |
|---|---|---|
| 1 | `/healthz` | **200** |
| 2 | `/readyz` | **200**, `database: ok`, `document_storage: configured`, `malware_scanner: configured`, `malware_scanner_reachable: true`, `document_import: enabled` |
| 3 | `/readyz` avec base absente | **503** `status: unavailable` (couvert par les tests existants) |
| 4 | requête API avec base absente | **503** message neutre, pas de repli SQLite |
| 5 | chaîne d'audit | **`is_valid: true`**, 14 événements après le parcours A |
| 6 | trace d'une exception applicative | traceback complet dans le journal du serveur (observé pendant le diagnostic du 500 d'import, voir `12-registre-anomalies.md` A-01) |
| 7 | événements d'audit métier | `membership.activated_by_sso`, `document.upload_clean`, `analysis.claim_detection_queued`, etc., chaînés |

Le journal d'audit est le point fort : chaque opération critique laisse une ligne
infalsifiable (chaîne de hachage, `previous_event_hash`), et le rôle runtime **n'a
pas le droit de la modifier** (`GRANT SELECT, INSERT` seulement — vérifié,
`has_table_privilege(…,'UPDATE') = false`).

## 3. Ce que les journaux ne contiennent pas

Vérifié par lecture du code et par observation des journaux produits durant le
parcours :

- aucun mot de passe, aucun jeton de session, aucun jeton CSRF ;
- aucun jeton OIDC ni code d'autorisation (le fournisseur de test ne journalise
  volontairement rien) ;
- aucun DSN ni chaîne de connexion ;
- aucun contenu documentaire ;
- les erreurs de stockage/scan/journalisation renvoient un message générique.

Points d'attention résiduels :

- les logs d'accès Uvicorn sont désactivés dans le harnais (`--no-access-log`) ;
  en production, les URLs signées peuvent apparaître dans un journal d'accès si un
  proxy les journalise. **Ne jamais journaliser les query strings des URLs
  présignées** (elles contiennent une signature valable).
- `logger.exception` sur une erreur inattendue peut inclure un message d'exception
  tiers ; c'est acceptable côté serveur, pas dans une réponse client (déjà le cas).

## 4. Alertes minimales à définir (aujourd'hui absentes)

| Alerte | Seuil proposé | Source possible | Responsable |
|---|---|---|---|
| Application indisponible | `/readyz` ≠ 200 sur 2 sondes consécutives | sonde externe | infra |
| Erreurs API répétées | > 1 % de 5xx sur 5 min | logs / agrégateur | infra |
| Base injoignable | `database: unavailable` | `/readyz` | infra |
| Worker arrêté | aucun job traité depuis N minutes alors que la file > 0 | requête SQL sur `*_jobs` | infra |
| Échec antivirus | `scan_error_code` non nul en hausse | table `document_uploads` | sécurité |
| Échec OCR | `extraction_error_code = extraction_unusable` en hausse | `document_versions` | produit |
| Stockage indisponible | `document_import: refused` | `/readyz` | infra |
| Saturation | profondeur de file croissante, latence de job | métrique à créer | infra |

**Aucune de ces alertes n'existe.** Le document 13 en fait une condition du pilote.

## 5. Procédure d'incident (proposée, à valider)

Parce qu'une procédure non testée est une intention, chaque étape indique comment
elle a été vérifiée ou pourquoi elle ne l'a pas été.

1. **Détecter** — `/readyz` ≠ 200, ou alerte, ou utilisateur.
   *Vérifié* : `/readyz` renvoie bien 503 quand la base manque.
2. **Qualifier** — `GET /readyz` pour distinguer : base, stockage, antivirus.
   *Vérifié* : les trois états sont exposés séparément.
3. **Localiser** — journal du processus : les erreurs non gérées y sont tracées
   avec pile.
   *Vérifié* : traceback complet obtenu lors du diagnostic A-01.
4. **Mesurer l'impact métier** — `GET /api/v1/audit/verify` (chaîne intacte ?) et
   comptage des jobs bloqués.
   *Vérifié* : `is_valid: true`, comptage SQL direct possible.
5. **Contenir** — si le stockage est indisponible, les imports renvoient 503 et
   **rien n'est promu** ; si un worker est arrêté, les files ne perdent rien.
   *Vérifié* : comportement fail-closed observé.
6. **Corriger** — redémarrer le service concerné ; une base revenue réactive les
   requêtes sans redéploiement (`pool_pre_ping`).
   *Partiellement vérifié* : redémarrage complet du processus testé ; reprise d'une
   base arrêtée non testée.
7. **Traiter les jobs bloqués** — retry explicite par document/analyse, ou attente
   du bail.
   *Vérifié* : route de retry existante et testée unitairement.
8. **Capitaliser** — consigner dans le registre d'anomalies.

### Ce qui manque à cette procédure pour être « opérationnelle »

- pas d'astreinte ni de canal d'alerte ;
- pas de tableau de bord ;
- pas d'accès aux logs de la Preview depuis mon environnement (voir `04` §3) ;
- pas de test de bascule (baisse/base secondaire) : Neon gère la disponibilité, mais
  rien n'est documenté côté VeriClaim.

## 6. Métriques et alertes livrées par A-09

### 6.1 Séries exportées (`GET /metrics`)

| Série | Type | Ce qu'elle permet de distinguer |
|---|---|---|
| `vericlaim_http_requests_total{method,route,status}` | counter | taux d'erreur **par point d'entrée** (gabarit de route, jamais l'URL) |
| `vericlaim_http_request_duration_seconds_bucket` | histogram | p95 de latence |
| `vericlaim_database_up` | gauge | base joignable **depuis ce processus** |
| `vericlaim_scanner_reachable` | gauge | moteur antivirus joignable (sonde `PING`) |
| `vericlaim_document_queue_queued` / `_running` / `_failed` | gauge | profondeur de file par état |
| `vericlaim_document_queue_oldest_queued_seconds` | gauge | **retard**, pas seulement volume |
| `vericlaim_analysis_queue_*` | gauge | idem pour l'analyse |
| `vericlaim_organizations_active` | gauge | périmètre servi |
| `vericlaim_uptime_seconds` | gauge | redémarrages |

Les deux séries de file sont lues **en base** à chaque scrap : elles restent
exactes quand aucun worker ne tourne, c'est-à-dire quand l'information compte.

`/healthz`, `/readyz` et `/metrics` sont **exclus** de la mesure : un superviseur
qui interroge toutes les 10 s fausserait sinon le taux d'erreur et la latence.

### 6.2 Règles (`infra/monitoring/alerts.yml`)

| Alerte | Seuil | Sévérité |
|---|---|---|
| `VeriClaimApiDown` | `up == 0` pendant 2 min | critical |
| `VeriClaimDatabaseDown` | `vericlaim_database_up == 0` pendant 1 min | critical |
| `VeriClaimHigh5xxRate` | > 2 % de 5xx sur 5 min | warning |
| `VeriClaimApiLatencyHigh` | p95 > 2,5 s sur 10 min | warning |
| `VeriClaimDocumentQueueBacklog` | > 20 jobs en attente sur 10 min | critical |
| `VeriClaimDocumentJobStalled` | job en attente depuis > 600 s | critical |
| `VeriClaimAnalysisQueueBacklog` | > 20 jobs en attente sur 10 min | warning |
| `VeriClaimScannerUnreachable` | `scanner_reachable == 0` sur 3 min | critical |
| `VeriClaimWorkerHeartbeatStale` | battement > 120 s | critical |

Chaque règle porte une **cause probable** et un **runbook** ; les tests
vérifient que le fichier de runbook existe réellement sur disque.
Runbooks : `docs/runbooks/`.

### 6.3 Les trois niveaux, tenus séparés

| Niveau | État | Preuve |
|---|---|---|
| Instrumentation présente | **OUI** | 8 tests A-09, séries vérifiées dans la réponse HTTP |
| Alertes configurées | **OUI** | YAML valide, seuils testés au-dessus/en dessous, runbooks existants |
| **Alertes observées** | **NON** | aucun Prometheus/Alertmanager déployé ; **aucune alerte n'a été reçue ni vue se déclencher** |

Les tests de seuils rejouent la logique de comparaison sur des valeurs choisies ;
ce n'est **pas** un Prometheus réel. Le chemin de notification n'a jamais été
exercé de bout en bout.

## 7. Verdict

- **Traçabilité : oui.** Logs, `/readyz`, journal d'audit infalsifiable, codes
  d'erreur stables et rejouables.
- **Instrumentation : oui depuis A-09.** Métriques réelles, testées, dont la
  profondeur et le retard de file lus en base.
- **Détection proactive : non.** Les alertes sont **écrites**, pas **branchées**.
  Un worker arrêté produit désormais une métrique claire, mais personne n'est
  encore notifié. Condition explicite du pilote, non satisfaite à ce jour.
- La procédure d'incident est proposée, partiellement vérifiée et désormais
  associée à des runbooks par alerte ; elle n'est pas outillée.
