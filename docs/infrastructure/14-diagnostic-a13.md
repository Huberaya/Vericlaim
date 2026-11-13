# 14 — A-13 : diagnostic de l'intermittence (13 novembre)

**Statut : NON RÉSOLUE. Mode de défaillance et deux défauts d'hygiène corrigés ;
cause première non élucidée.**
**Aucune décision de fusion ne peut reposer sur ce document seul.**

## 1. Ce qui a changé dans la méthode

Les runs précédents ne donnaient qu'un « exit code 1 ». Trois instruments ont été
ajoutés, dans cet ordre :

1. **annotations CI** échappant au piège du multi-ligne (une commande de workflow
   est ligne par ligne : un message contenant un saut de ligne était tronqué et
   l'annotation disparaissait silencieusement) ;
2. **marqueurs de progression** horodatés, pour situer une interruption entre
   deux étapes ;
3. **diagnostic des observations impossibles** (`RecetteStateError` remplaçant un
   `TypeError: 'NoneType' object is not subscriptable`), qui distingue « la
   version appartient à un autre tenant » de « la version n'existe pas ».

Ces trois ajouts ont transformé un échec opaque en cause lisible.

## 2. Ce qui a été trouvé

### 2.1 Une vraie fuite d'état dans le harnais (A-14, corrigée)

`execute()` n'avait aucun `try/finally` : un parcours qui levait laissait un
serveur `uvicorn` vivant. Mesuré à **un orphelin par exécution**. Corrigé, avec un
test de non-régression qui échoue sans le correctif.

### 2.2 Une vraie collision de stockage (A-15, corrigée)

Les buckets S3 étaient nommés en dur et le serveur Moto n'était jamais arrêté.
Conséquence observable : `BucketAlreadyOwnedByYou`, et l'impossibilité
d'enchaîner deux parcours dans le même processus. Corrigé (suffixe par exécution,
`S3Store.stop()` dans le `finally`). Après correctif : **6 parcours consécutifs
dans un seul processus, 0 échec** — ce qui était impossible avant.

### 2.3 Le mode de défaillance réel, enfin nommé

Sur les runs en échec, les annotations donnent :

| Run | Annotation | Lecture |
|---|---|---|
| `43341241416` | `TypeError: 'NoneType' object is not subscriptable` après « import documentaire indépendant pour B » | l'atelier d'observation a reçu `None` |
| `43342289898` | `B ne voit pas le document de A (404) — HTTP 409` | le produit répond 409, pas 404 |
| `43343496876` | `HTTP 409 — auth/status=200 me=200 **active_organization=None**` | B est authentifié mais **sans organisation active** |
| `43343801627` | `création produit fictif B — HTTP 404` | le fournisseur de B est introuvable |

Deux signatures, une seule histoire : **le tenant B perd son organisation active
en cours de parcours.** Le 409 est le message exact du produit pour cet état
(« Aucune organisation active »).

### 2.4 Le défaut de conception mis en évidence

`resolve_active_membership` (backend/app/identity/service.py) fait ceci quand la
ligne de membership n'est pas trouvée :

```python
if row is None:
    current.session.active_organization_id = None   # ← écriture destructive
    db.flush()
    return None                                     # → 409 côté HTTP
```

Une **lecture** décide donc de **détruire** l'état de session persisté. Toute
lecture transitoirement infructueuse — verrou, visibilité, cache d'identité,
contexte RLS non encore posé — fait basculer l'utilisateur dans un état
« aucune organisation », de façon durable, jusqu'à une nouvelle sélection
manuelle.

Pour un utilisateur réel, cela se traduit par : « je clique, l'application me dit
que je n'ai pas d'organisation, alors que j'en ai une ». C'est un défaut de
robustesse, indépendant de la recette.

**Ce qui n'est pas prouvé** : je n'ai pas établi *pourquoi* la lecture échoue
parfois. Les hypothèses restantes (course entre deux requêtes sur la même
session, effet de cache d'identité, ordre de transaction) ne sont pas départagées.

## 2.5 Un fait plus grave, établi au dernier run (`43344842829`)

L'annotation dit :

```
RecetteStateError: version ac97355b-… does not exist at all
(no row even for the admin role): the upload was never committed
```

et la progression la situe **juste après « segments consultables via l'API »**,
c'est-à-dire après un import qui a répondu **HTTP 201** à l'étape 23.

Autrement dit : **la complétion d'import a renvoyé un succès avec un identifiant
de version, puis aucune ligne n'existe en base — pas même pour le rôle admin, qui
contourne le RLS.**

Ce que cela exclut :
- une erreur de contexte tenant (l'admin ne voit pas davantage) ;
- une erreur d'identifiant côté harnais (le corps 201 provient du serveur) ;
- une lenteur de commit (la lecture a lieu plusieurs étapes plus tard).

Ce que cela implique : l'écriture de cette requête n'a pas été validée, alors que
le client a reçu un 201. La transaction est validée par `get_db`
(`yield db; db.commit()`), donc un 201 *devrait* impliquer un commit réussi. Le
fait que ce ne soit pas le cas est un **écart sérieux entre le succès annoncé et
l'état durable**.

**Je ne pousse pas plus loin l'instrumentation.** La suite exigerait de modifier
du code applicatif et de relancer des parcours jusqu'à reproduire l'incident, ce
qui dépasse l'autorisation de cette mission (aucune modification de production,
aucune migration) et le temps imparti. Le fait est consigné, l'anomalie reste
ouverte.

**Conséquence pour la décision de fusion.** Ce n'est plus seulement « la recette
est instable » : c'est « un succès d'import n'est pas toujours durable, au moins
dans cet environnement ». Si ce comportement existe en production, un utilisateur
verrait un document créé puis introuvable. Cela **doit** être tranché par le
propriétaire avant toute fusion, et je recommande de ne pas fusionner en l'état
tant que ce point n'est pas élucidé.

## 3. Ce que je n'ai pas pu faire

| Tentative | Résultat |
|---|---|
| Lire les logs de job GitHub | **impossible** : URLs signées en `EOF`, `rerun`/`dispatch` en **403** |
| Reproduire le 409 en local (2 cœurs, 10 + 6 + 6 parcours) | **0 occurrence** |
| Reproduire sous contrainte 1 cœur | 3/3 échecs observés à un moment, **puis 0/7** après les correctifs d'hygiène, sans lien prouvé |
| Instrumenter le serveur pour capturer l'erreur SQL | nécessite de modifier du code applicatif ; **tenté puis annulé**, aucune sortie capturée, arbre vérifié intact |

L'instrumentation applicative temporaire a été **entièrement révoquée** :
`git diff backend/app/catalog/service.py` est vide.

## 4. Série de mesures après correctifs (même arbre)

| Série | Résultat |
|---|---|
| 10 parcours consécutifs | **10 × 54/54** |
| 6 parcours dans un seul processus | **6 × 54/54** |
| 6 parcours supplémentaires | **6 × 54/54** |
| CI après correctifs | voir §5 |

## 5. État CI au moment de ce document

Sur les 12 derniers runs de la branche : **8 succès, 4 échecs**, dont deux échecs
postérieurs aux correctifs d'hygiène (`11a92e10`, `d27d8737`). Autrement dit, les
correctifs n'ont pas supprimé l'intermittence.

**Conclusion honnête : A-13 n'est pas résolue.** Ce document établit le mode de
défaillance (perte de l'organisation active, révélée par un 409 au lieu d'un 404),
identifie un défaut de conception réel dans `resolve_active_membership`, et
documente deux défauts d'hygiène corrigés — mais **ne prouve pas** la cause
première de l'intermittence, et **ne peut pas** être présenté comme une résolution.

Une décision explicite du propriétaire est requise : soit accepter A-13 comme
dette technique non bloquante pour la fusion de cette PR (la recette E2E n'est pas
un composant de production), soit exiger sa résolution préalable — auquel cas il
faut un accès aux logs CI, que je n'ai pas.

## 6. État final de la CI sur le SHA candidat `c8d8756`

| Contrôle | Résultat |
|---|---|
| Backend verification | **success** |
| PostgreSQL tenant isolation (RLS) | **success** |
| Frontend verification | **success** |
| Recette qualification (real data plane) | **success** |
| Vercel | **failure — « Deployment rate limited — retry in 24 hours »** |

Le job de recette est donc passé sur ce SHA. Cela **ne résout pas A-13** : le
même arbre a échoué sur les deux runs précédents, et la fréquence observée reste
d'environ **un échec sur trois**.

Le contrôle Vercel est bloqué pour une raison externe : **quota de déploiements
du compte** (offre gratuite), pas un défaut du code. Aucun contournement n'est
tenté.

### Ce que cela implique pour la fusion

1. **A-13 n'est pas résolue** → condition de fusion non satisfaite en l'état, sauf
   acceptation explicite de dette par le propriétaire.
2. **Le check Vercel est en échec sur le SHA candidat** → condition 4 (« tous les
   contrôles obligatoires sont verts ») non satisfaite, pour une raison externe.
3. Les quatre jobs GitHub sont verts **sur ce SHA précis**.

## 7. Décision du propriétaire du projet (13 novembre 2026)

> **Décision : NE PAS FUSIONNER tant que A-13 n'est pas élucidée.**

Motif retenu : un import qui répond **HTTP 201 sans écriture durable** touche
directement l'utilisateur (document créé puis introuvable). L'instabilité de la
recette n'est donc pas une simple dette de test.

**Conséquence immédiate** : la PR #14 reste en **brouillon**, et le verdict de ce
chantier est **BLOCKED** — indépendamment des quatre jobs GitHub verts.

### 7.1 Comment lever le blocage

Trois voies, par ordre de coût croissant.

| Voie | Ce qu'il faut | Ce que cela permettrait |
|---|---|---|
| **V1 — logs CI** | accès aux journaux du job `recette-qualification` (téléchargement des artefacts ou export du log) | lire la trace Python exacte de l'échec, donc l'instruction SQL et le chemin de code. **C'est la voie la moins coûteuse** : aucun code à modifier. |
| **V2 — autorisation d'instrumenter** | accord explicite pour modifier temporairement `app/core/database.py` (`get_db`) et `app/documents/service.py` (`complete_document_upload`), puis révoquer | capturer l'exception réelle ou la preuve qu'aucune instruction n'est émise pour cette écriture, en reproduisant sous charge |
| **V3 — reproduction forcée** | accord pour ajouter un mode de test injectant une contention/concurrence sur la session | rendre l'intermittence déterministe et la corriger par un test de non-régression |

**Ce qui est bloquant pour V1** : `gh run view --log`, `gh run download` et
l'API interne des blobs renvoient `EOF` depuis cet environnement ; `gh run rerun`
et `workflow_dispatch` renvoient **403**. Sans ces accès, l'annotation (déjà en
place) reste la seule source, et elle ne contient pas de trace Python.

### 7.2 Ce qui est déjà acquis, et qui ne sera pas perdu

- mode de défaillance nommé (perte de l'organisation active / 409 au lieu de 404) ;
- un défaut de conception réel documenté : `resolve_active_membership` **écrit**
  (`active_organization_id = None` + `flush`) à la suite d'une **lecture**
  infructueuse ;
- deux défauts d'hygiène du harnais corrigés et couverts par un test
  (A-14, A-15) ;
- instrumentation CI qui rendra le prochain échec lisible (annotations,
  marqueurs, diagnostic d'observation impossible).

### 7.3 Ce qu'il ne faut pas conclure

Que la recette est « verte parce que le dernier run est vert ». Sur le même
arbre : 2 échecs sur 4 runs CI, puis 5 runs verts, puis 1 échec, puis 2 verts.
La fréquence reste de l'ordre d'**un échec sur trois**.
