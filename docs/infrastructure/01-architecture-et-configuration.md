# 01 — Architecture et configuration réellement déployées

**Date :** 12 novembre 2026
**SHA applicatif étudié :** `fa31e0a8145fc86cda04b466989af100cf1eeeda` (+ `main` `b289652a` comme référence)
**Environnements :** recette (Preview Vercel + pile jetable locale)

## 1. Cartographie honnête des composants

La colonne « Réel ? » est la plus importante du document. Elle distingue ce que
j'ai pu faire tourner pour de vrai dans mon environnement de ce qui reste simulé
ou hors d'atteinte.

| Composant | Configuration | Service réel | Fonctionnel | Preuve |
|---|---|---|---|---|
| Frontend | Next.js 16, `frontend/` | Vercel (Preview + `staging`) | **oui** (build, typecheck, proxy) | `npm run typecheck` et `npm run build` verts ; smoke HTTP à travers le proxy Next |
| API | FastAPI, `backend/app/main.py` | Uvicorn / Vercel service | **oui** | 54 étapes E2E sur l'application non modifiée (`11-tests-e2e.md`) |
| Base PostgreSQL | SQLAlchemy + psycopg 3 | **Neon** (assistant) / PostgreSQL 16 local pour les preuves | **oui** sur ma pile jetable ; Neon non vérifiable ici | `03-neon-et-runtime.md` |
| RLS multi-tenant | policies `p_<table>_tenant` | PostgreSQL | **oui** | 27 tests RLS verts sur PostgreSQL 16.2 réel |
| Authentification | OIDC Authorization Code + PKCE (`app/identity/oidc.py`) | **fournisseur OIDC simulé** (Clerk non joignable) | **oui**, client réel exercé | `02-authentification-et-identite.md` |
| Sessions | cookie opaque + CSRF double-submit | base PostgreSQL | **oui** | E2E, refus CSRF 403, révocation, expiration |
| Stockage documentaire | `S3ObjectStorage` (boto3, SigV4) | **serveur S3 local** (MinIO non installable) | **oui**, adaptation boto3 réelle | `05-stockage-documentaire.md` |
| Antivirus | `ClamAvMalwareScanner` (clamd INSTREAM) | **écouteur de protocole, pas ClamAV** | protocole réel, **moteur absent** | `06-antivirus-et-fichiers.md` |
| OCR | PyMuPDF + Tesseract via `pytesseract` | **moteur Tesseract 5.5.4 réel** via `tesserocr` | oui pour le moteur ; chemin `pytesseract` non exerçable ici | `07-ocr-et-extraction.md` |
| Workers | `app/workers/*` (file PostgreSQL) | processus réels | **oui** | `08-workers-et-traitements.md` |
| Rapports | `pdf_generator`, `dossier_exporter` | in-process | **oui** | PDF 7 945 octets et ZIP récupérés en E2E |
| Audit | `audit_events` chaîne de hachage | PostgreSQL | **oui** | `/api/v1/audit/verify` → `is_valid: true`, 14 événements |
| Notifications | — | **aucune implémentation** | **non** | absence de code d'envoi, voir §5 |
| Monitoring | logs applicatifs Python | aucun agrégateur | **partiel** | `09-monitoring-et-incidents.md` |

## 2. Séparation des environnements

| Environnement | Base | Identité | Stockage | Constat |
|---|---|---|---|---|
| Développement local | SQLite autorisé | OIDC optionnel, `dev-login` opt-in | désactivé par défaut | conforme |
| CI | SQLite jetable + PostgreSQL 16 jetable | aucun IdP | aucun | conforme |
| Recette / Preview | **doit** être PostgreSQL | OIDC **obligatoire** | S3 **obligatoire** | voir §3 |
| Production | PostgreSQL | OIDC | S3 | non modifié |

Les gardes de `backend/app/core/config.py` sont réelles et fail-closed :
`APP_ENV=staging|production` refuse le SQLite, refuse `AUTO_CREATE_SCHEMA=true`,
refuse `AUTH_DEV_LOGIN_ENABLED=true`, refuse un `AUTH_SESSION_SECRET` de
substitution, exige HTTPS sur toutes les URLs et exige OIDC + S3 + ClamAV.

### Défaut corrigé : une Preview sans base ne s'arrêtait pas

`APP_ENV` vaut `development` par défaut. Une Preview Vercel sans `DATABASE_URL`
tombait donc sur `sqlite:///./vericlaim.db`. Sur un système de fichiers de
fonction serverless, cela donne au mieux une base éphémère **sans RLS ni chaîne
d'audit durable**, au pire un démarrage en échec. Correction : dès qu'une variable
de plateforme managée est présente (`VERCEL`, `K_SERVICE`, `DYNO`), l'absence de
`DATABASE_URL` — ou un `DATABASE_URL` SQLite — est une erreur de configuration
explicite. Le développement local garde son défaut documenté.

## 3. Variables nécessaires (noms seuls, jamais de valeurs)

**Obligatoires en recette**

`APP_ENV`, `DATABASE_URL`, `AUTH_SESSION_SECRET`, `AUTH_COOKIE_SECURE=true`,
`FRONTEND_URL`, `CORS_ORIGINS`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_REDIRECT_URI`, `OIDC_DISCOVERY_URL` (dérivée si absente),
`DOCUMENT_STORAGE_BACKEND=s3`, `DOCUMENT_STORAGE_ENDPOINT`,
`DOCUMENT_STORAGE_PUBLIC_ENDPOINT`, `DOCUMENT_STORAGE_ACCESS_KEY`,
`DOCUMENT_STORAGE_SECRET_KEY`, `DOCUMENT_STORAGE_SSE_MODE` (non `none`),
`DOCUMENT_SCANNER_MODE=clamav`, `DOCUMENT_CLAMAV_HOST`.

**Optionnelles à surveiller**

`OIDC_CLIENT_SECRET` (client confidentiel), `AUTH_SESSION_TTL_SECONDS`,
`MAX_UPLOAD_BYTES`, `MAX_PDF_PAGES`, `DOCUMENT_EXTRACTION_*`,
`ANALYSIS_DETECTION_*`, `TESSERACT_LANGUAGES`, `VERICLAIM_CERTIFICATE_REGISTRY_JSON`,
`EU_2024_825_FR_TRANSPOSITION_STATUS`.

**Interdites en recette** : `AUTH_DEV_LOGIN_ENABLED=true`, tout
`DATABASE_URL` de production, toute clé de production. Le garde de démarrage les
refuse pour les deux premières.

### Cohérence des origines

`OIDC_REDIRECT_URI` doit partager l'hôte de `FRONTEND_URL`, sinon le cookie de
session et le cookie CSRF ne seraient plus same-origin. Le contrôle existe dans
`get_settings` et lève une erreur au démarrage. `CORS_ORIGINS` refuse `*`.

### Dépendances de démarrage

`lifespan` exécute `create_tables()` : en recette (`AUTO_CREATE_SCHEMA=false`),
cela **vérifie** que la base est à la révision Alembic attendue et échoue sinon
(`verify_schema_at_head`). Aucune création opportuniste de schéma en recette : le
schéma est une précondition de déploiement.

## 4. Ce qui manque structurellement sur un déploiement Vercel

Ce point est décisif pour la commercialisation et n'était pas explicité jusqu'ici.

`vercel.json` déclare trois services (backend FastAPI, frontend Next.js) et des
rewrites. Il ne peut pas déclarer de **processus long**. Or :

1. `app/workers/document_extraction_worker.py` et
   `app/workers/analysis_detection_worker.py` n'ont qu'un point d'entrée
   `run_forever()`, lancé par `docker-compose.yml` comme services séparés.
2. `vercel.json` ne déclare **aucun `crons`**.
3. Sur Vercel, une fonction serverless est gelée après sa réponse.

**Conséquence vérifiée** : sur un déploiement Vercel, un document importé reste
`pending` et une analyse reste `queued` indéfiniment. Sur ma pile réelle, les
deux workers avancent dès qu'ils tournent, et **n'avancent pas** quand ils sont
arrêtés — ce que le harnais prouve explicitement (`worker arrêté : le job reste
en file, aucun succès fabriqué`). Ce n'est pas une hypothèse sur Vercel : c'est
la conséquence directe de l'absence de tout mécanisme de déclenchement.

C'est l'anomalie **A-06** du registre, priorité P1 pour un pilote.

## 5. Services absents du produit

| Service attendu par la mission | Réalité |
|---|---|
| Notifications | **aucun code d'envoi d'e-mail ou de webhook**. Les « alertes » de `enterprise` sont des objets de lecture en base. |
| Monitoring / métriques | logs Python standard + `/healthz` + `/readyz`. Aucun agrégateur, aucune alerte. |
| File d'attente externe | volontairement PostgreSQL (`document_extraction_jobs`, `analysis_detection_jobs`). Aucun Redis/RabbitMQ : rien à installer, mais aucun outil de DLQ externe. |
| Sauvegarde | aucune procédure automatisée outillée dans le dépôt (voir `10-sauvegardes-restauration.md`). |

Ne pas disposer de ces services n'est pas un défaut de code : c'est un écart entre
le périmètre de la mission et le produit existant. Il est reporté tel quel dans
le verdict.

## 6. Verdict de la phase

- Architecture réelle, cohérente, fail-closed sur l'essentiel.
- Le plan de données est **réellement démontrable** (base, RLS, stockage, workers,
  extraction, OCR, rapports, audit).
- Trois limitations structurelles à assumer : pas de worker sur Vercel, pas de
  notifications, pas de monitoring.
- Aucune variable de production n'est utilisée par les preuves de ce dossier ;
  les identifiants de la pile jetable sont générés par exécution et jamais écrits.
