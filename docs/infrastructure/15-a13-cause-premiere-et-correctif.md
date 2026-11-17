# 15 — A-13 : cause première, correctif et preuves

**Date :** 17 novembre 2026
**Branche :** `arena/0a0b694c-vericlaim` (PR #14, **non fusionnée**)
**SHA de base :** `b289652a383bfe385c531ff4fb25a5f1535f74c7` (`main`)
**SHA des preuves finales :** voir §8 — le hash exact est enregistré dans
`.recette/last-run.json` et dans les runs CI cités.
**Verdict : RESOLVED pour les deux causes démontrées ; réserves en §9.**

Ce document remplace la conclusion d'ouverture de `14-diagnostic-a13.md`. Il ne
l'annule pas : le §7 de ce document (décision « ne pas fusionner ») reste
l'historique de la décision, et la présente mission en fournit la levée
technique.

---

## 1. Réponse directe aux deux questions posées

### 1.1 Pourquoi un import a-t-il répondu HTTP 201 alors que sa version était introuvable ?

Parce que **le code HTTP était envoyé avant la validation de la transaction**.

Sur la pile du projet (FastAPI 0.163.1 / Starlette 1.10.0), une dépendance
`yield` est démontée **après** que la réponse a été construite et renvoyée. Le
commit vivait dans `get_db` :

```python
def get_db():
    try:
        yield db
        db.commit()      # ← s'exécute APRÈS l'envoi de la réponse
    ...
```

L'ordre réel d'une requête était donc :

```
corps du routeur  →  réponse 201 construite  →  teardown de la dépendance (commit)
```

Conséquence directe : à l'instant où le client lisait `201`, **rien n'était
encore validé**. Si le commit échouait dans cette fenêtre, l'échec ne pouvait plus
changer le code HTTP — il était déjà parti. Le client gardait un succès pour une
ligne qui n'existait pas. C'est exactement la trace lue en CI :

```
RecetteStateError: version ac97355b-… does not exist at all
(no row even for the admin role): the upload was never committed
```

« Même pour le rôle admin » était le bon indice : ce n'était ni du RLS, ni un
contexte tenant, ni un problème de lecture. C'était une **écriture non validée
annoncée comme réussie**. Le rôle admin n'y changeait rien, puisqu'il n'y avait
rien à lire.

### 1.2 Pourquoi l'organisation active disparaissait-elle ?

Deux mécanismes, tous deux mesurés :

1. **La même cause première, sur d'autres tables.** « Créer une organisation »
   répondait `201` avant validation. La requête suivante répondait `409
   « Aucune organisation active »` — alors que la création venait de réussir.
   Ce n'était pas la session qui perdait son organisation : c'est le
   basculement qui n'était pas encore durable. Reproduit **de façon
   déterministe** en limitant la machine à un seul cœur (§4.2).
2. **Un défaut propre à `resolve_active_membership`.** Une lecture qui ne
   ramenait aucune ligne écrivait `active_organization_id = None` puis `flush`.
   Une lecture — donc une opération sans effet attendu — détruisait un état
   persistant. C'est le comportement observé dans l'annotation du run
   `43343496876` : `active_organization=None`.

---

## 2. Reproduction déterministe (sans attendre la loterie de la CI)

La CI échouait « environ une fois sur quatre ». Un défaut qu'on ne sait pas
provoquer ne peut pas être corrigé, seulement espéré. Trois reproductions
contrôlées ont été construites.

### 2.1 Ordre réponse/commit, mesuré sur la pile

Un middleware observe la réponse au moment exact où elle est prête :

```
corps du routeur → middleware voit la réponse → teardown (commit)
```

Un `RuntimeError` levé dans le commit pendant le teardown **ne change pas** le
code reçu par le client : `CLIENT SAW: 201`. L'écart est structurel, pas propre
au projet.

### 2.2 Contention : `taskset -c 0`

La fenêtre ne s'ouvre pas au hasard : elle s'ouvre sous contention. En limitant
l'exécution à **un seul cœur**, le parcours de recette échouait
**systématiquement** (3/3 puis 4/4 séries), avec des symptômes qui changeaient
selon l'étape atteinte :

| Symptôme observé | Ce qui avait pourtant répondu succès |
|---|---|
| `409 Aucune organisation active` | la création de l'organisation A |
| `404 Produit introuvable ou inaccessible` | la création du produit A |
| `401 Authentification requise` (connexion B) | la connexion SSO de B |
| `Document introuvable ou inaccessible` (import) | la création de la fiche documentaire |

Quatre étapes, quatre tables, **un seul mécanisme**. C'est ce qui a permis de
trancher entre « plusieurs bugs » et « un défaut d'ordre ».

### 2.3 Injection d'erreur contrôlée

Un test force l'échec du commit après l'écriture et vérifie ce que reçoit le
client. Avant correctif, le corps complet du `201` était renvoyé :

```json
{"upload":{"status":"clean","finalized_document_version_id":"806c34c0-…"},
 "version":{"id":"806c34c0-…","version_number":1,…}}
```

Un document « créé » annoncé avec son identifiant, pour une transaction que le
test venait d'annuler. C'est la démonstration du défaut, pas son interprétation.

---

## 3. Correctif

### 3.1 Le correctif utile

```python
Depends(get_db, scope="function")
```

sur **toutes** les routes (12 modules) et dans `identity/dependencies.py`. Avec ce
scope, le teardown de la dépendance s'exécute **avant** l'envoi de la réponse ;
l'ordre devient :

```
corps du routeur  →  commit  →  réponse envoyée
```

Mesuré : `['endpoint body', 'dependency teardown/commit', 'response sent']`.

Un échec de validation produit alors un `500` et **ne peut plus** accompagner un
`2xx`. C'est la garantie directement opposée à la trace CI.

### 3.2 Ce qui a été essayé puis retiré

Une première version ajoutait un `db.commit()` dans chaque service concerné
(upload, organisation, session, catalogue). Elle fonctionnait, mais c'était un
correctif **par point de défaillance** : il laissait la règle dépendre de la
discipline de chaque auteur. Le correctif de scope rétablit l'invariant pour
toutes les routes, présentes et futures ; les commits dispersés ont été retirés
pour ne pas dupliquer la règle.

### 3.3 `resolve_active_membership` : une lecture n'efface plus un état

Avant :

```python
if row is None:
    current.session.active_organization_id = None   # une lecture décide
    db.flush()
    return None
```

Après, les causes sont distinguées :

| Cause | Comportement |
|---|---|
| erreur de lecture (incident transitoire) | l'exception remonte, **rien n'est écrit** |
| membership absent | effacement **tracé** (`membership_absent`) |
| membership inactif | effacement **tracé** (`membership_inactive`) |
| organisation absente | effacement **tracé** (`organization_absent`) |
| organisation suspendue/archivée | effacement **tracé** (`organization_inactive`) |
| membership actif + organisation active mais jointure vide | `IdentityNotResolvableError`, **sélection conservée** |

Le dernier cas est important : la jointure peut être vide pour une raison qui
n'est pas une décision métier (rôle manquant, vue incomplète). Effacer la
sélection revenait à traiter un défaut de lecture comme un choix de
l'utilisateur.

### 3.4 Garde ajoutée sur le court-circuit idempotent

Le chemin « upload déjà finalisé » renvoyait `200` avec `version: null` si la
version désignée était introuvable. Un client lisait un succès sans objet. Il
répond désormais `409` et nomme l'incohérence. L'état n'est pas atteignable par
suppression normale (la clé étrangère est `ON DELETE SET NULL`) : le test
l'injecte explicitement, en le disant, plutôt que de prétendre couvrir un chemin
de production.

---

## 4. Tests de non-régression

Tous échouent **sans** le correctif et passent avec (vérifié dans les deux sens
pour chaque fichier).

### 4.1 `backend/tests/test_a13_durability_scope.py` (4 tests)

| Test | Ce qu'il oppose au défaut |
|---|---|
| `test_every_route_keeps_the_session_commit_before_the_response` | balaie **toutes** les routes, y compris dans les routers inclus, et exige `scope="function"`. Une route ajoutée demain sans scope échoue ici. |
| `test_201_is_only_sent_once_the_version_is_durable` | au moment exact où la réponse est prête, la version doit être visible depuis une **autre** connexion. |
| `test_failed_commit_never_returns_201` | une validation qui échoue produit une erreur, jamais un succès. |
| `test_next_request_sees_the_version_announced_by_the_201` | ce que le `201` annonce est ce que la requête suivante retrouve. |

Preuve que le garde-fou n'est pas décoratif : en retirant `scope="function"`
d'un seul module, il liste **7 routes** en échec et le test de durabilité tombe
avec lui.

### 4.2 `backend/tests/test_a13_active_organization.py` (5 tests)

Une lecture en erreur ou incohérente ne modifie plus la sélection ; les causes
durables l'effacent et laissent une trace exploitable.

---

## 5. Résultats mesurés

### 5.1 Parcours de recette (E2E réel : PostgreSQL 16, S3, worker, OIDC)

| Condition | Avant | Après |
|---|---|---|
| 2 cœurs, 1 série | 54/54 | **54/54** |
| 1 cœur (`taskset -c 0`) | **échec systématique** | **54/54 ×4 séries consécutives** |

La condition « 1 cœur » est celle qui reproduisait l'incident à volonté : c'est
elle qui fait foi, pas les séries confortables.

### 5.2 Suites

| Suite | Résultat |
|---|---|
| `backend/tests` complet (SQLite isolé) | **249 passed, 31 skipped** |
| PostgreSQL réel (RLS, isolation tenant, intégrité opérationnelle, purge concurrente) | **27 passed** |
| `ruff` sur `backend/app` | aucune règle **nouvelle** (comparé au décompte de base) |
| `npm run typecheck`, `npm run build` | verts |
| `npm audit --audit-level=moderate` | 0 vulnérabilité |

### 5.3 CI sur le SHA candidat

Run `44081515104` sur `a73e1d5` : **4/4 jobs verts** — Backend verification,
PostgreSQL tenant isolation (RLS), Recette qualification (real data plane),
Frontend verification.

Le run précédent (`44081214447`, `b4310ef`) était vert sur les trois jobs qui
testent ce correctif et rouge sur le seul audit npm : un avis modéré
(`GHSA-mfr8-8qww-v76h`) publié contre `next` 16.3.x–16.4.4, sans lien avec
A-13. Corrigé par `next` 16.4.6.

---

## 6. Ce que le diagnostic a appris sur le diagnostic

- **Le premier diagnostic était faussé par l'environnement.** Le pointeur de
  branche local était retombé sur `b289652` (`main`) alors que le travail
  distant était sur `d7817e5`. Pendant ce temps, une correction déjà livrée
  (`db.flush()` entre l'insert de la version et l'UPDATE de l'upload, A-01)
  semblait absente, ce qui produisait une fausse piste « violation de clé
  étrangère ». Réaligné par `git fetch` + `update-ref` + `reset --hard`, sans
  réécriture d'historique. Leçon : vérifier `git rev-parse HEAD` contre
  `origin/<branche>` **avant** d'interpréter un comportement de code.
- **Les traces CI lisibles étaient suffisantes.** Les logs de job restent
  inaccessibles (`EOF`), et `rerun`/`workflow_dispatch` restent à `403`. Mais
  les annotations de check-run, une fois les messages multi-lignes aplatis,
  contenaient la phrase décisive. Aucune trace non lue n'est citée ici.
- **Un symptôme qui change d'étape n'est pas forcément un autre bug.** Les 409,
  404 et 401 successifs n'étaient pas trois défauts : c'était le même, rencontré
  plus loin à mesure que les précédents étaient corrigés.

---

## 7. Réserves non résolues

1. **Aucun test de concurrence multi-requêtes en écriture n'a été ajouté** pour
   `resolve_active_membership`. Deux requêtes simultanées de la même session
   partagent une ligne `auth_sessions` ; le code ne verrouille pas cette ligne.
   Le défaut observé (effacement par une lecture) est corrigé, mais la course
   elle-même n'est pas mesurée.
2. **Le commit a lieu au teardown, pas dans la route.** La durabilité est
   garantie *avant la réponse* ; elle n'est pas garantie *avant la sérialisation*
   de la réponse. Un échec de sérialisation après commit laisserait une
   écriture durable sans réponse — l'inverse du défaut A-13, et sans perte de
   données.
3. **La CI a échoué « environ une fois sur quatre »** avant correctif. Quatre
   séries vertes sous contention et 4/4 jobs verts sur le SHA final ne
   constituent pas une preuve statistique d'absence de récidive : c'est une
   preuve de mécanisme. Si un échec réapparaît, les annotations porteront
   désormais l'étape et l'identifiant concernés.
4. **Vercel reste en échec** sur ce SHA : `Deployment rate limited — retry in 24
   hours` (quota du compte, extérieur au code). Aucun déploiement Production n'a
   été créé depuis le 2026-10-20.

## 8. Traçabilité

- Correctif applicatif : `b4310ef`
- Correctif d'audit frontend : `a73e1d5`
- Run CI de référence (`a73e1d5`) : `44081515104` — 4/4 verts
- Transcription E2E : `.recette/last-run.json` (`generated_at` du dernier run)
- Empreinte du diff antérieur à cette mission (référence de non-régression) :
  `d47ac1519deffa6d249b5fcf4b77d4e65a9b8defe9e8bb102f2e4b3f6c6b1711`
  (37 fichiers, +6127/−32)

## 9. Conclusion

**RESOLVED**, pour les deux causes démontrées :

- un succès HTTP ne précède plus la validation de la transaction — c'est prouvé
  par un test qui échoue sans correctif, par la reproduction sous contention, et
  par la CI verte sur le SHA final ;
- une lecture ne détruit plus l'organisation active, et les causes durables sont
  distinguées et tracées.

**La fusion reste une décision distincte** et n'a pas été faite : A-06 (aucun
worker sur Vercel), A-07 (aucun antivirus réel), A-08 (aucune sauvegarde) et A-09
(aucun monitoring) restent ouverts, et ils interdisent un pilote avec des données
réelles — indépendamment de A-13.
