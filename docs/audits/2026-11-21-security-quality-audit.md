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
| E-01 | Pillow 12.3.0 : 42 CVE connues ; la contrainte `Pillow>=11,<13` exclut la version corrigée | **haute** | **corrigé dans cette mission** |
| E-02 | Aucun rate limiting effectif, alors que le modèle en prévoit un (`rate_limit_per_minute`) | moyenne | **ouvert** |
| E-03 | Clés API créables mais jamais acceptées comme mode d'authentification | moyenne | **ouvert** |
| E-04 | En-têtes de sécurité HTTP absents (nosniff, HSTS, frame-ancestors, Referrer-Policy) | faible | **ouvert** |
| E-05 | `/docs`, `/openapi.json` et `/metrics` exposés sans restriction | faible | **ouvert** |
| E-06 | Code mort après `return` dans `resolve_active_membership` | faible (qualité) | **corrigé dans cette mission** |
| E-07 | `user_agent_hash` enregistré à la création de session, jamais vérifié | faible (qualité) | **ouvert** |
| E-08 | Double format d'empreinte accepté pour le scellement de la chaîne d'audit | informationnel | **ouvert** |

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

---

## Points forts constatés (à conserver)

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

- **Exécution partielle locale.** 295 tests passés / 64 skipés : les tests RLS PostgreSQL
  (`VERICLAIM_TEST_POSTGRES_URL`), OCR réel (Tesseract) et signature binaire n'ont pas tourné
  sur le poste de revue. La preuve d'isolation tenant repose donc ici sur la lecture des
  politiques RLS et des tests CI existants, pas sur une exécution locale.
- **Aucun antivirus réel ni stockage managé réel** n'a été exercé (périmètre connu des registres
  A-07/A-08/A-10, inchangés par cette revue).
- **Pas d'audit d'intrusion, pas de fuzzing** au-delà de la lecture ciblée des frontières ; pas
  d'analyse SAST automatisée (à intégrer en CI si besoin).
- **Le code métier du moteur** (rule_book, fact_extractor, preuve/risk) n'a pas été revu pour
  son exactitude réglementaire — uniquement ses frontières de sécurité.
- **Dépendances frontend** : `npm audit` = 0 vulnérabilité au 21/11/2026 ; le périmètre n'inclut
  pas l'analyse des scripts de build.

## Annexes — commandes exécutées

```bash
# Suite backend (baseline puis après correction Pillow)
cd backend && APP_ENV=test DATABASE_URL=sqlite:////tmp/vericlaim-audit.db AUTO_CREATE_SCHEMA=true python -m pytest -q
# → 295 passed, 64 skipped (avant et après)

# Dépendances
pip-audit -r backend/requirements.txt   # → 42 vulnérabilités pillow 12.3.0 (contrainte <13)
cd frontend && npm audit --audit-level=low   # → 0 vulnérabilité

# Fumée image avec Pillow 13.0.0
python -c "<inspect_document_bytes + extract_result sur PNG>"  # → inspection OK, OCR dégradé contrôlé
```

**Corrections livrées par cette mission** (commits sur `main`, même base `6f2cade`) :

1. `backend/requirements.txt` : `Pillow>=11,<13` → `Pillow>=13,<14` (E-01) ;
2. `backend/app/identity/service.py` : suppression du code mort de `resolve_active_membership` (E-06).
