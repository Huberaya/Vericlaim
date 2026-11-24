# Inventaire des variables Vercel — pilote Clerk (Mission 4)

> **Statut** : document de travail à remplir par l'opérateur.
> État connu au 2026-11-24 : déploiement `dpl_6QQyDFMCoAafHkBTyCUMtk4UoHGQ` (READY)
> tourne sur l'**ancien commit `6f2cade`** — les 8 commits locaux attendent un
> jeton GitHub pour être poussés. Rapport d'exécution ChatGPT reçu (NO-GO).
>
> Sources : exigences `backend/app/core/config.py` (fail-closed en
> staging/production), `docs/infrastructure/04-vercel-preview.md` §6, rapport
> d'exécution Vercel du jour.

## 1. Variables d'identité et d'URL — ✅ en place

| Variable | Valeur / remarque | Statut |
|---|---|---|
| `OIDC_ISSUER` | `https://fond-moth-6064.clerk.accounts.dev` | ✅ posée |
| `OIDC_CLIENT_ID` | `ZHg0T9RSvAUH3wbX` | ✅ posée |
| `OIDC_CLIENT_SECRET` | (secret Clerk, validé côté provider) | ✅ posée (sensitive) |
| `OIDC_REDIRECT_URI` | `https://vericlaim-nine.vercel.app/api/v1/auth/callback` | ✅ posée, déjà acceptée par Clerk |
| `FRONTEND_URL` | `https://vericlaim-nine.vercel.app` | ✅ corrigée |
| `CORS_ORIGINS` | alignée sur le domaine Vercel | ✅ corrigée |
| `AUTH_COOKIE_SECURE` | `true` | ✅ corrigée |
| `AUTH_DEV_LOGIN_ENABLED` | **doit rester absente/false** | ✅ absente (souhaité) |

## 2. Variables bloquantes — à traiter dans l'ordre

| # | Variable / action | Problème | Action requise | Fait |
|---|---|---|---|---|
| 1 | **`git push origin main`** | 8 commits locaux jamais poussés (E-01→E-09, tests, chantier OIDC, fix proxy). Le déploiement tourne sur `6f2cade` | ~~Fournir un **jeton GitHub**~~ — ✅ **fait** : branche `mission4/pilote-clerk` poussée (jeton fourni), **PR [#18](https://github.com/Huberaya/Vericlaim/pull/18)** ouverte, **CI 5/5 verte**. Reste à **merger** (bouton Merge) → Vercel redéploie sur `main` | ☑ |
| 2 | **`DATABASE_URL`** | `password authentication failed for user 'neondb_owner'` sur `ep-curly-glade-b1da98jf.c-5.eu-central-1.aws.neon.tech` — mot de passe invalide/rotaté | Console **Neon** → reset password de `neondb_owner` (ou créer un rôle dédié) sur cet endpoint → copier la connection string → mettre à jour `DATABASE_URL` dans Vercel (**Production + Preview**, `sslmode=require`) | ☐ |
| 3 | **Migrations Alembic** | La base doit atteindre la head `c6d7e8f9a012` | **Strictement APRÈS le push** (le code attend cette head) : `alembic upgrade head` selon `docs/operations/production-migration-runbook.md` | ☐ |
| 4 | **`AUTH_SESSION_SECRET`** | Présente mais longueur non vérifiable ; exige ≥ 32 car. non-placeholder en staging/production | Vérifier qu'elle fait ≥ 32 caractères aléatoires ; sinon la régénérer (`openssl rand -hex 32`) | ☐ |
| 5 | **`METRICS_AUTH_TOKEN`** | Absente — **requis** (E-05) en staging/production, sinon le démarrage échoue | Créer (`openssl rand -hex 32`) et poser en sensitive ; valeur à conserver pour appeler `/metrics` | ☐ |

## 3. Variables d'infra (cycle documentaire) — exigées si `APP_ENV` est `staging`/`production`

Le code refuse de démarrer en environnement production-like sans ces éléments
(philosophie fail-closed). **Décision à prendre** (voir §5).

| Variable | Exigence | Statut | Action |
|---|---|---|---|
| `DOCUMENT_STORAGE_BACKEND` | `s3` (défaut prod) | ☐ à confirmer | Renseigner `s3` |
| `DOCUMENT_STORAGE_ENDPOINT` | endpoint S3/MinIO **interne** | ☐ absente | Provisionner MinIO/S3, renseigner |
| `DOCUMENT_STORAGE_PUBLIC_ENDPOINT` | endpoint public (téléchargements signés) | ☐ absente | Renseigner |
| `DOCUMENT_STORAGE_ACCESS_KEY` / `DOCUMENT_STORAGE_SECRET_KEY` | fournies **ensemble** | ☐ absentes | Renseigner (sensitive) |
| `DOCUMENT_STORAGE_REGION` | défaut `us-east-1` si backend `s3` | ☐ | MinIO : laisser défaut |
| `DOCUMENT_STORAGE_SSE_MODE` | **≠ `none`** — `aes256` ou `aws:kms` | ☐ absente | `aes256` suffit pour MinIO |
| `DOCUMENT_QUARANTINE_BUCKET` | requis | ☐ absente | Créer le bucket, renseigner |
| `DOCUMENT_CLEAN_BUCKET` | requis | ☐ absente | Créer le bucket, renseigner |
| `DOCUMENT_SCANNER_MODE` | `clamav` en prod-like | ☐ absente | Renseigner `clamav` |
| `DOCUMENT_CLAMAV_HOST` | hôte ClamAV joignable depuis Vercel | ☐ absente | Provisionner ClamAV (voir `docs/infrastructure/`), renseigner |

### Questions d'inventaire à remplir (l'opérateur coche)

- [ ] Un service **MinIO/S3** existe-t-il déjà ? → URL interne, URL publique, identifiants
- [ ] Les buckets `quarantine` et `clean` existent-ils ? → noms exacts
- [ ] Un **ClamAV** existe-t-il déjà ? → hôte/port joignable depuis Vercel
- [ ] Sinon : le pilote SSO doit-il démarrer sans le cycle documentaire ? (voir §5)

## 4. Variables optionnelles (bonnes pratiques)

| Variable | Recommandation pilote |
|---|---|
| `API_DOCS_ENABLED` | `false` (docs off en prod ; `/healthz` reste public) |
| `API_RATE_LIMIT_ENABLED` | `true` (+ `API_RATE_LIMIT_AUTH_PER_MINUTE`, `..._WRITE_PER_MINUTE`, `..._EVALUATE_PER_MINUTE` aux défauts convient) |

## 5. Décision de configuration — à trancher

- **Option A — configuration production complète** (recommandée) : fournir tout
  le §3 + §2, `APP_ENV=production` (ou `staging`). Le pilote inclut le dépôt de
  documents, la config fail-closed s'applique.
- **Option B — minimal SSO temporaire** : `APP_ENV=development` sur Vercel,
  seul le §2 est requis. Le SSO Clerk fonctionne, mais la config fail-closed
  n'est pas appliquée (documenté comme limite du pilote, à corriger avant tout
  usage réel).

## 6. État de service hors Vercel (à mettre à jour)

| Composant | Rôle | Statut |
|---|---|---|
| **Neon** (`ep-curly-glade-b1da98jf`, eu-central-1) | PostgreSQL | 🟥 identifiants invalides (cf. §2-2) |
| **GitHub** (`Huberaya/Vericlaim`) | source du déploiement | 🟧 branche poussée + PR #18 CI verte — **à merger** (main protégée : PR obligatoire) |
| **Clerk** (`fond-moth-6064.clerk.accounts.dev`) | IdP OIDC | ✅ validé de bout en bout |
| **MinIO/S3** | pièces jointes | ☐ à inventorier |
| **ClamAV** | antivirus | ☐ à inventorier |
| **Connecteur Vercel (agent)** | vérification autonome | ⚠ activé mais token « Access denied » — revoir les scopes du token et la portée team |

## 7. Séquence de mise en service (ordre strict)

1. Push GitHub (§2-1) → Vercel redéploie sur le bon commit.
2. Réparation `DATABASE_URL` (§2-2).
3. `AUTH_SESSION_SECRET` + `METRICS_AUTH_TOKEN` + variables d'infra selon décision §5.
4. `alembic upgrade head` (§2-3) — **après** le push, **avant** le test SSO.
5. Vérifs : `/api/v1/auth/status` → `oidc_configured: true`, `dev_login_enabled: false` ;
   `/readyz` → `database: ok`, `document_storage: configured`, `malware_scanner_reachable: true`.
6. Test bouton SSO Clerk réel (compte pilote) → comptes pilotes + pré-invitations
   (`INVITED` → `ACTIVE` au 1er login, email vérifié exigé).
7. Clôture : registre de recette, commit de suivi.
