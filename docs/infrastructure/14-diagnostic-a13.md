# 14 — A-13 : diagnostic de l'intermittence (13 novembre)

**Statut : cause partiellement identifiée — un défaut de conception du produit
est mis en évidence, l'intermittence n'est pas close.**
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
