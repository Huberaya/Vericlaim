# Chantier G — revue indépendante des corrections PostgreSQL : arrêt motivé au lot 1

Date : 2026-10-20. Mandat : auditer le candidat `79af1e7714a354433c474b2f2966bbf7d43deaa5` et le rapport
`docs/audits/2026-10-20-chantier-F-cycle-de-vie-des-purges.md`.

**Résultat : le lot 1 échoue. Le candidat annoncé n'est pas accessible et l'historique ne correspond pas au
rapport. Conformément à la clause d'arrêt du mandat, aucune modification n'a été tentée — aucun correctif,
aucune re-création de worktree, aucun fetch, aucune opération sur une référence distante.** Lots 2 à 5 :
non exécutés, faute d'objet à auditer. Ce document tient lieu de preuve de l'échec, d'inventaire de perte et
de procédure de reprise.

---

## 1. Ce que le dépôt est réellement, aujourd'hui

| Contrôlé | Résultat relevé |
| --- | --- |
| `git rev-parse --abbrev-ref HEAD` | `arena/2672a0ba-vericlaim` (branche de session Arena — voir note §6) |
| `git rev-parse HEAD` | `36cd8313434a3b51a2428e676d5e5d8a851ee8e2` — **le SHA de `main`** |
| `git log --oneline --all \| wc -l` | `1` commit dans toutes les refs confondues |
| `git reflog --all` | 4 lignes, toutes `clone:` ou `branch: Created from 36cd831…` — **aucun commit n'a jamais été enregistré dans ce `.git`** |
| `git branch --list` | `arena/2672a0ba-vericlaim`, `main` seulement. Pas de `chantier-e-candidat` |
| `git tag --list` | vide. Le tag `candidat-F-20261020` annoncé par le rapport F n'existe pas ici |
| `git worktree list` | un seul worktree : `/home/user/Vericlaim`. `/home/user/wt-cand` et `/home/user/wt-A` sont **absents du système de fichiers**, et `.git/worktrees/` est vide |
| `git cat-file -t 79af1e7714a3…` | `fatal: git cat-file: could not get object info` — **l'objet n'existe pas** |
| `git count-objects -v` | `loose: 0`, `in-pack: 231`, un seul pack (celui du clone) |
| `git fsck --unreachable --no-progress` | aucune sortie : **aucun objet enfoui à récupérer** |
| `git stash list` | vide |
| `du -sh .git` | 636 Ko — la taille d'un clone frais, pas celle d'un dépôt porté par cinq chantiers |
| `b3ab1fb` (commit du rapport F) | `absent du dépôt distant (jamais poussé)` et absent d'ici : **son contenu fichier survit, son commit non** |

Vérification sur le dépôt distant (lecture seule, `gh api repos/Huberaya/Vericlaim/commits/<sha>`) :

```
31af11e90439e194f90a287dcd43d2c2a8e17461 -> absent du dépôt distant (jamais poussé)
0b042933f2c94c29421fffef052bd2e02b8759ca -> absent du dépôt distant (jamais poussé)
79af1e7714a354433c474b2f2966bbf7d43deaa5 -> absent du dépôt distant (jamais poussé)
8d557d0 (rapport E) / b3ab1fb (rapport F) -> absents du dépôt distant (jamais poussés)
```

C'est cohérent avec le mandat (aucun push autorisé) : les travaux B, E et F n'ont **que** des copies locales.
Quand l'environnement local a été reconstruit, ces copies locales ont disparu avec lui. Les refs distantes,
elles, sont intactes et ne contiennent pas mon travail : `main` = `36cd831`, `arena/b48349e4-vericlaim` =
`274c6ee` (branche d'A, PR #10), `feat/audit-remediation-c1-c24` = `3f56ee0` (PR #7), plus `arena/81e77eaa…`,
les branches `feature/*` et les `refs/pull/*` — rien d'autre.

## 2. Ce qui a survécu, et à quel niveau de fidélité

L'arbre de travail restauré contient **le contenu** des fichiers du chantier B, en **modifications non
commitées** (14 fichiers suivis : +688 / −95 selon `git diff --stat` ; 5 fichiers nouveaux côté backend et
frontend), plus les cinq rapports d'audit en fichiers non suivis :

```
backend/app/api/v1/{catalog,documents,pagination}.py   backend/app/core/database.py
backend/app/documents/{service,extraction}.py          backend/app/main.py
backend/app/models/document_schemas.py                 backend/app/workers/document_extraction_worker.py
backend/tests/{document_lifecycle_support,test_database_startup_safety,
              test_document_extraction_boundaries,test_document_pipeline_states}.py
frontend/src/{app/globals.css,lib/api.ts,lib/types.ts,components/DocumentUploader.tsx,
              components/SecureDocumentVault.tsx}      docker-compose.yml
docs/audits/2026-09-25-origin-main-branch-audit.md     (211 lignes)
docs/audits/2026-10-19-chantier-B-diagnostic-…md       (228 lignes)
docs/audits/2026-10-20-chantier-B-rapport-d-execution.md (497 lignes)
docs/audits/2026-10-20-chantier-E-reconciliation-A-B.md  (284 lignes)
docs/audits/2026-10-20-chantier-F-cycle-de-vie-des-purges.md (231 lignes, sha256 4ab59d5142d65cf7…e03339)
```

**Le contenu du chantier F n'est pas dans cet arbre.** Preuve par recherche, sur les huit symboles que F a
introduits, dans `backend/app`, `backend/tests` et `frontend/src` :

| Symbole propre à F | Fichiers contenant |
| --- | --- |
| `_reassert_tenant_context` | 0 |
| `retry_retained_document_purges` | 0 |
| `PrefixDeletionResult` | 0 |
| `pg_advisory_xact_lock` | 0 |
| `document_upload_max_bytes` | 0 |
| `noteServerDocumentUploadBound` | 0 |
| `upload_object_purge_retained` | 0 |
| `DocumentPurgeReport` | 0 |

Le schéma d'identité est, lui aussi, d'avant-A-et-F : `backend/app/models/identity_schemas.py:50` ne déclare
que `oidc_configured` et `authentication_required` — ni `dev_login_enabled` (apporté par A), ni
`document_upload_max_bytes` (apporté par F). Et le balayage restauré est bien la **version d'avant-F** : `backend/app/documents/service.py` contient encore
4 occurrences de la clé d'audit mensongère `quarantine_deleted` que F supprimait, sa boucle
`for upload in candidates:` appelle `_best_effort_delete(...)` **sans aucun `db.commit()` par ligne** —
c'est exactement le défaut F-4 décrit au rapport F. Les deux modules de test de F
(`test_document_purge_ordering.py`, `test_postgres_purge_concurrency.py`) sont absents.

Autres pertes, toutes situées **hors de la racine du dépôt** (seule `/home/user/Vericlaim` est persistée par le
bac à sable) : `/home/user/wt-cand`, `/home/user/wt-A`, `/home/user/pgenv` et `/home/user/pgdata` (cluster
PostgreSQL 16.2 de `pgserver`), `/home/user/backups` (bundle `candidat-F-20261020`, patch de 6 612 lignes,
`audits-F.tar.gz`), `/home/user/logs` (F-01 à F-06, c'est-à-dire **les journaux bruts des 155 tests, des coursives
et des cinq cas de démarrage**), `/home/user/tools/smoke_e.py`, `backend/.venv` (absent ; `backend/requirements.txt`
fait 16 lignes) et `frontend/node_modules` (absent ; `frontend/package-lock.json` est présent).

## 3. Conséquence sur les allégations du rapport F

Le rapport F est **intact** et reste lisible tel quel (231 lignes, empreinte ci-dessus). Mais dans le présent
environnement, aucune de ses mesures n'est reproductible ni vérifiable :

- les chiffres (155 passed / 0 skipped, coursives à 3 workers, cinq cas de démarrage avec codes de sortie 0/1/3,
  18/18 avec dev-login et 4/4 en anonyme en HTTP, 0 vulnérabilité npm) portent sur un arbre et des journaux qui n'existent plus ici ;
- les deux corrections transverses du §3 du rapport F (verrou consultatif à la place du verrou de ligne dans
  `append_audit_event` ; réinstallation du contexte locataire après chaque commit) ne sont **pas dans le code
  présent** : il n'y a donc rien à auditer au lot 2 ;
- la correction F-6 (l'instruction d'import renvoyait la taille déclarée par le client) n'est **pas non plus**
  dans le code présent — `grep max_size_bytes app/api/v1/documents.py` montre toujours l'ancien
  `max_size_bytes=body.size_bytes` ; le défaut que F corrigeait est donc, ici, **toujours ouvert** : `backend/app/api/v1/documents.py:313` porte encore
  `max_size_bytes=body.size_bytes`.

Un auditeur honnête ne peut pas conclure « le candidat est bon » à partir d'un rapport, ni conclure « il est
mauvais » à partir de l'absence d'un objet : la seule issue est l'arrêt motivé du §1.

## 4. Procédure de reprise, avec le coût de chaque option

R1 — **Restaurer l'environnement précédent** (le plus court, si la plateforme le permet). Demander le snapshot
du bac à sable qui contenait `/home/user/wt-cand`, `/home/user/backups` et `/home/user/pgdata`. Si ce snapshot
existe, le SHA `79af1e7…` redevient l'objet d'audit et les lots 2 à 5 s'enchaînent sans reconstruction.

R2 — **Reconstruire le candidat localement**, puis auditer le nouveau SHA. Ordre exact :

```bash
cd /home/user/Vericlaim
git fetch origin refs/heads/arena/b48349e4-vericlaim:refs/heads/arena-b48349e4   # A = 274c6ee
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci && cd ..
# 1. rejouer les modifications non commitées du chantier B : elles sont dans l'arbre, il faut les committer
#    sur une branche de travail dédiée, ou les retrouver dans un bundle si R1 aboutit.
# 2. recréer le candidat : merge de A sur la base B (commande et résultat d'origine au rapport E §5).
# 3. ré-appliquer le changement de 17 fichiers décrit par le rapport F §2 et §4, table par table.
# 4. ré-écrire les deux modules de test de F (§5 du rapport en donne les noms exacts).
# 5. remonter le PostgreSQL jetable : python3 -m venv /tmp/pgenv && /tmp/pgenv/bin/pip install pgserver==0.1.4
#    puis get_server(Path('<répertoire-cluster>'), cleanup_mode=None) ; cluster dans la racine persistée,
#    PAS dans /home/user/pgdata.
```

Coût et mise en garde : la reconstruction produit **un nouveau SHA**. Les chiffres du rapport F ne sont **pas**
transférables : chaque mesure (suite complète avec `VERICLAIM_TEST_POSTGRES_URL`, quatre coursives, cinq cas de
démarrage, `tsc`/build/`npm audit`, 18/18 et 4/4 HTTP, `verify_runtime`) doit être **re-jouée sur ce nouveau SHA**
et re-consignée. Toute autre conduite consisterait à présenter un commit antérieur comme preuve d'un état final.

R3 — **Auditer G après R1 ou R2.** Les questions du mandat, une fois l'objet reconstitué, se règlent avec :

- *verrou consultatif* : contrôle que la clé est `hashtext(organisation)` (donc collision possible entre
  organisations — sans conséquence d'exactitude : la collision n'ajoute qu'une sérialisation excédentaire), que le verrou est posé
  **avant** la relecture de la tête et tenu jusqu'au commit (sinon la fenêtre de fourche subsiste), qu'un seul
  verrou est pris par transaction (pas d'ordre d'acquisition → pas d'interblocage), et que `03_verify_runtime.sql`
  continue d'affirmer `audit_events_update_allowed = false` / `…delete_allowed = false` sur le rôle applicatif ;
  test d'écriture concurrente élargi (au moins 8 writers × 16 événements, assertion : zéro fourche, une origine,
  `count = 128`) ;
- *contexte locataire* : trace des cinq points de commit (`grep -n "db.commit()" backend/app/documents/service.py`
  croisé avec `_reassert_tenant_context`), test en deux temps sur PostgreSQL réel — insérer un audit après un
  commit **sans** réinstallation doit échouer (`new row violates row-level security policy for table "audit_events"`),
  **avec** réinstallation doit réussir ; preuve que l'organisation vient de la session authentifiée et jamais d'un
  en-tête ou d'un paramètre de requête (recherche de tous les appelants de `set_db_request_context`) ; test de
  fuite : deux `Session` successives sur la **même connexion du pool**, la seconde sans contexte doit lire
  0 ligne ; multi-locataires, multi-lignes, reprise et erreur intermédiaire ;
- *F-6 et portails* : assertion que la politique du POST reçoit la borne du serveur quelle que soit la taille
  déclarée (`storage.upload_requests[-1][3] == settings.max_upload_bytes`), refus serveur d'un client qui
  déclare petit et envoie gros (rejet à la complétion, pas à l'instruction), `/auth/status` cohérent avec
  `MAX_UPLOAD_BYTES` surchargé, et consommation réelle du champ par le frontend ;
- *lots 4* : simulation déterministe de mort du worker (processus qui `os._exit(9)` **entre** le commit de
  transition et la libération de l'objet), puis passe de reprise : reprise effective, aucune double publication,
  aucun état terminal faux, audit conservé, objet orphelin traité, et concurrences à 4 et 8 workers. **Point de
  contrôle à trancher lors de la reconstruction** : la fenêtre entre le commit de transition et la libération ne
  doit pas être soldable « propre » avant la libération — sinon la ligne ne correspond plus à aucune reprise et
  l'objet reste orphelin pour toujours. Le marqueur de reprise doit donc être posé **avec** la transition, pas
  seulement en cas d'échec de la libération ; c'est vérifiable par le test ci-dessus et cela doit être consigné
  comme exigence, pas comme intention ;
- *`internal_workers`* : comparaison porte par porte avec `3f56ee0` (PR #7), lecture seule — la PR #7 n'est pas
  fusionnée et ne doit pas l'être en bloc.

## 5. Écarts, risques, prévention

- **Risque principal, réalisé** : un chantier dont les livrables sont des commits locaux et des sauvegardes
  posées hors de la racine du dépôt perd **tout** au premier renouvellement du bac à sable. Le mandat
  « ne pas pousser » est juste, mais il laissait comme seule copie un objet non persisté. Le rapport F notait
  « sauvegarde indépendante vérifiée dans `/home/user/backups/` » : cette sauvegarde, précisément, n'était pas
  indépendante du bac à sable.
- **Prévention à adopter pour le prochain chantier** (sans changer le mandat) : bundle **dans** la racine du
  dépôt (`git bundle create preuves/candidat-<sha>.bundle --all`, répertoire non suivi, ajouté à `.gitignore` mais
  capturé par le snapshot), `git format-patch` à côté, et journaux de tests écrits sous `docs/audits/preuves/`.
  Un `git push origin <branche-de-session>` sur autorisation explicite du pilote reste la seule copie vraiment
  indépendante du bac à sable.
- **Écart mineur consigné au passage** : `docker-compose.yml`, modifié par B, reste non committé dans l'arbre
  restauré — son état doit être vérifié au moment de la reprise, pas supposé.
- **Non-vérifié, donc non affirmé** : Neon, MinIO réel, ClamAV réel, OCR, eIDAS. Rien dans le présent mandat ne
  permettait de les valider, et l'environnement qui les simulait n'existe plus.

## 6. Note sur deux instructions du mandat, non exécutables telles quelles

1. « Rester sur `arena/b48349e4-vericlaim` » : cette ref est la branche **d'A**, et la session Arena est liée à
   `arena/2672a0ba-vericlaim` ; y basculer aurait cassé le suivi de session et rien d'utile n'y aurait été écrit.
   Aucune bascule de branche n'a donc été faite, et la ref distante d'A n'a pas été touchée — l'intention
   (ne pas travailler sur une autre branche que celle de la session, ne rien toucher d'autre) est respectée.
   Si le pilote veut que l'audit porte **sur le contenu de la branche d'A**, il faut me le dire explicitement :
   ce serait alors un autre objet que le candidat `79af1e7`.
2. « Produire un rapport … et repartir » supposait un objet auditable. Il n'y en a pas : les sections 2 à 5 du
   mandat sont donc **en attente**, pas en succès, pas en échec du candidat.

## 7. Décision demandée au responsable

- **D1** — R1 (restauration du snapshot antérieur) si la plateforme le peut : audit G alors exécutable sur le
  SHA annoncé, aucune reconstruction.
- **D2** — sinon R2 : autoriser la reconstruction locale (fetch de la branche d'A, merge, ré-application du
  changement de F, ré-écriture des tests) **et** le re-mesurage complet sur le nouveau SHA ; l'audit G portera
  sur ce nouveau SHA, explicitement nommé.
- **D3** — dans les deux cas, décider si les deux corrections transverses du §3 du rapport F (verrou d'audit,
  contexte locataire) sont approuvées en principe, puisqu'elles sortent du périmètre documentaire strict ; elles
  conditionnent le fonctionnement du cycle de vie sur PostgreSQL, indépendamment de toute reconstruction.

Aucune action distante, aucune fusion, aucun déploiement, aucun secret manipulé. Arbre de travail laissé dans
l'état où le snapshot l'a remis, à l'exception du présent fichier.
