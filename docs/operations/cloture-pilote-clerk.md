# Clôture du pilote Clerk — mise en service SSO (Mission 4)

**Date :** 26 novembre 2026
**Application :** `https://vericlaim-nine.vercel.app` (projet Vercel `vericlaim`, branche `main`)
**Tenant IdP :** `https://fond-moth-6064.clerk.accounts.dev` (instance Clerk, mode Development)
**Portée :** mise en service du flux SSO réel avec le vrai tenant Clerk et la
configuration Vercel. Ce document signe la recette du pilote et nomme les
déviations et les restes à faire pour une ouverture réelle.

## 1. Chaîne d'exécution

| Étape | Référence | Résultat |
|---|---|---|
| Code (missions 1→4, 11 commits) | PR **#18**, merge `1617a7c` | CI 5/5 verte avant merge |
| Migration schéma **staging** | workflow `Staging database migration` run **#5** | `alembic upgrade head` + grants + vérification RLS ✅ |
| Migration schéma **production** | workflow `Production database migration` run **#5** (env. `production`, `APPLY`) | idem ✅ |
| Routage `/readyz` | PR **#19**, merge `28205ac` (`vercel.json`) | anomalie **A-15** corrigée |
| Redeploiement Vercel | déploiements automatiques sur `main` | `READY` |

## 2. Validations (observées, pas déduites)

| Contrôle | Observation |
|---|---|
| `/api/v1/auth/status` | `oidc_configured: true`, `authentication_required: true`, `dev_login_enabled: false` |
| `/healthz` | `{"status":"ok","service":"vericlaim-rule-engine"}` |
| `/readyz` | `{"status":"ready","database":"ok","document_storage":"disabled","malware_scanner":"disabled","document_import":"refused"}` |
| Redirection SSO | `302` → `/oauth/authorize` Clerk : `client_id`, `redirect_uri`, PKCE S256, `state`, `nonce` conformes |
| **Parcours complet** | Bouton SSO → Clerk (GitHub OAuth) → retour authentifié, espace de travail affiché |
| Provisionnement | `users` : e-mail vérifié, `status=active`, `identity_provider=https://fond-moth-6064.clerk.accounts.dev`, `external_subject=user_3MdwduzjiB08C` (stable) |
| Adhésion | `memberships` : `active`, rôle `owner` (organisation « Rikik ») |
| Session | `auth_sessions` : 1 session active, créée au login (liaison navigateur E-07 en place) |
| Journal d'audit | `organization.created` tracé sur le tenant réel |

## 3. Décisions actées

- **Comptes pilotes** : le compte propriétaire suffit à la recette ; les
  pré-invitations (`INVITED` → `ACTIVE` au 1er login, e-mail vérifié exigé)
  seront faites à l'ouverture réelle. Conséquence : l'événement
  `membership.activated_by_sso` — prouvé en CI — n'a pas d'occurrence réelle
  à ce jour (il apparaîtra au premier login d'un membre pré-invité).
- **Configuration** : **option B (minimal SSO)** actée pour le pilote —
  `APP_ENV` reste en `development` sur Vercel : les contrôles fail-closed ne
  sont pas appliqués et le cycle documentaire est indisponible
  (`document_storage: disabled`, `document_import: refused`). L'**option A
  (production complète)** — variables §3 de
  `inventaire-variables-vercel-pilote.md` + `APP_ENV=production` — est
  **requise avant toute donnée réelle**.

## 4. Déviations au runbook (`production-migration-runbook.md`)

1. La base cible s'appelle **`neondb`** (et non `vericlaim`) — convention de
   nommage non respectée, garde-fous de contenu du script bootstrap exécutés.
2. Les rôles `vericlaim_migrator` / `vericlaim_app` préexistaient avec `LOGIN` :
   le script d'activation a **refusé** (comportement voulu, toute rotation
   implicite est interdite). Une **rotation volontaire et explicite** des deux
   mots de passe a été faite le 24/11 (comme pour `neondb_owner` exposé), puis
   les URLs ont été placées dans l'environnement GitHub `production`.
3. Les grants de fin du script bootstrap (annulés avec la transaction du refus)
   ont été appliqués séparément, à l'identique du script.
4. L'environnement GitHub `staging` disposait déjà de ses secrets ; la
   validation staging obligatoire a été exécutée (run #5, cible vérifiée
   distincte de la production par `preflight_staging.py`).
5. L'instance Clerk est en **Development mode** — suffisant pour le pilote, à
   promouvoir en production avant ouverture réelle.

## 5. Restes à faire avant ouverture réelle

- Configuration **production complète** (option A, §3 de l'inventaire) :
  `METRICS_AUTH_TOKEN`, stockage S3/MinIO + buckets + SSE, `ClamAV` +
  `DOCUMENT_SCANNER_MODE=clamav`, `APP_ENV=production`, `AUTH_SESSION_SECRET`
  ≥ 32 caractères vérifiée. → débloque le cycle documentaire (A-07 partiel).
- Comptes pilotes réels + pré-invitations (cycle `INVITED`→`ACTIVE` observé).
- Instance Clerk production (fin du Development mode).
- Les P0 « partiel » du registre d'anomalies : antivirus réel (A-07),
  planification des sauvegardes (A-08), monitoring/alertes (A-09).

## 6. Hygiène de fin de mission

- Les jetons GitHub transmis en conversation pendant la mission doivent être
  **révoqués** (Settings → Developer settings → Personal access tokens).
- Les mots de passe rotatés (`neondb_owner`, `vericlaim_migrator`,
  `vericlaim_app`) ne figurent dans aucun dépôt ; les URLs de connexion vivent
  uniquement dans les secrets GitHub et la variable Vercel `DATABASE_URL`.
- Anomalie de recette consignée : **A-15** (`/readyz` non routé, corrigé).
