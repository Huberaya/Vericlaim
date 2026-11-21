# Revue de sécurité et de qualité — rapport d'écarts

**Date :** 21 novembre 2026
**Base auditée :** `6f2cadee98fcbc1359b4c440b0c5581580a9346b` (branche `main`, après fusion de la PR #16)
**Périmètre :** revue ciblée auth/RLS, ingestion documentaire, stockage, workers, exports, frontend, configuration, dépendances
**Méthode :** revue statique du code exécuté au chemin des requêtes, puis vérification dynamique (suite de tests, audit des dépendances)
**Nature :** aide à la conformité technique. Ce document n'est ni une certification, ni un audit d'intrusion, ni un avis juridique.

---

## Règle de lecture

Chaque écart est **observé** (reproduit ou constaté dans le code livré), pas déduit. Trois états,
comme dans le registre des anomalies : **corrigé dans cette mission** (correction appliquée et
testée ici), **ouvert** (constaté, recommandation proposée, aucune modification portée), ou
**informationnel** (faiblesse résiduelle assumée ou dette documentée, à décider). Les points
forts sont listés séparément : un rapport d'écarts qui ne dit pas ce qui tient ne sert à rien.

---

## Synthèse

| # | Écart | Sévérité | État |
|---|---|---|---|
| E-01 | Pillow 12.3.0 : 42 CVE connues ; la contrainte `Pillow>=11,<13` exclut la version corrigée | **haute** | **corrigé** (mission précédente) |
| E-02 | Aucun rate limiting effectif, alors que le modèle en prévoit un (`rate_limit_per_minute`) | moyenne | **corrigé dans cette mission** |
| E-03 | Clés API créables mais jamais acceptées comme mode d'authentification | moyenne | **corrigé dans cette mission** |
| E-04 | En-têtes de sécurité HTTP absents (nosniff, HSTS, frame-ancestors, Referrer-Policy) | faible | **corrigé dans cette mission** |
| E-05 | `/docs`, `/openapi.json` et `/metrics` exposés sans restriction | faible | **corrigé dans cette mission** |
| E-06 | Code mort après `return` dans `resolve_active_membership` | faible (qualité) | **corrigé** (mission précédente) |
| E-07 | `user_agent_hash` enregistré à la création de session, jamais vérifié | faible (qualité) | **corrigé dans cette mission** |
| E-08 | Double format d'empreinte accepté pour le scellement de la chaîne d'audit | informationnel | **corrigé dans cette mission** |
| E-09 | **Divergence schéma migré / modèle ORM sur `api_keys` et `legal_holds`** — la fonctionnalité échoue sur toute base migrée | **haute** | **corrigé dans cette mission** (trouvé pendant le traitement de E-03) |

Aucun écart de classe « critique » n'a été constaté sur les frontières examinées : les contrôles
d'authentification, de périmètre tenant, d'ingestion documentaire et de fencing des workers sont
en place et cohérents avec ce que les tests en prouvent (voir « Limites de cette revue »).

---

## E-01 — Pillow 12.3.0 : 42 CVE connues sur une bibliothèque de traitement d'images non fiables

**Sévérité : haute** (bibliothèque atteinte par des fichiers uploadés par un utilisateur, en
amont de l'OCR ; 42 advisories connus au moment de la revue).
**État : corrigé dans cette mission.**

**Observation.** `pip-audit -r backend/requirements.txt` remonte **42 vulnérabilités connues**
(PYSEC-2026-43xx, CVE-2026-96xxx, CVE-2026-112xxx, CVE-2026-128xxx, GHSA-78g4-xqwv-q3g8…)
contre `pillow 12.3.0`, toutes corrigées en **13.0.0**. Pillow est la bibliothèque qui ouvre et
vérifie les images (`_open_checked_image`, `app/engine/document_extractor.py`), c'est-à-dire
précisément la surface qui traite les octets d'upload avant OCR.

**Cause.** La contrainte `Pillow>=11,<13` dans `backend/requirements.txt` **exclut la ligne
corrigée** : une installation fraîche résout 12.3.0, la dernière version satisfaisant la
contrainte, et aucune mise à jour automatique ne peut atteindre le correctif.

**Correction.** `backend/requirements.txt` : `Pillow>=11,<13` → `Pillow>=13,<14`.

**Preuve.** Suite backend complète avec Pillow 13.0.0 installé : **295 passed / 64 skipped**
(identique à la baseline 12.3.0). Fumée manuelle du chemin image réel (`inspect_document_bytes`
+ `DocumentTextExtractor.extract_result` sur un PNG) : inspection conforme, chemin OCR dégradant
en erreur contrôlée (Tesseract absent du poste de revue), `Image.DecompressionBombWarning`
toujours présent et actif sous Pillow 13. `pip-audit` sur la nouvelle contrainte : 0 vulnérabilité.

**Limite.** La compatibilité a été prouvée par la suite de tests et un test manuel du chemin
image ; aucun test OCR complet (binaire Tesseract) n'a pu tourner sur le poste de revue. Les
64 tests skipés ici sont les tests qui exigent PostgreSQL, Tesseract ou un binaire de signature —
la CI `recette-qualification` doit confirmer le passage sur son runner complet.

---

## E-02 — Aucun rate limiting effectif malgré un modèle qui en prévoit un

**Sévérité : moyenne** (pas d'exploitation démontrée, mais aucune borne de débit nulle part).
**État : ouvert.**

**Observation.** Le modèle `ApiKey` porte un champ `rate_limit_per_minute` (défaut 120,
`app/enterprise/service.py`), mais **aucun mécanisme de limitation de débit n'exécute cette
valeur** : aucun middleware de throttling, aucune librairie (slowapi ou équivalent), aucun
compteur dans `app/main.py` ou les routes. Vérifié par recherche sur l'ensemble de `app/` :
`rate_limit` n'apparaît que dans le modèle et sa présentation.

Les surfaces concernées, par ordre de priorité :

1. `POST /api/v1/engine/evaluate` — traitement CPU/OCR lourd, atteignable par tout membre
   avec `audit:run` (le multipart déclenche extraction + OCR) ;
2. `POST /api/v1/auth/dev-login` et le flux OIDC — pas de limitation de tentatives côté applicatif ;
3. `POST /api/v1/documents/{id}/versions` et complétion — création d'uploads presignés, appels
   scanner/stockage ;
4. les lectures paginées généreuses.

**Recommandation.** Implémenter une limitation par (tenant, route) et par IP sur les routes
mutantes et sur `/evaluate`, avec un plafond par défaut et la valeur `rate_limit_per_minute`
des clés API comme plafond par clé si E-03 est tranché dans le sens de l'implémentation.
Ajouter au moins un test de non-régression (429 au-delà du seuil).

**Traitement (21/11/2026) — corrigé.** `app/core/rate_limit.py` : limiteur à fenêtre fixe en
mémoire (thread-safe) + middleware. Trois seaux cookie/anonymes (`API_RATE_LIMIT_AUTH_PER_MINUTE`=30,
`API_RATE_LIMIT_EVALUATE_PER_MINUTE`=30, `API_RATE_LIMIT_WRITE_PER_MINUTE`=240, bornés en config),
clé par jeton de session hashé ou IP ; les lectures GET et les pré-vols ne consomment rien.
Réponses 429 avec `Retry-After` et `X-RateLimit-*`. La limite **par clé API** est contractuelle :
toujours active, indépendante du commutateur global (`API_RATE_LIMIT_ENABLED`, off par défaut en
APP_ENV=test). Limite assumée : état par processus — avec N réplicas, le plafond réel est
multiplié (documenté au README). Preuve : `tests/test_e02_rate_limiting.py` (fenêtre glissante à
l'unité, refus 429 sur les seaux auth/évaluation, commutateur off respecté) + 429 par clé dans
`tests/test_e03_api_key_auth.py`.

---

## E-03 — Clés API créables mais jamais acceptées comme mode d'authentification

**Sévérité : moyenne** (fonctionnalité présentée à l'utilisateur mais sans portée technique ;
risque de fausse confiance).
**État : ouvert — décision produit requise.**

**Observation.** Le parcours entreprise permet de créer, lister et révoquer des clés API
(`POST /api/v1/enterprise/api-keys`, `ApiKeyCreateModal.tsx`) : préfixe `vc_live_`, 32 octets
d'entropie (`secrets.token_urlsafe(32)`), stockage sous empreinte SHA-256, périmètres, expiration,
journalisation d'audit. En revanche, **aucun chemin de code n'authentifie une requête par ces
clés** : recherche sur `app/` — aucun en-tête `X-API-Key`/`Authorization` consommé, aucune
lecture de `key_hash` pour vérifier une requête entrante. Toute l'API est authentifiée par la
session cookie OIDC (`require_permission`). Le champ `rate_limit_per_minute` est lui-même sans
effet (E-02).

**Risque.** Une organisation qui « tourne avec des clés API » croit disposer d'une intégration
machine scopée et limitée : elle ne dispose que d'un enregistrement en base. Inversement, si
l'intention est de les rendre opérantes, le chemin de vérification (comparaison en temps
constant de l'empreinte, prise en compte de l'expiration et de `is_active`, application des
scopes, mise à jour de `last_used_at`, refus CSRF cohérent) reste entièrement à écrire.

**Recommandation.** Décider explicitement : (a) implémenter l'authentification par clé API sur
un sous-ensemble versionné de l'API (avec tests de vérification d'empreinte, d'expiration, de
scopes et de révocation), ou (b) retirer/dégrader l'écran de création tant que le chemin
d'authentification n'existe pas, pour ne pas promettre une capacité absente.

**Traitement (21/11/2026) — corrigé, option (a).** `app/identity/api_keys.py` + intégration dans
`app/identity/dependencies.py` :

- **transport** — `X-API-Key: vc_live_...` ou `Authorization: Bearer vc_live_...` ; une requête
  qui présente une clé n'est jamais réauthentifiée par cookie (pas de bascule silencieuse) ;
- **vérification** — recherche par empreinte SHA-256 (temps constant sur la valeur), refus
  uniforme clé inconnue/révoquée/expirée/organisation inactive ; `last_used_at` tenu à jour ;
- **périmètres** — vocabulaire machine (`documents:*`, `catalog:*`, `evidence:*`, `audit:*`,
  `rules:read`) ; les périmètres d'administration humaine (`organization:*`, `members:*`,
  `validation:*`, `evidence_requests:*`) sont refusés à la création **et** neutralisés à l'usage
  (intersection, jamais l'union) ;
- **CSRF** — exigé pour les sessions cookie uniquement ; inapplicable aux requêtes porteur ;
- **débit** — `rate_limit_per_minute` appliqué par clé (E-02), avec `Retry-After` ;
- **audit** — les actions machine sont attribuées au membre créateur de la clé (jamais un
  utilisateur fantôme), `actor_user_id` nullable.
- **RLS** — la recherche par empreinte précède la connaissance de l'organisation : la migration
  `b5c6d7e8f901` ouvre une politique de SELECT bornée par le réglage transactionnel
  `app.api_key_lookup` (posé/effacé par la seule résolution). Preuve sous RLS réelle (rôle
  applicatif, `NOBYPASSRLS`) : `test_api_key_lookup_is_the_only_unscoped_read_and_is_transaction_bound`
  dans `tests/test_postgres_tenant_isolation.py` (exécuté par la CI PostgreSQL).

Preuve applicative : `tests/test_e03_api_key_auth.py` (9 tests : authentification machine sans
cookie ni CSRF, refus de scopes, clés invalides/révoquées/expirées, absence de repli cookie,
périmètres humains refusés, isolation tenant, journalisation, `last_used_at`, 429 contractuel).
Le traitement a **révélé E-09** (ci-dessous), sans lequel ce correctif resterait lettre morte sur
toute base migrée.

---

## E-04 — En-têtes de sécurité HTTP absents

**Sévérité : faible** (défense en profondeur ; l'application est majoritairement JSON, TLS
terminé par la plateforme).
**État : ouvert.**

**Observation.** Aucun des en-têtes suivants n'est émis, ni par FastAPI (`app/main.py` n'ajoute
aucun middleware d'en-têtes), ni par le frontend (`frontend/next.config.ts` ne définit pas
`headers()`), ni par `vercel.json` :

- `X-Content-Type-Options: nosniff` — les réponses de rapports (PDF, ZIP, CSV) et les textes
  extraits sont servis depuis le même domaine ;
- `Referrer-Policy` ;
- `X-Frame-Options` ou CSP `frame-ancestors` — l'interface (formulaires d'import, modales
  sensibles) n'est pas protégée contre l'encadrement ;
- `Strict-Transport-Security` — à la main de la plateforme en production, mais absent en
  preview/self-host ;
- `Content-Security-Policy` sur le frontend (une app Next.js avec scripts inline).

**Recommandation.** Ajouter un middleware d'en-têtes côté API (`nosniff`, `Referrer-Policy:
strict-origin-when-cross-origin`, `frame-ancestors 'none'`, HSTS quand `auth_cookie_secure`) et
déclarer `headers()` côté Next.js (CSP progressive : `frame-ancestors 'none'`, `object-src 'none'`,
`base-uri 'self'` d'abord). Couvrir par un test qui vérifie la présence des en-têtes sur `/healthz`
et sur une page frontend.

**Traitement (21/11/2026) — corrigé.** `app/core/security_headers.py` : middleware au **plus
externe** de la chaîne (les 429 du limiteur, les 503 d'infrastructure et les pré-vols CORS sont
stempés comme les réponses métier) — `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`, CSP `frame-ancestors 'none'; object-src
'none'; base-uri 'none'`, et `Strict-Transport-Security` **seulement** quand
`auth_cookie_secure` (jamais de HSTS menti en clair). `frontend/next.config.ts` : `headers()`
équivalents sur toutes les routes (`nosniff`, `DENY`, `Referrer-Policy`, CSP partielle sans
`script-src`, pour ne rien casser aux bundles Next.js). Preuve :
`tests/test_e04_e05_security_surface.py` (présence sur `/healthz` et `/api/v1/auth/status`,
HSTS conditionnel).

---

## E-05 — `/docs`, `/openapi.json` et `/metrics` exposés sans restriction

**Sévérité : faible** (divulgation d'information ; pas de donnée client).
**État : ouvert.**

**Observation.**

- `vercel.json` route **explicitement** `/docs` et `/openapi.json` vers le backend : le schéma
  complet de l'API (routes, modèles, erreurs) est public sur l'URL frontend, en production comme
  en preview ;
- `GET /metrics` (export Prometheus, A-09) est **sans authentification** : il expose la profondeur
  des files, l'état base/scanner et le trafic HTTP par gabarit de route. Il n'est pas routé par
  `vercel.json`, mais il l'est en Compose et sur tout déploiement nu de l'API.

**Recommandation.** En production : désactiver `/docs`/`openapi.json` (ou les réserver à un rôle
`admin`/réseau interne) et restreindre `/metrics` au réseau de supervision (ou exiger une
authentification). Garder l'ouverture éventuelle comme choix documenté de transparence, mais
alors l'assumer explicitement dans la doc d'exploitation.

**Traitement (21/11/2026) — corrigé.**

- `/docs`, `/redoc`, `/openapi.json` : fermés par défaut en staging/production (`API_DOCS_ENABLED`
  est l'opt-in explicite et assumé). Double garde : montage FastAPI conditionnel **et**
  middleware `DocsLockdownMiddleware` (testable et réversible à l'exécution) ; la fermeture ne
  touche jamais l'API elle-même.
- `/metrics` : dès qu'un jeton est configuré (`METRICS_AUTH_TOKEN`), la route exige
  `Authorization: Bearer <jeton>` (comparaison en temps constant). **Le jeton est obligatoire en
  staging/production** (le démarrage refuse autrement — fail-closed comme le reste de la
  configuration), optionnel en développement/test. À donner au scraper Prometheus.
- Preuve : `tests/test_e04_e05_security_surface.py` (documentation fermée à l'exécution, jeton
  requis/accepté/refusé, configuration de production fail-closed sur `METRICS_AUTH_TOKEN` et
  `api_docs_enabled` défaut à false).

---

## E-06 — Code mort après `return` dans `resolve_active_membership`

**Sévérité : faible (qualité de code).**
**État : corrigé dans cette mission.**

**Observation.** Dans `app/identity/service.py`, `resolve_active_membership` contenait, après
`return None`, deux instructions inaccessibles reliques d'une réécriture (reconstruction d'un
`MembershipView` et `set_db_request_context`). Elles ne pouvaient jamais s'exécuter, mais leur
présence suggérait à tort qu'un contexte RLS était posé sur ce chemin — ce qu'il n'est pas.

**Correction.** Les deux lignes inaccessibles sont supprimées ; la logique lisible du chemin
(final : `return None` après purge de l'organisation active) est inchangée.
**Preuve.** Suite backend complète : **295 passed / 64 skipped**.

---

## E-07 — Empreinte User-Agent enregistrée à la création de session, jamais vérifiée

**Sévérité : faible (qualité / contrôle mort).**
**État : ouvert.**

**Observation.** `AuthSession.user_agent_hash` est calculé et stocké à la création de la session
(`create_auth_session`), contraint en base (`length(user_agent_hash) = 64`), mais `resolve_current_session`
ne le compare **jamais** au User-Agent de la requête courante. Le champ ne protège donc rien :
un vol de cookie de session passe indifféremment de navigateur.

**Recommandation.** Trancher : soit retirer le champ (le binding UA est de toute façon un
contrôle faible, contournable par un attaquant qui rejoue l'en-tête), soit le vérifier et
forcer une reconnexion en cas de changement. Dans les deux cas, documenter la décision — un
contrôle visible et inactif est pire qu'une absence assumée.

**Traitement (21/11/2026) — corrigé, option « vérifier ».** `resolve_current_session` compare
désormais l'empreinte stockée à celle du User-Agent courant (comparaison en temps constant,
égalité stricte y compris l'absence) : tout écart rend le jeton inutilisable — le client se
reconnecte. La liaison est faible et assumée comme défense en profondeur (un outil qui rejoue
le cookie rejoue généralement l'en-tête) : elle ne remplace ni la rotation de session ni un
contrôle de device. Preuve : `tests/test_e07_session_ua_binding.py` (résolution unitaire dans
les deux sens, création sans UA, scénario HTTP « cookie volé → 401 »).

---

## E-08 — Double format d'empreinte accepté pour le scellement de la chaîne d'audit

**Sévérité : informationnel** (résidu de migration, documenté en code).
**État : ouvert.**

**Observation.** Les événements sont scellés par `sha256_json(material)` (JSON canonique) par
`append_audit_event`, mais `verify_audit_chain` accepte aussi l'empreinte historique
`compute_event_hash` (jointure par `:` des champs). Deux préimages distinctes valident donc un
même événement. De plus, la jointure `:` n'échappe pas les séparateurs : deux jeux de champs
différents produisent théoriquement le même matériau si un champ contient `:`. Les tests
(`test_audit_chain_integrity.py`) couvrent les deux formats.

**Recommandation.** À terme : n'accepter que le format canonique pour les événements créés après
une date de bascule, faire migrer/revérifier la chaîne, et conserver l'ancien format uniquement
pour la lecture de l'historique. En l'état, l'écart est connu, commenté en code, et n'ouvre pas
de contournement d'authentification.

**Traitement (21/11/2026) — corrigé.** `verify_audit_chain` n'accepte plus le format historique
(jointure `:`) que pour les événements **antérieurs au 21 novembre 2026**
(`CANONICAL_HASH_ENFORCED_AFTER`, constante datée et testée) ; tout événement postérieur doit
porter l'empreinte canonique. Le producteur n'écrivant plus que le format canonique depuis
plusieurs versions, la règle ne casse aucune chaîne légitime ; les lignes historiques restent
vérifiables sans réécriture. Preuve : `tests/test_e08_audit_hash_strictness.py` (legacy récent
→ chaîne invalide ; canonique récent → valide ; date de bascule figée) et le test historique
`test_historical_legacy_hashes_are_readable_without_rewriting`, dont la ligne fabriquée porte
désormais réellement un horodatage historique.

---

## E-09 — Le schéma migré et le modèle ORM divergent sur `api_keys` et `legal_holds`

**Sévérité : haute** (classe A-01 : fonctionnalité entière absente en base réelle, invisible
sur la suite SQLite).
**État : corrigé dans cette mission.** Trouvé pendant le traitement de E-03.

**Observation.** La migration `e8f9a1b2c3d4` crée `api_keys(hashed_secret, scopes, revoked_at)`
et `legal_holds` sans `is_active`/`expires_at`/`updated_at`, alors que le modèle ORM
(`app/models/domain.py`) et toute la couche applicative (`app/enterprise/service.py`, schémas
Pydantic) utilisent `key_hash`, `scopes_json`, `is_active`, `last_used_at`, `updated_at`.
Vérification systématique (schéma Alembic vs schéma `create_all`, diff des colonnes) : **exactement
ces deux tables divergent**, dans les deux sens.

**Conséquence.** Sur SQLite, `create_all` construit le schéma depuis le ORM : tout fonctionne et
la suite reste verte. Sur **toute base construite par la chaîne Alembic** (staging, production,
fixtures PostgreSQL de la CI), chaque opération ORM sur ces tables échoue sur une colonne
inexistante (`UndefinedColumn`) : création, liste, révocation de clés API et gestion des legal
holds sont **entièrement inopérantes**, et le seraient aussi de l'authentification par clé (E-03).
Les tests PostgreSQL existants ne le voyaient pas : ils écrivaient en SQL brut avec les noms de
la migration (cohérents entre eux), jamais via le ORM. C'est exactement le mécanisme d'A-01/A-04
(SQLite permissif + tests qui n'exercent pas le chemin réel).

**Correction.** Migration `c6d7e8f9a012` (après `b5c6d7e8f901`) qui aligne le schéma migré sur
le modèle applicatif : renommages `hashed_secret`→`key_hash` (unicité ajoutée, conforme au ORM)
et `scopes`→`scopes_json`, colonnes `is_active` (dérivée de `revoked_at IS NULL`), `last_used_at`,
`updated_at` (remplie depuis `created_at` pour les lignes existantes), et ajout des mêmes
colonnes manquantes sur `legal_holds`. `revoked_at` est conservé comme **trace historique**
(l'application ne le lit ni ne l'écrit plus ; documenté dans la migration, y compris la limite
du retour arrière). Les deux tables étant vides sur tout déploiement réel — l'écriture ORM y
échouait — aucune donnée n'était à migrer.

**Preuve.** Round-trip `upgrade head → downgrade → upgrade head` propre ; diff de schéma
post-migration : plus aucune divergence hors `revoked_at` (conservé exprès) ; la suite
complète **318 passed / 65 skipped** ; le seed SQL des tests PostgreSQL est aligné et la CI
`recette-qualification` (PostgreSQL réel) exerce désormais la chaîne complète avec la nouvelle
tête `c6d7e8f9a012` (épinglée dans la CI, le runbook Neon et les tests de migration).

**Leçon.** Une table dont le schéma n'est exercé que par `create_all` (SQLite) et des seeds en
SQL brut n'est jamais prouvée en base migrée. La vérification systématique « schéma des
migrations vs schéma du ORM » utilisée pour détecter cet écart est reproductible en une
dizaine de lignes ; elle gagnerait à être rejouée dans la CI.

La revue a porté sur les frontières qui tombent mal le plus souvent. Elles tiennent :

- **OIDC strict** (`identity/oidc.py`) : PKCE S256, allowlist d'algos asymétriques (jamais
  `none`/HS*), `kid` obligatoire, rotation de JWKS gérée, `aud`/`iss`/`azp` vérifiés, `nonce`
  comparé en temps constant, e-mail vérifié exigé, transaction de connexion signée et périmée
  (10 min), `state` rapproché du cookie de transaction.
- **Session et CSRF** : jeton 256 bits en cookie `HttpOnly`/`SameSite`, stockage sous HMAC,
  double-submit CSRF lié côté serveur (empreinte HMAC de la session) avec `compare_digest` ;
  redirection post-login confinée à l'origine frontend (`safe_return_to` refuse `//`, `\`,
  schéma et netloc).
- **`dev-login` fail-closed** : opt-in explicite (`AUTH_DEV_LOGIN_ENABLED`), refus catégorique
  en environnement production-like (jusqu'au démarrage : `config.py` refuse la combinaison).
- **Ingestion documentaire** (`documents/`) : cohérence extension ↔ MIME déclaré ↔ signature
  magique, noms de fichiers assainis (pas de chemin, guillemet, CRLF ou caractère de contrôle),
  taille bornée, **scan antivirus avant tout parseur** (INSTREAM, protocole clamd parse strictement :
  réponse NUL-terminée, EOF exigé, ambiguïté = verdict non exploitable), fail-closed partout
  (scanner/stoage absent = refus, jamais de contournement).
- **Anti-TOCTOU** : lecture conditionnée à l'ETag (`IfMatch`), promotion par copie conditionnelle
  (`CopySourceIfMatch`), compensation par suppression de l'objet propre si la transaction échoue,
  registre de purge avec preuve par relecture (jamais un booléen « supprimé »).
- **Workers** : `FOR UPDATE SKIP LOCKED`, leases à expiration récupérés, **fencing** par
  `worker_id` + `attempt_count` resouscrit sous `FOR UPDATE` immédiatement avant publication,
  segments immuables (double publication refusée), arrêt propre et santé observable (A-06).
- **Parseurs bornés** : pages/caractères/pixels/timeouts plafonnés, bombes de décompression
  (avertissements PIL promus en erreur + `verify()`), PDFs chiffrés refusés, rendu OCR estimé
  avant rasterisation.
- **Stockage** : buckets privés, clés générées serveur (jamais fournies par le client), policy
  POST présignée stricte (Content-Type exact + plage de taille), SSE obligatoire en prod,
  suppression par préfixe tenant-scopé validé par regex ancrée avec preuve de relecture.
- **Isolation tenant** : RLS PostgreSQL `ENABLE` + `FORCE` avec politiques par organisation,
  contexte `set_config` transactionnel posé par la résolution d'adhésion, SQLite interdit sur
  plateforme managée et en production (le fallback silencieux a été supprimé — A-05).
- **Chaîne d'audit** : append-only, sérialisée par advisory lock transactionnel, chaînage
  SHA-256 avec `previous_event_hash`, vérification mathématique exposée (`verify_audit_chain`).
- **Configuration fail-closed** (`core/config.py`) : HTTPS forcé en prod partout, CORS sans
  wildcard avec credentials, secrets non-placeholder exigés (≥ 32 caractères), TTL plafonnés,
  cohérence `lease ≥ pages × timeout OCR + 60`, SSE/clamav/OIDC obligatoires en prod.
- **Exports** : noms d'entrées ZIP fixes (pas de zip-slip), `Content-Disposition` construit à
  partir de noms serveur, CSV/PDF sans injection de formule ni d'en-tête.
- **CI/CD** : actions GitHub épinglées par SHA, `permissions: contents: read`, migration de
  production sous confirmation manuelle (`DO_NOT_APPLY` par défaut).
- **Frontend** : aucun `dangerouslySetInnerHTML`/`eval`, jetons uniquement en cookies (jamais
  `localStorage`), CSRF attaché à toute mutation, liens externes en `rel="noopener noreferrer"`.

---

## Limites de cette revue

À ne pas lire comme un feu vert au-delà de ce qui est écrit :

- **Exécution partielle locale.** 318 tests passés / 65 skipés : les tests RLS PostgreSQL
  (`VERICLAIM_TEST_POSTGRES_URL`), OCR réel (Tesseract) et signature binaire n'ont pas tourné
  sur le poste de revue — y compris la nouvelle preuve RLS de la recherche par clé (E-03/E-09),
  qui doit être confirmée par le job `recette-qualification` de la CI (PostgreSQL réel). Les
  corrections de migrations (E-09) ont été prouvées localement par round-trip Alembic et par la
  comparaison systématique des schémas, pas contre un PostgreSQL réel.
- **Aucun antivirus réel ni stockage managé réel** n'a été exercé (périmètre connu des registres
  A-07/A-08/A-10, inchangés par cette revue).
- **Pas d'audit d'intrusion, pas de fuzzing** au-delà de la lecture ciblée des frontières ; pas
  d'analyse SAST automatisée (à intégrer en CI si besoin).
- **Le code métier du moteur** (rule_book, fact_extractor, preuve/risk) n'a pas été revu pour
  son exactitude réglementaire — uniquement ses frontières de sécurité.
- **Dépendances frontend** : `npm audit` = 0 vulnérabilité au 21/11/2026 ; le périmètre n'inclut
  pas l'analyse des scripts de build.
- **Limites assumées des correctifs** : le limiteur de débit est par processus (multi-réplicas
  = plafond multiplié) ; la liaison User-Agent est une défense en profondeur rejouable ; la
  politique de recherche de clé ouvre la lecture de `api_keys` pendant une transaction portant
  le réglage `app.api_key_lookup` (borné et testé, mais c'est une brèche RLS assumée).

## Annexes — commandes exécutées

```bash
# Suite backend (baseline puis après chaque correction)
cd backend && APP_ENV=test DATABASE_URL=sqlite:///<scratch>.db AUTO_CREATE_SCHEMA=true python -m pytest -q
# → 295 passed / 64 skipped (baseline E-01) ; 318 passed / 65 skipped (après E-02→E-09)

# Dépendances
pip-audit -r backend/requirements.txt   # → 42 vulnérabilités pillow 12.3.0 (E-01) ; 0 après
cd frontend && npm audit --audit-level=low && npm run typecheck && npm run build   # → propres

# Fumée image avec Pillow 13.0.0
python -c "<inspect_document_bytes + extract_result sur PNG>"  # → inspection OK, OCR dégradé contrôlé

# E-09 : diff systématique migration vs ORM (reproductible)
python -c "<alembic upgrade head sur SQLite A ; create_all sur SQLite B ; diff des colonnes>"
# → api_keys et legal_holds divergents avant ; uniquement revoked_at (trace) après

# Chaîne de migrations : upgrade → downgrade → upgrade, tête unique
alembic upgrade head && alembic downgrade b5c6d7e8f901 && alembic upgrade head && alembic heads
```

**Corrections livrées sur `main`** (base `6f2cade`, PR #16) :

Mission 1 (E-01, E-06) : `backend/requirements.txt` `Pillow>=13,<14` ; suppression du code mort
de `resolve_active_membership`.

Mission 2 — traitement des écarts restants :

1. **E-02** `app/core/rate_limit.py` + config `API_RATE_LIMIT_*` — limitation de débit à trois
   seaux + limite contractuelle par clé ;
2. **E-03** `app/identity/api_keys.py`, `app/identity/dependencies.py`, `app/enterprise/service.py`
   (garde de périmètres), `app/api/v1/enterprise.py` — authentification machine réelle ;
3. **E-04** `app/core/security_headers.py`, `frontend/next.config.ts` — en-têtes de sécurité
   des deux côtés ;
4. **E-05** `DocsLockdownMiddleware`, `/metrics` authentifié, `API_DOCS_ENABLED` /
   `METRICS_AUTH_TOKEN` (fail-closed en production) ;
5. **E-07** `app/identity/service.py` — liaison User-Agent contraignante ;
6. **E-08** `app/audit/service.py` — scellement canonique seul après la bascule du 21/11/2026 ;
7. **E-09** migrations `b5c6d7e8f901` (politique de recherche de clé) et `c6d7e8f9a012`
   (réconciliation `api_keys`/`legal_holds`), seed et preuve RLS dans
   `tests/test_postgres_tenant_isolation.py`, ré-épinglage de la tête (`c6d7e8f9a012`) dans la
   CI, le runbook Neon et les tests de migration.

Tests ajoutés : 23 (`test_e02_rate_limiting.py` 3, `test_e03_api_key_auth.py` 9,
`test_e04_e05_security_surface.py` 5, `test_e07_session_ua_binding.py` 3,
`test_e08_audit_hash_strictness.py` 3) + 1 test PostgreSQL gated (E-03/E-09).
