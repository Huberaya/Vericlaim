# Chantier B — exécution locale et livraison sans push

## A. État initial vérifié

- Dépôt `/home/user/Vericlaim`, `Huberaya/Vericlaim`.
- Branche unique imposée par la session : `arena/b48349e4-vericlaim`. Impossible de créer une branche distincte dans cette session ; les changements du chantier sont isolés en commits locaux après `274c6eedab482e0b77e6ed3d44267e0edf0a622d`.
- Arbre initial propre ; `git fetch origin` exécuté (lecture du distant, pas de push). Main reste `36cd8313434a3b51a2428e676d5e5d8a851ee8e2`.
- PR #10 ouverte, head distant `274c6ee`, backend/PostgreSQL/frontend verts (run 39332146846). PR #9 ouverte, head `8cf78a3`, backend/PostgreSQL et Preview verts. PR #7 ouverte, head `3f56ee0` : backend/frontend/restauration verts, revue des chemins sensibles et Vercel en échec. Aucun de ces contrôles ne couvre les nouveaux commits locaux.
- Aucun fichier AGENTS.md trouvé. Scripts réellement disponibles : pytest, Alembic, frontend typecheck/build/dev/start. Pas de script frontend test/lint ni configuration dédiée de lint/typecheck Python constatée.
- Aucun changement préexistant non commité absorbé. Aucun fichier auth, modèle, migration ou API modifié dans ce chantier.

## B. Environnement et cibles

| Composant | Constat réel |
|---|---|
| Python | 3.11.2, venv existant, imports pytest/httpx/psycopg/pgserver réussis |
| Node/npm | 22.22.3 / 10.9.8, dépendances frontend déjà installées |
| PostgreSQL | 16.2 local isolé sous `.cache/pgdata`, répond effectivement à SQL ; aucune base partagée utilisée |
| Rôles | `postgres` uniquement pour provisionner/supprimer les bases jetables des fixtures ; migrations avec `vericlaim_migrator`, contrôles métier/RLS avec `vericlaim_app` non propriétaire, sans SUPERUSER/CREATEDB/CREATEROLE/BYPASSRLS |
| Docker / Compose | binaire docker absent, Compose non exécuté |
| ClamAV | clamd/clamscan absents ; aucun moteur ni signatures réels validés. Tests du protocole avec un pair TCP local explicitement simulé |
| Stockage | aucune configuration S3/MinIO fournie au processus ; stockage réel non validé. Adaptateurs de test pour les tests documentaires ; stockage désactivé et refus 503 dans la preview HTTP |
| API/frontend locaux | API redémarrée sur le code corrigé, Uvicorn `0.0.0.0:8000`, Next `0.0.0.0:3000`, proxy serveur relatif `/api`, SQLite dédiée `.cache/preview.db`, dev-login désactivé, secret local éphémère ignoré par Git |

### Neon — contrôle explicite, aucune cible présumée autorisée

- Aucun nom de variable NEON/DATABASE/S3/CLAM/OIDC disponible dans l'environnement du processus au démarrage (noms seulement inspectés, aucune valeur affichée).
- Chargement du connecteur Neon : statut `unsupported`. Aucun appel à une API Neon effectué.
- `gh secret list --env staging` : **403 Resource not accessible by integration** ; aucune valeur de secret demandée ou lue.
- `gh variable list --env staging --json name` : commande non disponible dans cette version de gh ; repli en lecture `gh api repos/Huberaya/Vericlaim/environments/staging/variables --jq '.variables[] | .name'` : **403**.
- Les environnements GitHub Preview/Production/staging existent ; cela n'identifie ni projet, ni branche, ni base Neon, ni rôle effectif, ni autorisation de test.
- Réseau autorisé limité à GitHub/npm/PyPI : les hôtes Neon, Vercel, OIDC et stockage ne sont pas autorisés depuis cet environnement.
- **Neon : bloqué / non exécuté. Aucun SQL, migration, écriture, suppression ou création de branche Neon.** Les preuves PostgreSQL ci-dessous restent strictement locales.

## C. Parcours et contrôles

| Étape | Composants et preuve disponible |
|---|---|
| Importation | `api/v1/documents.py`, `documents/service.py` ; permissions et CSRF, création d'une intention privée, clé opaque générée côté serveur et organisation vérifiée |
| Validation | `documents/security.py` ; taille, extension/MIME/signature, TXT UTF-8, fichiers vides, noms sans chemins/contrôles ; tests existants |
| Quarantaine | POST S3 signé, compartiment distinct, upload expiré/rejeté non promu ; tests taille/type/checksum/erreurs et idempotence de finalisation |
| Antivirus | `documents/scanner.py` ; commande clamd INSTREAM, aucun parseur avant verdict propre ; nouveau refus des réponses incomplètes/ambiguës, timeout sûr |
| Promotion | GET conditionné par ETag puis COPY conditionnelle (correctif déjà présent au départ), refus si source remplacée ; stockage privé configuré en staging requis |
| Extraction | `documents/extraction.py`, worker documentaire ; SHA-256 relu avant parseur, lease durable, retries bornés, transcript et segments persistés, offsets et hash source |
| Analyse | `analyses/service.py` et worker de détection ; versions persistées, détection déterministe, citations attachées aux segments et entrées idempotentes. Aucun nouveau score/verdict/citation fabriqué par ce chantier |
| Consultation | URL de téléchargement courte après contrôle tenant ; segments consultables après extraction ; tests RBAC/CSRF/inter-tenant |
| Interface | `SecureDocumentVault.tsx` relu : quarantaine, contrôle, extraction pending/running/completed/review_required/failed, polling, erreur de rafraîchissement et relance. Pas de défaut d'affichage reproduit, aucun changement frontend |

La signature sommaire d'un PDF ne prouve pas qu'il est parsable : l'extraction est une étape distincte qui peut échouer sans faire passer ce fichier pour extrait. Les tests ne prétendent pas qu'un header PDF constitue une validation antivirus.

### Défauts reproduits et corrigés

| ID | Gravité | Attendu / observé et cause | Correction / clôture locale |
|---|---|---|---|
| B-AV-01 | Haute | Un verdict incertain doit refuser la promotion. Avant correction, le test TCP obtenait une acceptation pour `stream: OK` sans terminateur, préfixe étranger, `ERROR OK`, newline normalisée, plusieurs réponses dans le même fragment ; un UTF-8 invalide était aussi toléré. Cause : test du suffixe, `.strip()`, EOF accepté et décodage avec remplacement | Réponse NUL-terminée bornée à 16 Kio, décodage strict, contrôles de caractères et de cadre, seul `stream: OK` propre ; 6 échecs observés avant correction, nouveaux tests verts après |
| B-AV-02 | Moyenne | Le timeout par lecture pouvait être renouvelé par chaque fragment lent | Deadline monotone commune à transmission/réception ; test déterministe de fragments lents. La résolution DNS système n'est pas prouvée bornée par ces tests |
| B-JOB-01 | Haute | Une tentative ancienne, après expiration/reprise avec le même worker_id, devait être refusée. Avant correction les 3 tests échouaient : ancien traitement/publication accepté, échec ancien altérant l'état courant. Cause : worker_id utilisé seul comme identité de lease | Comparer aussi attempt_count au traitement, publication et enregistrement d'erreur ; rafraîchir l'identité ORM lors des relectures verrouillées. La tentative courante réussit une fois, son rejeu ne réécrit rien |
| B-RLS-01 | Couverture | Les preuves PostgreSQL existantes ne peuplaient pas les tables documentaires | 4 tests supplémentaires avec `vericlaim_app` sur documents/versions/jobs, lectures sans contexte et croisées, insert/move refusés par RLS, update/delete étrangères sans effet ; aucune migration ni politique modifiée |

### Fichiers / commits locaux

- `82d5976` : `backend/app/documents/scanner.py`, `backend/tests/test_clamav_protocol.py`.
- `a3e4239` : `backend/app/documents/extraction.py`, `backend/tests/test_document_lease_fencing.py`.
- `17ff471` : `backend/tests/test_postgres_document_isolation.py`, ajout de ce fichier à la commande du job PostgreSQL dans `.github/workflows/continuous-integration.yml` (une seule ligne CI modifiée).
- Le commit de ce rapport suit les trois commits ci-dessus ; son SHA final est donné dans le message de livraison après rejeu des contrôles. Aucun push.

## D. Commandes et preuves

Toutes les commandes Python ci-dessous sont lancées depuis `backend`, sauf indication contraire. Les valeurs des secrets ne figurent pas dans ce document.

| Commande | État / code / résultat observé |
|---|---|
| `APP_ENV=test ../.venv/bin/python -m pytest -q tests/test_clamav_protocol.py` avant correction | **Échoué, 1** : 6 failed, 6 passed sur base initiale + nouveau test |
| `APP_ENV=test ../.venv/bin/python -m pytest -q tests/test_document_lease_fencing.py` avant correction | **Échoué, 1** : 3 failed sur commit scanner + nouveau test |
| `APP_ENV=test ../.venv/bin/python -m pytest -q tests/test_clamav_protocol.py tests/test_secure_documents.py` après correction et ajout API/deadline | **Réussi, 0** : 32 passed avant commit scanner |
| `APP_ENV=test ../.venv/bin/python -m pytest -q tests/test_document_lease_fencing.py tests/test_secure_documents.py` | **Réussi, 0** : 20 passed avant commit extraction, nouvelle tentative aboutit et pas de double publication |
| `APP_ENV=test VERICLAIM_TEST_POSTGRES_URL='postgresql://postgres@/postgres?host=/home/user/Vericlaim/.cache/pgdata' ../.venv/bin/python -m pytest -q tests/test_postgres_document_isolation.py` | **Réussi, 0** : 4 passed, vrai PostgreSQL local et rôle runtime |
| Même préfixe PostgreSQL puis `python -m pytest -q tests/test_postgres_tenant_isolation.py tests/test_postgres_document_isolation.py tests/test_database_migration_workflow_safety.py` | **Réussi, 0** : 23 passed avant commit tests PostgreSQL |
| `APP_ENV=test VERICLAIM_TEST_POSTGRES_URL='postgresql://postgres@/postgres?host=/home/user/Vericlaim/.cache/pgdata' ../.venv/bin/python -m pytest -q` | **Réussi, 0** sur `17ff471` : **132 passed, aucun skip** ; comprend 15 tests PostgreSQL/RLS, les 18 nouveaux tests protocole/lease et la suite existante |
| `npm run typecheck && npm run build && npm audit` depuis frontend | **Réussi, 0** sur l'état `a3e4239` (frontend inchangé depuis le départ), Next 16.4.2, zéro vulnérabilité |
| `DATABASE_URL='postgresql+psycopg://vericlaim@db.invalid:5432/vericlaim' AUTO_CREATE_SCHEMA=false ../.venv/bin/python -m alembic heads` | **Réussi, 0** sur `17ff471` : `e8f9a1b2c3d4 (head)` ; migrations online depuis base vierge exécutées par les fixtures PostgreSQL, pas par cette commande offline d'inventaire |
| `git diff --check 274c6ee HEAD` depuis racine | **Réussi, 0** sur `17ff471` |
| Scan des signatures de secrets identique à CI, commande ci-dessous | **Réussi, 0** : aucune correspondance ; ce n'est pas une garantie d'absence de toutes les formes de secrets |
| Parsing YAML de `.github/workflows/continuous-integration.yml` avec `yaml.safe_load` | **Réussi, 0**, 3 jobs lus |
| CI distante des nouveaux commits | **Non exécuté**, push interdit. La CI verte de #10 concerne uniquement `274c6ee` |
| Lint et tests frontend dédiés | **Non exécuté**, scripts absents ; contrôle de types et build exécutés |
| Neon, ClamAV réel, S3/MinIO réel, navigateur E2E et staging complet | **Bloqué / non exécuté**, voir accès/services ci-dessus |

Scan depuis racine :

```sh
! git grep -nE -- '(npg_[A-Za-z0-9_-]{8,}|napi_[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk_(test|live)_[A-Za-z0-9_-]{12,})'
```

### HTTP local réel (pas un login OIDC ni un upload réel)

API redémarrée après les corrections. Commande exacte depuis backend :

```sh
APP_ENV=development AUTH_DEV_LOGIN_ENABLED=false \
DATABASE_URL=sqlite:////home/user/Vericlaim/.cache/preview.db \
AUTH_SESSION_SECRET="$(cat ../.cache/preview-session-secret)" \
VERICLAIM_ALLOW_LOCAL_HTTP_SEED=true \
../.venv/bin/python -m tests.http_preview_smoke
```

**Code 0 sur `17ff471`**, quatre groupes PASS : HTML/proxy/refus anonyme/dev-login fermé ; sessions réelles provisionnées directement dans la base jetable, CSRF et séparation des métadonnées ; stockage absent refuse 503 ; révocation et expiration rejettent le jeton. Aucun faux stockage/scanner branché dans cette API locale. L'import/extraction/rapport complet avec services réels n'est pas revendiqué.

Avertissements non bloquants : dépréciations Starlette/httpx et fitz. Aucun test pertinent supprimé ni sécurité désactivée pour obtenir une suite verte.

## E. Coordination et PR #7 — intégration sélective à préparer, non exécutée

- Les modifications actuelles de scanner/extraction chevauchent des fichiers de #7 : ne pas les remplacer par les versions de #7, qui ne contiennent pas ces nouveaux correctifs. La PR #7 contient des ajouts de probe sur scanner, pas la correction du parseur clamd.
- Extraction #7 dépend de facturation/enforcement/plans et de l'évolution du schéma/segments. Service/storage/workers dépendent aussi de readiness, logging, metrics et rapports. Ne pas importer ces fichiers isolément ni cherry-pick le chantier en bloc.
- Séquence recommandée au responsable : garder les barrières ETag et attempt_count ; réconcilier d'abord les migrations et politiques RLS ; intégrer ensuite les interfaces billing/quota nécessaires ; fusionner manuellement extraction et observabilité ; enfin ordonnanceur authentifié et frontend, avec rejeu PostgreSQL/docs et services réels.
- Risque de la sonde S3 #7 à examiner avant adoption : son `ThreadPoolExecutor` en context manager peut attendre le thread à la fermeture malgré le timeout de la future. Risque identifié par lecture, **non reproduit dans cette session**, pas annoncé corrigé.
- Handoff au responsable du moteur/API : `analyses/service.py` compare aussi worker_id sans attempt_count aux lignes de traitement/publication/échec. **Risque apparent non reproduit ici**, aucun changement concurrent de ce fichier entrepris ; vérifier et appliquer le même principe si confirmé.
- Absence de sous-agent ou de canal de coordination temps réel : aucun travail d'un autre agent simulé. Le SHA distant inchangé vérifié au démarrage et le périmètre de chaque commit sont les éléments de coordination disponibles.

## F. Livraison et limites opérationnelles

Les correctifs sont **appliqués et commités localement**, avec tests réalisables réussis. Aucun push, création/modification/commentaire de PR, merge, workflow distant, déploiement externe ou modification de production exécuté pour ce chantier. Les seules écritures DB concernent les bases locales jetables explicitement créées pour les tests/preview.

Avant push éventuel : obtenir l'autorisation explicite, refaire fetch et comparaison du head distant, inspecter les changements concurrents, puis rejouer les tests si réconciliation nécessaire.

Avant validation opérationnelle complète : fournir par le mécanisme sécurisé de l'environnement une cible Neon isolée autorisée (projet/branche/base identifiés et rôles vérifiés), autoriser son réseau, fournir S3 privé et ClamAV avec signatures à jour, démarrer les workers et exécuter import → scan → promotion → extraction → analyse → résultat/rapport et accès inter-tenant avec services réels. Contrôler visuellement le frontend avec navigateur. Le staging et le produit complet ne sont pas déclarés validés.
