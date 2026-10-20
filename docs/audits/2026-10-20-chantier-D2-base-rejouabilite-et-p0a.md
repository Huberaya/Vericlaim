# Chantier D2 — choix de base prouvé, rejouabilité B/G1, et lot P0-A (limite d'import)

Date : 2026-10-20. Branche de session : `arena/2672a0ba-vericlaim`. Dépôt : `Huberaya/Vericlaim`.
Aucun accès aux bases réelles, aucun déploiement, aucune modification de `main`. Les mesures
ci-dessous ont été prises **après** le correctif, sur le même arbre ; rien n'est déclaré vert sans
sortie observable.

## 1. Verdict

| Question | Réponse |
| --- | --- |
| Base intégrable | `274c6eedab482e0b77e6ed3d44267e0edf0a622d` — **`main` (`36cd831`) en est un ancêtre direct**, donc aucun contenu manquant |
| PR #7 importable ? | **Non** : sa base est un `main` périmé, l'arbre fusionné n'est pas à jour ; seul son *chemin d'exécution* reste à comparer (reporté) |
| Travaux B récupérables ? | **Oui, intégralement** : le patch G1 s'applique **sans conflit** sur l'arbre de A (14 fichiers suivis) + 5 fichiers non suivis restaurés |
| Lot P0-A | **Implémenté et mesuré** : `c147c6435aadc389a3b5830f34d787b541d36a76` |
| Suite de tests | **146 passed, 0 failed, 0 skipped** sous PostgreSQL 16 réel (135 SQLite + 11 RLS) ; baseline avant correctif : 139/0/0 |

## 2. Preuves d'identité (et pourquoi la base a été *prouvée*, pas supposée)

| Objet | Arbre | Parents | Rôle |
| --- | --- | --- | --- |
| `36cd8313…` | `9b253063…` | `f7d5d07…` | `main` — **aucun** chantier B/E/F/G n'a modifié `main` : `git log` de `main` s'arrête à « Add CI workflow and harden Neon bootstrap » |
| `274c6eed…` | `9d04d1cd…` | `36cd831…` | candidat **A** (PR #10, `arena/b48349e4-vericlaim`) — parent = `main` exactement |
| `2fa8df0a…` | `9d04d1cd…` | `36cd831…`, `274c6ee…` | `refs/pull/10/merge` GitHub — **arbre identique à A** → le merge-test n'apporte aucun contenu |
| `5f2cb6c1…` | — | `42426b76…`, `3f56ee0…` | `refs/pull/7/merge` — base `42426b76…` ≠ `main` → **PR #7 non rébasée** : l'importer reviendrait à régresser `main` |

`git merge-base --is-ancestor 36cd831 274c6ee` → **0 (ancêtre)**. Autrement dit, A **contient** tout `main`
plus la sécurité (verrou de chaîne de hash par `pg_advisory_xact_lock`, ETag `IfMatch` à la relecture,
secret de session aléatoire par process, `POSTGRES_PASSWORD` obligatoire, 11 tests d'isolation multitenant
réels en CI). C'est donc la base de travail, et non une branche à fusionner au hasard.

Ce qui **n'a pas** été importé : `refs/pull/7/merge` (et `origin/feat/audit-remediation-c1-c24` = `3f56ee0`) ;
la porte `internal_workers` n'est toujours pas jugée — le chantier G la compare au chemin d'exécution, en
lecture.

## 3. Rejouabilité : le contenu B + F restauré sur A, sans perdre une ligne

Le dépôt principal contient 24 entrées non commitées (14 suivies modifiées, 10 non suivies) issues des
chantiers B/E/F. Le commit `6eaa9a8` (« integrate: replay the document-lifecycle work on the security
branch ») rejoue ce contenu **sur** `274c6ee` : 19 fichiers, +1 679/−95, dont les correctifs de B
(repli SQLite supprimé → 503, `S3StorageUnavailable`, `/readyz`, reconciliation des états, sweep
d'orphelins, page de documents, pagination, bornes d'export, imports par lot côté coffre, garde-fous
Pillow/PyMuPDF) et les 5 fichiers de code/tests qui n'existaient qu'en copie de sauvegarde.

**Un écart volontaire, chiffré** : le hunk du patch G1 qui remontait `MAX_UPLOAD_BYTES` à 50 MiB n'a **pas**
été retenu — il contredisait le frontend et son test d'accord (message relevé par le chantier F). La
limite reste 15 MiB ; la correction réelle est le lot P0-A ci-dessous (le client ne fixe plus la limite).
La borne de 50 MiB reste atteinte par `VERICLAIM_DEV_MAX_UPLOAD_BYTES` (`.env.example`) pour le hors-ligne.

**Preuve de non-perte** : les travaux restent intégralement dans `chantier-g1-sauvegarde/` (hors index) ; la
copie a été **resynchronisée et revérifiée après** le rejou et après P0-A :

| Contrôle | Résultat |
| --- | --- |
| V1 artefacts | 15 OK, 0 FAILED |
| V2 manifeste vs arbre | 24 OK, 0 FAILED |
| V3 archive | 10 membres, identique à `git status` = True |
| V4 applicabilité | `git apply --check` : applicable sans conflit |
| V5 restauration complète dans un clone jetable | 24 fichiers identiques, 0 divergent |
| V6 périmètre | 24 entrées, identique au manifeste = True |

Le patch de préservation est inchangé (55 393 octets) : `chantier-g1-sauvegarde/diffs/HEAD-946dbe7-suivi.patch`.
**Choix de non-rétablissement** : les 4 rapports d'audit non suivis (B-diagnostic, B-exécution, E, F) ne sont
pas remis sous `docs/audits/` — ils citent des commits qui n'existent plus, et les rejouer tel quel ferait
revivre des chiffres faux. Ils restent consultables dans la copie de sauvegarde, et le rapport F est le seul
témoin des travaux F (non réintégrés).

## 4. Lot P0-A — la borne d'import vient du serveur

`c147c6435aadc389a3b5830f34d787b541d36a76` — 11 fichiers, +332/−22.

| Défaut | Correction |
| --- | --- |
| L'instruction renvoyait `max_size_bytes = body.size_bytes` (la déclaration du client) : contrôle anticipe tautologique, mensonge aux intégrateurs | `documents.py` : `max_size_bytes=settings_of(request.app).max_upload_bytes`. Le plafond **réellement** appliqué au POST par la politique (`content-length-range`) reste borné à la déclaration — documenté dans le schéma, et épinglé par test : jamais supérieur à `MAX_UPLOAD_BYTES` |
| Rien ne publiait la borne avant l'appel d'intention ; le frontend compilait une constante qui peut contredire `MAX_UPLOAD_BYTES` | `AuthStatusResponse.document_upload_max_bytes` (défaut `None` → compatible avec un serveur plus ancien) ; `frontend/src/lib/api.ts` expose `noteServerDocumentUploadBound` / `documentImportMaxBytes()`, `lib/auth.ts` alimente le cache depuis `/auth/status` (déjà consulté à chaque ouverture de session), les deux composants (`DocumentUploader`, `SecureDocumentVault`) utilisent la borne publiée et ne comparent plus à la constante |
| Le garde de session de développement lisait le singleton global, l'application peut servir une autre `app.state.settings` | `Settings.settings_of(app, *, fallback)` : la configuration servie fait foi, le module reste la dernière ressource **patchable** — sémantique des tests unitaires existante préservée (le test A « refused unless explicitly opted in » passe sans modification) |

Points **vérifiés sur cette base** (le rapport F, perdu en code, les signalait ouverts) — aucune correction
ajoutée là où le code est déjà juste, mais vérification refaite ici plutôt qu'héritée :

- refus d'autorisation : `require_permission` lève **403** (`app/identity/dependencies.py:103-104`) ; il
  n'existe sur cette base **aucune** route de lecture d'import distincte et **aucune** permission
  `document_import:read` — la seule route d'upload est `POST /api/v1/document-uploads/{id}/complete`
  (`documents.py:321`) sous `documents:manage`. Le point du rapport F ne s'applique donc pas ici : à ne pas
  « corriger » deux fois ;
- `source_filename` : `Field(min_length=1, max_length=500)` (`document_schemas.py:56`) **et** `String(500)`
  en base (`domain.py:548,647`) → aucun champ de 65 536 caractères ;
- `size_bytes` : `Field(gt=0)` (`document_schemas.py:58`) → pas de trou à 0 octet ;
- `dev_login_enabled` : cinq couches fail-closed (défaut `False`, `_bool_from_env` inconnu → défaut, refus
  `is_production_like` dans le garde, `get_settings` qui lève à l'ouverture, route en 404 et `/status`
  honnête) — les trois premières sont épinglées par le test A existant, la cohérence garde/statut par le
  test ajouté ici.

### Non-contournement (extraits du contrat observable)

| Cas de test | Résultat attendu et mesuré |
| --- | --- |
| `/auth/status` == limite effective, et == `max_size_bytes` de l'instruction | OK |
| Instruction après override `max_upload_bytes=256` | la borne publiée suit, la déclaration du client non crue |
| Déclaration 58 o, objet stocké 1 033 o, plafond 256 o → complétion | **422**, `version: null`, upload `rejected`/`size_mismatch`, document `failed`, objet retiré de la quarantaine, événement `document.upload_rejected` |
| Déclaration > plafond (257 pour 256) | **422 avant** toute signature de politique (`upload_requests` inchangé) |
| Plafond de la politique | `== taille déclarée` et `<= MAX_UPLOAD_BYTES` |
| Contrat frontend (parité lue statiquement, sans `npm test`) | champ présent dans les types, helpers dans `api.ts`, branchés dans `auth.ts`, plus aucune comparaison directe à la constante dans les deux composants |

### Mesures, après le correctif

- `pytest -q` **avec** `VERICLAIM_TEST_POSTGRES_URL` (PostgreSQL 16.2, cluster jetable du workspace) :
  **146 passed, 0 failed, 0 skipped** en 12,96 s. Les 11 tests RLS sont réellement exécutés (pas de skip compté
  comme succès).
- `pytest -q` sans PostgreSQL : 135 passed, 11 skipped (les 11 sont les tests qui exigent le serveur).
- `npx tsc --noEmit` : 0 erreur ; `npm run build` : 0 erreur ; `npm audit --omit=dev` : 0 vulnérabilité.
- OpenAPI après correctif : `AuthStatusResponse` publie `document_upload_max_bytes` en
  `anyOf[int, null]`, requis = `oidc_configured` uniquement ; `DocumentUploadInstructionResponse.max_size_bytes`
  reste un `integer` requis.

## 5. Environnement, périmètre et suites

- Travail dans un worktree dédié `/home/user/wt-p0` (branche `p0-integration`), **jamais** sur `main`, jamais
  sur la ref distante d'un autre chantier. Venv recréé (`backend/.venv`, `pip install -r requirements.txt`,
  imports vérifiés), `node_modules` recréé (`npm ci`), cluster PostgreSQL 16 via `pgserver==0.1.4` et
  `pgenv2`/`pgdata` **dans le workspace** — rien de tout cela ne survit hors du dépôt : c'est la limite connue
  de l'environnement, documentée au chantier G1, et la raison pour laquelle la copie de préservation est
  **dans** le dépôt et non à côté.
- Aucun secret manipulé ni archivé : la sauvegarde contient 24 entrées, aucune `.env`, aucun jeton ; les deux
  lignes de DSN du rapport F sont des URL de socket locale sans mot de passe, et n'ont pas été réintégrées.
- Suites du chantier G (lots 2-4, revue indépendante en lecture) : **non commencées ici** — elles portent sur
  la sûreté des transactions et la reprise, et seront menées sur la base ci-dessus, en lecture d'abord.
- **Reste à faire, dans l'ordre** : (P0-B) verrou d'audit append-only et concurrence RLS sur PostgreSQL réel,
  y compris les deux corrections transverses attendues d'approbation (D1 : `pg_advisory_xact_lock`
  manquante dans `audit.append` ; D2 : `_reassert_tenant_context` après chacun des cinq commits de purge du
  worker) ; (P0-C) rejet d'une réponse ClamAV ambiguë, `scan_error_code` et `quarantine_deleted` honnêtes,
  bornes et *fencing* des tentatives du worker ; (P1) cycle de vie et purge — purger l'objet **après** le
  commit de transition, marqueur posé **avec** ce commit, sweep ligne par ligne, plus `quarantine_deleted`
  dans `_record_rejection` qui dit « pas supprimé » alors qu'il n'y avait rien à supprimer.
- La CI de la branche va désormais exécuter `test_upload_bound_contract.py` **sans** PostgreSQL ni npm : la
  parité de contrat frontend y est vérifiée par lecture statique des fichiers du dépôt, pour ne pas créer de
  faux négatif ; le test saute si `frontend/src` est absent du checkout.
