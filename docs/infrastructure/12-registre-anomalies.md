# 12 — Registre des anomalies

**Date :** 12 novembre 2026
**Base :** `b289652a383bfe385c531ff4fb25a5f1535f74c7` (branche `main`)
**SHA de correction :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`
**Périmètre :** recette technique uniquement, aucune donnée client

Chaque anomalie indique : comment elle a été **observée**, sa cause vérifiée, ce
qui a été corrigé, et ce qui **reste ouvert**. Aucune anomalie n'est close sur la
base d'un raisonnement : elle est close sur une observation.

## Synthèse

| ID | Titre | Sévérité | État |
|---|---|---|---|
| A-01 | Import documentaire impossible sur PostgreSQL (HTTP 500) | **P0** | **corrigé** |
| A-02 | Rapport PDF d'une analyse persistée en erreur 500 | P1 | **corrigé** |
| A-03 | Dossier probatoire ZIP en erreur 500 | P1 | **corrigé** |
| A-04 | La suite de tests ne voyait pas les défauts ci-dessus | P1 | **corrigé** |
| A-05 | Preview sans `DATABASE_URL` servie par un SQLite jetable | P1 | **corrigé** |
| A-06 | Aucun worker/cron sur Vercel : les files ne sont jamais drainées | **P1** | **ouvert** |
| A-07 | Aucun antivirus réel n'a pu être exécuté | **P0 pour un pilote** | **ouvert** (environnement) |
| A-08 | Aucune sauvegarde automatisée, stockage objet non couvert | **P0 pour un pilote** | **ouvert** |
| A-09 | Aucun monitoring proactif (métriques, alertes) | P1 | **ouvert** |
| A-10 | `vericlaim` : TLS PostgreSQL non vérifiable, `verify_runtime.py` jamais exécuté avec succès | P1 | **ouvert** |
| A-11 | Import d'un PDF volumineux refusé par la limite par défaut (2 Mo) | P2 | **atténué** |

---

## A-01 — Import documentaire impossible sur PostgreSQL (HTTP 500)

**Sévérité : P0 (bloquant : le parcours principal du produit).**
**Statut : corrigé.**

**Observation.** Sur la pile réelle (PostgreSQL 16 + S3 + clamd contractuel), tout
import de PDF valide échouait à la complétion avec **HTTP 500**. Le parcours
n'avait jamais été exercé sur PostgreSQL : la suite tournait sur SQLite, où le
défaut est invisible.

**Cause vérifiée.** `complete_document_upload` renseignait
`document_uploads.finalized_document_version_id` **avant** l'`INSERT` de
`document_versions`. Aucune relation ORM ne déclare cette dépendance, donc
SQLAlchemy ordonnait l'`UPDATE` d'abord. PostgreSQL refuse (violation de clé
étrangère) ; SQLite, lui, n'applique pas les clés étrangères par défaut.

**Correction.** `flush()` explicite entre la création de la version et
l'affectation de la référence (`backend/app/documents/service.py`).

**Preuve.** Étape 23 du parcours E2E : **HTTP 201**, avec `sha256` et
`size_bytes` persistés. Test de non-régression :
`test_recette_defect_regressions.py`.

**Leçon retenue et appliquée.** `PRAGMA foreign_keys=ON` est désormais posé dans
`conftest.py` : une suite qui tourne sur une base permissive doit être rendue
aussi stricte que la production.

---

## A-02 — Rapport PDF d'une analyse persistée en erreur 500

**Sévérité : P1 (livrable inutilisable).**
**Statut : corrigé.**

**Observation.** `GET /api/v1/analyses/{id}/report.pdf` renvoyait **500** dès que
l'analyse était une version **persistée** ; les analyses calculées à la volée
fonctionnaient, ce qui masquait le défaut en démonstration.

**Cause vérifiée.** `reports.py` lisait `AnalysisVersion.input_manifest`,
attribut inexistant (`AttributeError`). Le manifeste vit dans
`result_json['input_manifest']`, et son intégrité est garantie par
`input_manifest_sha256`.

**Correction.** Helper unique `_persisted_source_text(version)` qui lit le
manifeste persisté et vérifie son empreinte.

**Preuve.** Étape 38 : **HTTP 200**, 7 943 octets.

---

## A-03 — Dossier probatoire ZIP en erreur 500

**Sévérité : P1.**
**Statut : corrigé.**

Même cause qu'A-02, chemin distinct (`/dossier.zip`). Corrigé par le même helper.
Preuve : étape 39, **HTTP 200**, 6 717 octets.

Ces deux anomalies avaient la même racine : **un livrable que personne n'avait
ouvert depuis la dernière refonte du modèle**. Elles ne pouvaient pas être vues en
lecture de code seule, ni par les tests existants — d'où A-04.

---

## A-04 — La suite de tests ne voyait pas ces défauts

**Sévérité : P1 (c'est le défaut de méthode).**
**Statut : corrigé.**

**Observation.** 255 tests verts, et néanmoins un 500 sur le parcours principal.
Trois causes cumulées :

1. **SQLite partout** : clés étrangères non appliquées → A-01 invisible ;
2. **base non PostgreSQL** : 27 tests RLS étaient `skipped` silencieusement ;
3. **aucun test de bout en bout** sur les livrables réels.

**Correction.**
- `PRAGMA foreign_keys=ON` sur SQLite ;
- `VERICLAIM_TEST_POSTGRES_URL` permet d'exécuter réellement les 27 tests RLS
  (PostgreSQL 16 obtenu localement pour ce chantier) ;
- 8 tests de non-régression (`test_recette_defect_regressions.py`), dont 3 sur le
  refus d'une base SQLite sur plateforme managée ;
- matrice d'authentification (12 tests) contre un **vrai** fournisseur OIDC HTTP ;
- 9 tests OCR contre un **vrai** moteur Tesseract ;
- parcours E2E de 54 étapes, exécuté en CI (`recette-qualification`).

**Résultat.** **267 passed, 2 skipped** avec PostgreSQL, contre 255 avant — et les
2 restants sont explicitement justifiés (chemin `pytesseract` et un cas de
signature qui exige un binaire absent).

---

## A-05 — Preview sans `DATABASE_URL` servie par un SQLite jetable

**Sévérité : P1 (faux sentiment de sécurité ; en pratique, démarrage en erreur).**
**Statut : corrigé.**

**Observation.** Reproduit en lançant réellement l'application avec `VERCEL=1` et
sans `DATABASE_URL` :

```
sqlite3.OperationalError: unable to open database file
```

`get_settings()` retombait sur `sqlite:///./vericlaim.db` dès que `APP_ENV` valait
`development`. Sur Vercel, cela signifie au mieux une base éphémère **sans RLS**,
au pire un plantage de démarrage — et dans les deux cas un déploiement qui ne dit
pas qu'il est mal configuré.

**Cause vérifiée.** Le repli SQLite ne distinguait pas le poste de développement
d'une plateforme managée.

**Correction.** `_running_on_managed_platform()` (`VERCEL`, `K_SERVICE`, `DYNO`) :
refus explicite d'une `DATABASE_URL` absente **et** d'une `DATABASE_URL` SQLite
sur ces plateformes. Développement local inchangé.

**Preuve.** 4 cas vérifiés : `VERCEL=1` sans URL → refus ; `VERCEL=1` + URL SQLite
→ refus ; local sans URL → SQLite accepté ; local + URL PostgreSQL → accepté.
3 tests de non-régression ajoutés.

---

## A-06 — Aucun worker ni cron sur Vercel : les files ne sont jamais drainées

**Sévérité : P1 (bloque le parcours métier sur la Preview).**
**Statut : OUVERT — décision d'architecture requise, hors de ma portée.**

**Observation.** Prouvé par contraste sur la pile réelle :

| Condition | Résultat observé |
|---|---|
| workers lancés | `job=completed`, `segments=1`, `claims=3` |
| workers **non** lancés | `pending` / `queued` après attente, **aucune** transition |

Et lecture de `vercel.json` : **aucune clé `crons`**. Les workers n'exposent que
`run_forever()`, adapté à un processus long (Compose, conteneur), pas à un
déploiement serverless.

**Conséquence.** Sur la Preview, un import reste `pending`, une analyse reste
`queued`. Le parcours s'arrête après l'upload. Ce n'est pas un bug : c'est
l'architecture cible du produit (traitement long, OCR) confrontée à un hébergement
sans processus long.

**Ce qui est déjà bon.** L'absence de worker **ne fabrique aucun succès** : la file
ne bouge pas, rien n'est marqué lu (étapes 53–54). Le comportement est honnête ;
c'est la fonctionnalité qui manque.

**Trois options à trancher (aucune décidée ici) :**
1. exécution planifiée (`crons` Vercel ou planificateur externe) appelant une route
   **idempotente** qui exécute `run_once()` un nombre borné de fois — le moins
   intrusif, mais ajoute une surface HTTP à protéger ;
2. service worker **hors Vercel** (conteneur identique à Compose) ;
3. file managée + runtime adapté.

**Recommandation.** Option 2 pour un pilote (elle ne change pas le produit, elle
change l'hébergement des workers) ; option 1 si l'on veut rester sur Vercel.

---

## A-07 — Aucun antivirus réel n'a pu être exécuté

**Sévérité : **P0 pour un pilote ingérant des pièces tierces**.**
**Statut : OUVERT — limite d'environnement, non contournable ici.**

**Observation.** `apt` indisponible, root absent, aucun dépôt joignable :
`libclamav`, `clamd`, `freshclam` et la base de signatures ne sont pas
installables. Aucune détection réelle n'a donc été exercée.

**Ce qui est prouvé malgré tout** (voir `06`) : le client clamd INSTREAM respecte
le protocole, l'EICAR est refusé (422, `upload.status=rejected`), aucune version
n'est promue, et toute erreur ou indétermination clamd est traitée comme une
erreur — **jamais** comme un « propre ».

**Aggravant.** `scan_signature_version` n'est jamais renseigné, donc même avec un
vrai moteur, la preuve « analysé avec la base X » ne serait pas auditable.

**À faire avant un pilote.** Déployer un vrai `clamd` (ou un service managé),
vérifier `/readyz` → `malware_scanner: configured`, exécuter l'EICAR **sur la
Preview**, renseigner la version de signatures, documenter la politique de
quarantaine. **Ne pas communiquer « antivirus opérationnel » avant cela.**

---

## A-08 — Aucune sauvegarde automatisée ; stockage objet non couvert

**Sévérité : **P0 pour un pilote avec données réelles**.**
**Statut : OUVERT (mais une restauration a été exercée).**

**Observation.** Aucun script, aucune tâche, aucun workflow de sauvegarde dans le
dépôt ; aucune politique de rétention, de chiffrement, de RPO/RTO ; aucune
procédure de restauration. Les objets de stockage ne sont couverts par rien.

**Ce qui a été fait.** Un aller-retour complet réel a **été exécuté** sur une cible
isolée (`tools/recette/restore.py`) : export `pg_dump`, restauration dans une
**autre** base, réattribution de propriété, grants revus, puis vérification de la
révision Alembic, des données, du RLS et **de la chaîne d'audit recalculée**.

```
revision_preserved: true | all_organizations_present: true
restored_audit_chain_valid: true | rls_protected_tables: 26
rows_visible_without_tenant_context: 0 | restore_seconds: 0.53
```

**Deux obstacles réels découverts**, qui doivent entrer dans le runbook : (a)
`pg_restore --no-owner` recrée les objets au nom de l'utilisateur courant, donc la
propriété doit être réattribuée **avant** les grants ; (b) les **rôles** ne sont
pas dans le dump — ils doivent être recréés.

**Reste à faire.** Sauvegarde Neon (PITR, rétention, chiffrement, responsable) **et**
sauvegarde des objets, avec réconciliation version ↔ objet.

---

## A-09 — Aucun monitoring proactif

**Sévérité : P1 pour un pilote.**
**Statut : OUVERT.**

**Observation.** `/readyz` et le journal d'audit sont solides, les logs sont utiles
et sans secret — mais **aucune métrique et aucune alerte** n'existent. Un worker
arrêté (A-06) ou une file qui gonfle ne se voit que si quelqu'un regarde.

**À faire.** Définir a minima : disponibilité (`/readyz`), taux de 5xx, base
injoignable, worker inactif avec file non vide, hausse des échecs de scan et d'OCR,
profondeur de file. Aucune de ces alertes n'existe aujourd'hui.

---

## A-10 — TLS PostgreSQL et `verify_runtime.py` non validés

**Sévérité : P1 (preuve manquante, pas défaut produit connu).**
**Statut : OUVERT — impossible ici.**

**Observation.** `infra/neon/verify_runtime.py` exige un TLS actif. Le PostgreSQL
utilisé pour ce chantier est compilé **sans SSL** ; toutes les tentatives se sont
arrêtées sur :

```
RuntimeError: Refusing database operation: PostgreSQL TLS is not active.
```

C'est une **preuve négative utile** (le garde refuse bien une connexion non
chiffrée), mais ce n'est pas la validation attendue : la commande n'a **jamais**
réussi. Neon et `api.neon.tech` sont hors de portée réseau.

**À faire.** Exécuter, par l'assistant :
`python infra/neon/verify_runtime.py --expected-revision a7d4c9e2b510` sur la base
staging, et fournir la sortie. Le script doit être considéré comme **non validé**
tant que ce n'est pas fait.

---

## A-11 — Import d'un PDF volumineux refusé par la limite par défaut

**Sévérité : P2 (paramétrage, pas défaut).**
**Statut : atténué (limite rendue explicite).**

**Observation.** Un PDF scanné de bonne qualité dépassait `MAX_UPLOAD_BYTES`
(2 Mo par défaut) et l'import échouait en **422** — comportement correct, mais
surprenant : un utilisateur qui photographie un document reçoit « fichier trop
volumineux » sans indication de la limite.

**Traitement.** Le contrat `GET /api/v1/auth/status` publie désormais
`document_upload_max_bytes`, afin que l'interface affiche la limite **avant** le
dépôt. Le harnais relève la limite à 8 Mo pour ses fixtures.

**Reste à faire.** Fixer la limite de recette en fonction des usages réels
(téléphone, 300 dpi) et l'afficher dans l'interface.

---

## Ce qui n'a pas été testé du tout

Pour éviter toute ambiguïté :

- parcours navigateur réel sur la Preview (SSO + réseau) ;
- vrai fournisseur OIDC externe ;
- vrai antivirus ;
- vrai Neon, vraies sauvegardes Neon, vrai Vercel à l'exécution ;
- charge, performance, latence, montée en charge ;
- comportement multi-workers concurrents sur la même file ;
- restauration d'une base de production.

Ces éléments sont repris comme limites dans `13-verdict-recette-exploitable.md`.
