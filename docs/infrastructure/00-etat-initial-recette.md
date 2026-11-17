# 00 — État initial de la recette

**Date :** 12 novembre 2026
**Environnement examiné :** recette / Preview uniquement
**Branche de travail du chantier :** `arena/0a0b694c-vericlaim`
**SHA de départ :** `b289652a383bfe385c531ff4fb25a5f1535f74c7` (`main`)
**Aucune modification de production, aucune fusion, aucun force-push.**

## 1. Pourquoi ce document existe

La qualification antérieure (`docs/commercialisation/09-qualification-recette-neon-preview.md`,
présente sur la branche de la PR #13) concluait : CI verte, migration Neon staging
réussie, Preview `READY` — mais **parcours utilisateur E2E non validé**. Ce
chantier part de cette limite, pas d'une page blanche.

Consigne de la mission : ne pas refaire les qualifications déjà clôturées. Ce qui
suit distingue donc systématiquement **ce qui a été re-vérifié ici** de **ce qui
est repris de la qualification antérieure**.

## 2. État Git observé

| Élément | Valeur observée | Comment |
|---|---|---|
| `main` locale et distante | `b289652a383bfe385c531ff4fb25a5f1535f74c7` | `git log`, `git fetch --all` |
| Branche de travail | `arena/0a0b694c-vericlaim`, créée depuis `b289652` | note de session |
| PR #13 | ouverte, **DRAFT**, base `main`, tête `6f6fb1f143656c9083860b414406a6929922d861` | `gh pr view 13` |
| PR #7 | ouverte, non fusionnée — **hors périmètre de ce chantier** | `gh pr list` |
| Autres PR (#1–#12) | fusionnées | `gh pr list --state all` |

La branche de la PR #13 (`arena/c5488624-vericlaim`) n'est pas un ancêtre de
`main` : ses fichiers n'existent pas dans mon arborescence de départ. Je les ai
lus **en lecture seule** via l'API GitHub pour la Phase 0, sans les copier dans
ma branche (voir §7, coordination).

### Commits de la PR #13, dans l'ordre

| SHA | Rôle |
|---|---|
| `c0f55a2c8065` | première itération |
| `115c1863ce02` | **SHA applicatif qualifié** (CI verte, Preview `READY`) |
| `81575daee76a` | corrections |
| `8234e2ca8984` | corrections |
| `c80059a63042` | corrections (CI **en échec** sur ce commit) |
| `6f6fb1f14365` | **SHA documentaire** (tête actuelle, CI verte) |

## 3. CI/CD observée

Workflow unique `.github/workflows/continuous-integration.yml`, quatre jobs au
SHA de départ :

| Job | Ce qu'il prouve réellement |
|---|---|
| Backend verification | tests isolés SQLite, tête Alembic, scan de signatures de secrets, intégrité whitespace |
| PostgreSQL tenant isolation (RLS) | **RLS réel** sur un PostgreSQL 16 jetable (4 fichiers de tests) |
| Frontend verification | `npm ci`, `npm audit`, typecheck, build |
| Backend container build | image construite, UID non-root 10001 |

Historique relevé sur la branche de la PR #13 :

- `115c1863` → CI `success` (5 jobs + Vercel) ;
- `6f6fb1f1` → CI `success` (5 jobs + Vercel) ;
- `c80059a6` → CI **`failure`**.

Les workflows `staging-database-migration.yml` et
`production-database-migration.yml` sont manuels (`workflow_dispatch`) et
correctement séparés : le second n'est jamais déclenché par un push.

## 4. Vercel observée

Déploiements listés via l'API GitHub (`/deployments`), qui reflète les
déploiements Vercel connectés au dépôt :

| Déploiement | Environnement | Réf. | État |
|---|---|---|---|
| `7745430154` | Preview | `6f6fb1f1` | `success` — https://vericlaim-axgd3wb8p-…vercel.app |
| `7745416783` | Preview | `c80059a6` | `success` |
| `7745416775` | Preview | `8234e2ca` | `inactive` |
| `7745379471` | Preview | `81575dae` | `success` |
| `7743189374` | Preview | `115c1863` | `success` — https://vericlaim-4iv842mqy-…vercel.app |
| `7744971223` | `staging` | `main` `b289652a` | `success` |
| `7213592895` | **Production** | `b289652a` | `success` (20/10) |

Constats importants :

1. **Un déploiement d'environnement `staging` existe** (réf. `main`), distinct
   des Previews de branche. La cartographie antérieure ne le mentionnait pas.
2. Un environnement **Production** a été déployé le 20/10 sur `b289652a`. Je n'y
   ai **pas touché** : le chantier porte sur la recette.
3. Les Previews restent protégées par SSO Vercel. **Aucune tentative de
   contournement n'a été faite** — l'accès réseau sortant de mon environnement de
   travail est restreint et n'atteint pas `*.vercel.app` (voir
   `04-vercel-preview.md` §3).

## 5. Services externes : ce qui est joignable depuis mon environnement

C'est une contrainte structurante de tout ce chantier. Mesures effectuées :

| Service | Joignable ? | Conséquence |
|---|---|---|
| GitHub (API, git) | **oui** | lecture du dépôt, PR, runs CI, déploiements |
| `registry.npmjs.org`, `pypi.org` | **oui** | installation des dépendances, PostgreSQL embarqué, moteur OCR |
| `api.vercel.com` | **non** (`000`) | impossible de lire les logs Vercel ou les variables Preview |
| `api.neon.tech` | **non** (`000`) | impossible de vérifier Neon directement |
| `*.vercel.app` | **non** | impossible d'ouvrir la Preview dans un navigateur |
| `dl.min.io`, releases GitHub (binaires) | **non** | MinIO et ClamAV non installables |

**Conséquences assumées :**

- Je ne peux ni qualifier ni modifier Neon et Clerk. Ces opérations relèvent de
  l'assistant (ChatGPT), conformément à la répartition de la mission. J'ai
  préparé les demandes techniques précises ; elles sont dans
  `03-neon-et-runtime.md` §6 et `02-authentification-et-identite.md` §7.
- Je ne peux pas fournir de capture d'écran de la Preview ni de ses logs. La
  SSO reste active ; je ne l'ai pas désactivée.
- En revanche, **PostgreSQL 16, un moteur OCR Tesseract 5.5.4 et un client S3
  réel sont installables** dans mon environnement. C'est ce qui rend possible une
  démonstration non simulée du plan de données, documentée dans les documents 01
  à 11.

## 6. Ce que la qualification antérieure affirmait, et ce que j'ai re-vérifié

| Affirmation (doc 09) | Re-vérifié ici | Résultat |
|---|---|---|
| CI verte au SHA `115c1863` | oui, via l'API GitHub | **confirmée** — 5 jobs + Vercel `success` |
| Migration Neon staging réussie sur `main` | **non** — Neon injoignable depuis mon environnement | non re-vérifiée (hors de mon périmètre) |
| Preview `READY` au SHA `115c1863` | oui, via l'API GitHub | **confirmée** — déploiement `7743189374` |
| RLS multi-tenant prouvé sur PostgreSQL jetable | **oui, et reproduit localement** | **confirmée** — 27 tests RLS verts sur PostgreSQL 16.2 local (voir `03-neon-et-runtime.md`) |
| « E2E navigateur non validé » | oui, et c'était le point à traiter | **traité côté plan de données** ; reste limité par la SSO (voir `11-tests-e2e.md`) |
| « Aucune donnée client dans Neon » | non vérifiable | non re-vérifiée |

## 7. Coordination avec le chantier 1

- Branche indépendante (`arena/0a0b694c-vericlaim`), aucun commit sur `main`.
- Les documents `docs/commercialisation/*` **ne sont pas modifiés** : ils
  n'existent pas sur `main` et appartiennent à la PR #13. Je n'ai pas réconcilié
  #13 ni #7.
- Aucun composant métier attribué à l'agent 1 n'a été réécrit. Les quatre
  corrections apportées sont des défauts d'infrastructure et de sécurité qui
  bloquaient la recette (import documentaire, génération de rapport, démarrage
  d'un déploiement managé sans base) : elles entrent dans l'autorisation
  explicite de la mission (« corriger les défauts d'infrastructure et de sécurité
  nécessaires à la recette, sans attendre la fin de l'audit général »).
- **Découverte à signaler à l'agent 1** : les défauts P0/P1 corrigés ici
  (voir `12-registre-anomalies.md`) ne sont **pas** détectables par la suite de
  tests telle qu'elle existait — elle reste verte sur du code qui échoue en
  500 sur PostgreSQL. Toute conclusion d'audit fondée sur « les tests passent »
  doit en tenir compte.

## 8. Livrable de la Phase 0 : verdict

- État de départ établi et daté, avec preuves GitHub vérifiables.
- Deux environnements recette distincts existent : Previews de branche et un
  environnement Vercel `staging`. Le second n'était pas documenté.
- Le plan de données (PostgreSQL, stockage, workers, extraction, OCR,
  authentification OIDC) est **démontrable réellement** dans mon environnement.
- Les opérations Neon et Clerk restent **entièrement à la charge de l'assistant** :
  aucune affirmation de ce dossier ne repose sur une vérification Neon ou Clerk
  que je n'ai pas faite.
