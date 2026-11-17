# 04 — Vercel et déploiement Preview

**Date :** 12 novembre 2026
**Environnement :** recette / Preview uniquement
**Accès :** GitHub (API) uniquement. **`api.vercel.com` et `*.vercel.app` ne sont pas
joignables depuis mon environnement.**

## 1. Ce que confirme l'API GitHub

Vercel publie ses déploiements dans l'API GitHub du dépôt ; c'est vérifiable et
daté.

| Déploiement | Environnement | Référence | État | Créé |
|---|---|---|---|---|
| `7745430154` | Preview | `6f6fb1f1` | `success` | 12/11 18:02 |
| `7745416783` | Preview | `c80059a6` | `success` | 12/11 18:01 |
| `7745416775` | Preview | `8234e2ca` | `inactive` | 12/11 18:01 |
| `7745379471` | Preview | `81575dae` | `success` | 12/11 18:00 |
| `7743189374` | Preview | `115c1863` | `success` | 12/11 16:30 |
| `7744971223` | `staging` | `main` `b289652a` | `success` | 12/11 17:42 |
| `7213592895` | Production | `b289652a` | `success` | 20/10 23:30 |

Preview de tête au SHA `6f6fb1f1` :
`https://vericlaim-axgd3wb8p-baya-ibraimmayoanouedu-8164s-projects.vercel.app`
Preview au SHA applicatif `115c1863` :
`https://vericlaim-4iv842mqy-baya-ibraimmayoanouedu-8164s-projects.vercel.app`

L'état `success` d'un déploiement Vercel signifie que le build et le déploiement
ont abouti. **Cela ne prouve aucun parcours utilisateur.** C'est exactement la
limite que la qualification antérieure avait identifiée.

## 2. Configuration de build examinée dans le dépôt

`vercel.json` déclare deux services et des réécritures :

- `backend` : FastAPI, entrée `main.py` (qui réexporte `app.main:app`) — donc
  `api/index.py` et `main.py` ne divergent pas ;
- `frontend` : Next.js, avec un binding `BACKEND_URL` injecté depuis le service
  backend ;
- réécritures : `/api/*` et `/healthz` → backend, `/(.*)` → frontend.

Le frontend lit `BACKEND_URL` **ou** `NEXT_PUBLIC_API_URL`, avec repli
`http://localhost:8000` — et son `next.config.ts` réécrit `/api/:path*` vers
`BACKEND_URL`. C'est cohérent : le navigateur n'appelle jamais le backend en
direct, donc pas de problème de CORS entre Preview et API.

### Conséquence structurelle (voir aussi `01` §4)

`vercel.json` ne déclare **aucun `crons`**. Les deux workers n'ont qu'un
`run_forever()`. Sur un déploiement Vercel, aucun processus ne draine les files :
un import reste `pending`, une analyse reste `queued`. Cette campagne le prouve
par contraste sur la pile réelle (les workers avancent quand ils tournent,
n'avancent pas quand ils sont arrêtés).

Ce n'est pas un défaut du build : c'est une inadéquation entre l'architecture
(cibles longues) et l'hébergement choisi pour la Preview. Anomalie **A-06**.

## 3. SSO : ce que je n'ai pas fait

La Preview est protégée par Vercel SSO. Je n'ai **ni désactivé, ni contourné, ni
affaibli** cette protection, et je ne le ferai pas sans autorisation explicite
distincte. Concrètement :

- je n'ai pas de jeton d'accès Vercel ;
- le réseau sortant de mon environnement n'atteint pas `*.vercel.app` (mesure :
  code `000`, connexion refusée) ;
- je n'ai pas fabriqué de capture d'écran ou de log « simulé » de la Preview.

### Scénarios impossibles à exécuter ici, et pourquoi

| Scénario | Statut | Raison |
|---|---|---|
| Ouvrir la Preview dans un navigateur | **impossible** | SSO + réseau |
| Vérifier que la build sert bien le SHA testé *à l'exécution* | **impossible** | pas d'accès HTTP |
| Lire les logs Vercel d'une fonction | **impossible** | `api.vercel.com` bloqué |
| Lire les variables d'environnement Preview | **impossible** | mêmes raisons |
| Vérifier la présence d'une variable de production dans Preview | **impossible** | idem — **risque résiduel** |
| Mesurer le temps de démarrage à froid | **impossible** | pas d'accès |
| Tester un rollback | **impossible** sans accès Vercel | non exécuté |

**Ces lignes sont la limite principale de ce chantier et sont reprises dans le
verdict.** Elles ne peuvent pas être converties en « validé » par un raisonnement.

## 4. Ce que j'ai pu vérifier du déploiement, autrement

Faute d'accès à la Preview, j'ai vérifié **le contenu déployable** :

| Contrôle | Commande | Résultat |
|---|---|---|
| typecheck frontend | `npm run typecheck` | **0 erreur** |
| build frontend | `npm run build` | **succès** (job CI « Frontend verification ») |
| build backend dans l'image | job CI « Backend container build » | **succès**, UID non root 10001 |
| proxy frontend → API, de bout en bout | `next dev` + `uvicorn` + `tests/http_preview_smoke.py` | **4/4 PASS** (voir §5) |
| démarrage sans `DATABASE_URL` sur une plateforme managée | `VERCEL=1`, `get_settings()` | **refus explicite** après correction |

Autrement dit : le code qui *serait* déployé fonctionne, dans une configuration
équivalente, sur un proxy Next.js réel. Ce n'est pas la même chose que « la
Preview fonctionne », et ce document ne le prétend pas.

## 5. Smoke HTTP à travers le proxy frontend

`backend/tests/http_preview_smoke.py` exécute un vrai `next dev` devant un vrai
`uvicorn` et vérifie :

```
PASS frontend HTML, API proxy, anonymous refusal, dev-login closed, OIDC unavailable explicitly
PASS valid sessions, CSRF refusal, document metadata persistence, two-tenant API isolation and audit verification
PASS disabled real storage fails closed (NOT a successful upload/extraction proof)
PASS server-side revocation and expiration over HTTP
```

Ce script contenait une assertion **périmée** : il comparait le corps entier de
`/api/v1/auth/status` à une forme à trois champs, alors que le contrat publie
aussi `document_upload_max_bytes`. Il échouait donc sur toute version récente.
Corrigé (assertions par champ), et il est resté vert depuis.

## 6. Ce qu'il faut vérifier dans Vercel (assistant ou opérateur)

Liste précise, à exécuter **dans le tableau de bord Vercel**, sans désactiver la SSO :

1. **Projet et branche connectée** : quelle branche alimente l'environnement
   Preview ? (attendu : les branches `arena/*`, dont `arena/0a0b694c-vericlaim`
   pour ce chantier.)
2. **SHA déployé** : la Preview de ce chantier doit correspondre au SHA final
   publié dans `13-verdict-recette-exploitable.md`. Un `READY` sur un autre SHA
   n'est pas une preuve.
3. **Variables Preview** — vérifier la **présence** (jamais la valeur) de :
   `DATABASE_URL`, `AUTH_SESSION_SECRET`, `AUTH_COOKIE_SECURE=true`,
   `FRONTEND_URL`, `CORS_ORIGINS`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
   `OIDC_REDIRECT_URI`, `DOCUMENT_STORAGE_BACKEND=s3`, `DOCUMENT_STORAGE_ENDPOINT`,
   `DOCUMENT_STORAGE_PUBLIC_ENDPOINT`, `DOCUMENT_STORAGE_ACCESS_KEY`,
   `DOCUMENT_STORAGE_SECRET_KEY`, `DOCUMENT_STORAGE_SSE_MODE`,
   `DOCUMENT_SCANNER_MODE=clamav`, `DOCUMENT_CLAMAV_HOST`.
4. **Absence** de : `AUTH_DEV_LOGIN_ENABLED`, de toute valeur de production, de
   toute clé étiquetée Production dans l'environnement Preview.
5. **`/readyz`** sur la Preview, avec un accès autorisé :
   attendu `database: ok`, `document_storage: configured`,
   `malware_scanner: configured`, `document_import: enabled`.
   Si `document_import: refused`, la Preview ne peut pas importer de document — et
   le pilote ne peut pas démarrer.
6. **Workers** : confirmer qu'aucun mécanisme (cron, file externe, service tiers)
   ne draine les files sur cet environnement. Attendu : **aucun**.
7. **Logs** : vérifier qu'un `/api/v1/auth/status` produit une ligne de log
   exploitable et qu'aucun jeton n'y apparaît.

## 7. Verdict de la phase

- Le déploiement Preview existe, est `READY` et correspond à un SHA identifiable.
- Le **contenu déployé** est vérifié (typecheck, build, proxy, smoke HTTP).
- **L'exécution de la Preview n'est pas vérifiée** : SSO + réseau restreint. Les
  scénarios correspondants sont listés en §3 et **restent non exécutés**.
- Un point bloquant a été identifié et corrigé côté code : une Preview sans
  `DATABASE_URL` démarrait sur SQLite au lieu d'échouer.
- Un point bloquant subsiste et dépend de Vercel : **aucun worker**. Voir A-06.
