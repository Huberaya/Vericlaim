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
| A-12 | Le job CI de recette ne pouvait pas s'installer (python 3.13 vs wheels `pgserver`) | P1 | **corrigé** |
| A-14 | Le harnais laissait un serveur Uvicorn orphelin après toute exception | P1 | **corrigé** |
| A-13 | Échec CI intermittent du parcours de recette (~1 run sur 3) | P1 | **ouvert — cause candidate corrigée, non confirmée** |

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

**Résultat.** **269 passed, 2 skipped** avec PostgreSQL, contre 255 avant — et les
2 restants sont explicitement justifiés (chemin `pytesseract` et un cas de
signature qui exige un binaire absent).

**Validation en intégration continue.** Run `43325846167` sur la PR #14 : les
4 jobs sont `success`, y compris le parcours complet et la restauration exercée
sur un runner propre. Les preuves de ce dossier ne sont donc plus seulement
locales.

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

---

## A-12 — Le job CI de recette ne pouvait pas s'installer

**Sévérité : P1 (la preuve n'était pas reproductible en intégration continue).**
**Statut : corrigé.**

**Observation.** Premier run de `recette-qualification` sur la PR #14 : **échec**
à l'étape « Install the application and the recette harness », donc *avant* tout
test. Les étapes suivantes ont été `skipped` et l'artefact n'a pas été produit.

**Accès aux logs.** L'API GitHub ne m'a **pas** rendu les logs de ce run
(`EOF` sur les trois endpoints de téléchargement). Je n'ai donc pas lu le message
d'erreur. La cause a été établie autrement, et de façon certaine, à partir de
l'index PyPI :

```
pgserver 0.1.4 → wheels cp39, cp310, cp311, cp312 (16 fichiers)
                 aucune source distribution
job CI        → python-version: "3.13"
```

Il n'existe **aucun** fichier installable pour cp313 : la résolution échoue, par
construction, quel que soit l'état du code applicatif. La même requête montre que
`tesserocr` publie bien du cp313 — le problème est propre à `pgserver`.

**Correction.**
1. `python-version: "3.12"` pour ce job — **vérifié cohérent avec
   `backend/Dockerfile` (`FROM python:3.12-slim`)** : le job teste donc exactement
   l'interpréteur du runtime déployé. Les deux autres jobs restent en 3.13, ce qui
   est sans risque : ils n'installent pas `pgserver` (vérifié — seule l'étape de
   recette l'ajoute) ;
2. une étape « Verify the harness dependencies really imported » qui importe
   `pgserver` et `moto.server` explicitement : un échec de résolution est
   rapporté avec un message clair au lieu de faire tomber toutes les étapes
   suivantes sur un `ImportError` indistinct.

**Preuve.** Run `43325846167` : les 4 jobs sont `success`, et pour le job de
recette les étapes 5 (« Verify the harness dependencies… »),
6 (« Run the end-to-end recette journey ») et 7 (« Prove a backup can actually
be restored ») sont **toutes `success`**.

**Ce que cela change dans ce dossier.** Jusqu'à ce run, « 54/54 » et la
restauration étaient des preuves **locales**. Elles sont maintenant exécutées
aussi en intégration continue, sur un runner propre. C'est exactement la raison
d'être de ce job — et il a immédiatement trouvé un défaut, dans le harnais
lui-même.

---

## A-13 — Échec CI intermittent du parcours de recette

**Sévérité : P1 (une CI qui échoue au hasard ne prouve rien).**
**Statut : OUVERT — instrumenté, pas encore expliqué.**

**Observation.** Sur six runs du job `recette-qualification` au même commit
applicatif, **deux ont échoué** et quatre ont réussi. Les deux échecs se
produisent à l'étape « Run the end-to-end recette journey ».

**Ce que les horodatages prouvent.** En comparant les étapes :

| Run | Étape 6 (parcours) | Résultat |
|---|---|---|
| `43327153607` | 23:15:19 → 23:15:38 (**19 s**) | success |
| `43326500112` | 23:10:47 → 23:11:04 (**17 s**) | success |
| `43326928526` | 23:13:37 → 23:13:44 (**7 s**) | **failure** |
| `43326090494` | 23:08:06 → 23:08:15 (**9 s**) | **failure** |

Un parcours complet dure 17–19 s. Les échecs s'arrêtent après 7–9 s : ce n'est
donc **pas** une expiration de délai, c'est une **exception levée tôt** — et le
harnais n'en publiait que « exit code 1 ».

**Ce qui est exclu par l'observation.**
- Ce n'est pas le code applicatif : les runs verts et rouges portent des SHA
  dont les seules différences sont documentaires, et trois exécutions locales
  consécutives donnent 54/54.
- Ce n'est pas la contention CPU : deux exécutions locales avec deux boucles
  occupant les deux cœurs donnent 54/54.
- Ce n'est pas le défaut A-12 (installation) : l'installation réussit dans tous
  les runs concernés, et l'étape 5 de contrôle d'import passe.
- Ce n'est pas un manque de moteur OCR : le parcours accepte explicitement
  l'échec contrôlé de l'étape 31.

**Ce que je ne peux pas faire.** Lire les logs : les URL signées des blobs
GitHub Actions sont refusées depuis mon environnement (`EOF`), et `gh run
rerun` / `workflow_dispatch` sont à 403. Je ne peux donc pas voir la trace
Python de l'échec.

**Ce qui a été fait à la place** — rendre le prochain échec explicite :
1. le parcours dépose des **marqueurs horodatés** à chaque étape et à chaque
   phase non vérifiée ;
2. une exception hors étape est nommée (type + message) **et** localisée par la
   dernière progression, en annotation ;
3. la console est capturée (`tee`) et sa fin réémise en annotations ;
4. les étapes en échec sont répétées en annotations.

Vérification de l'instrumentation par injection volontaire d'une exception :
`RuntimeError: connexion réinitialisée par le pair | dernière progression:
fixtures generated > healthz répond`.

**Conséquence sur nos affirmations.** Tant que A-13 n'est pas expliqué, je ne dis
pas « la CI est verte » : je dis « la CI a été verte sur les runs cités, et elle
échoue encore de façon intermittente sur le job de recette ». Les documents
`11` et `13` portent cette nuance.

**Analyse complémentaire (13 novembre) — corrélation temporelle.** Une exécution
locale instrumentée situe les phases du parcours :

| Phase | Fenêtre locale |
|---|---|
| démarrage de la pile, fixtures, serveur d'API | T+0 → T+5,2 s |
| imports (dont EICAR, 9 Mo refusés) | T+5,2 → T+5,5 s |
| **worker d'extraction (étape 29)** | **T+5,5 → T+7,0 s** |
| segments, **puis PDF scanné / OCR (étape 31)** | T+7,0 → **T+8,7 s** |
| téléchargements, analyse, rapports | T+8,7 → T+13 s |
| redémarrage et contrôle final | T+13 → T+16,8 s |

Les deux échecs CI s'arrêtent à **7 s** et **9 s**, c'est-à-dire **exactement**
sur ces deux phases-là, et sur aucune autre. La corrélation est donc forte :
l'échec se produit au moment où un processus worker est lancé puis arrêté, et à
l'étape qui dépend du moteur OCR.

Deux précisions honnêtes :
- le runner GitHub n'a probablement **pas** de binaire `tesseract` (aucune étape
  ne l'installe), donc l'étape 31 doit passer par la branche « échec contrôlé ».
  Le harnais **accepte explicitement** cet échec, donc il ne devrait pas lever ;
- une corrélation n'est pas une cause : rien ici n'explique encore *pourquoi* ces
  deux phases lèveraient.

**Prochaines étapes.** Le prochain échec produira une annotation contenant le
type d'exception et l'étape exacte (instrumentation vérifiée par injection), ce
qui tranchera entre : port pris entre réservation et écoute, worker sorti en
erreur, ou exception dans `_job_state` (accès base pendant la transition).

---

## A-14 — Le harnais laissait un serveur orphelin après toute exception

**Sévérité : P1 (état reporté d'un run à l'autre).**
**Statut : corrigé, avec preuve.**

**Observation.** Sur quatre exécutions locales, un processus `uvicorn` restait
vivant après chacune. Mesure après nettoyage : **zéro** orphelin pour deux
exécutions successives ; avant correctif, exactement **un par exécution**.

**Cause vérifiée.** `RecetteRun.execute` n'avait **aucun `try/finally`** autour du
serveur : `self.server.stop()` n'était appelé que sur le chemin heureux. Une
exception — dans une étape, dans un worker, dans un rendu de rapport — laissait
donc le serveur en vie, avec sa connexion PostgreSQL ouverte et son port occupé.
`ApiServer.stop()` ne fermait pas non plus le descripteur du journal.

**Pourquoi c'est grave.** C'est exactement la classe d'état qui fait échouer un
run **ultérieur** de façon apparemment aléatoire : port occupé par un processus
d'un run précédent, connexions résiduelles, files de jobs partagées.

**Corrections.**
1. `try/finally` dans `execute()` : serveur arrêté et navigateurs fermés quelle
   que soit la sortie ;
2. `stop()` ferme le descripteur du journal ;
3. `stop()` **attend la libération effective du port** et échoue sinon, au lieu de
   supposer que `terminate()` suffit ;
4. `running()` expose l'état du processus.

**Preuves.**
- `backend/tests/test_recette_harness_hygiene.py` (2 tests). Le plus important
  injecte une exception en milieu de parcours après avoir démarré un vrai serveur,
  puis vérifie que le processus est arrêté **et** que le port est re-libérable.
  **Vérifié en échec sans le `finally`** (assertion sur `run.server is None`), vert
  avec.
- 10 parcours consécutifs : **54/54** à chaque fois, **0 orphelin**.

**Lien avec A-13, énoncé sans le surestimer.** Ce défaut est un **mécanisme
plausible** de l'intermittence : un orphelin d'un run peut gêner le suivant. Il est
désormais impossible, et 10 runs consécutifs passent. Cela ne **prouve** pas que
A-13 était causée par là — les échecs CI observés étaient sur des runners neufs,
où aucun orphelin préalable n'existe. A-13 reste donc ouverte.

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
