# Chantier 6.1 — Catalogue fournisseurs et produits tenant-scoped

## Objet et frontière de produit

Le premier incrément du Chantier 6 active le catalogue achats déjà présent dans le modèle fondation : fournisseurs et produits tenant-scoped deviennent des ressources API et UI persistantes.

Le catalogue sert uniquement à **contextualiser** une pièce documentaire ou un dossier de pré-audit. Il ne calcule aucun score fournisseur, ne vérifie aucune identité, ne réalise aucun enrichissement externe et ne produit aucune conclusion réglementaire ou juridique.

## Inclus

- CRUD API de `Supplier` et `Product` ;
- RLS, RBAC, CSRF et journal d’audit métier ;
- création idempotente tenant-scoped avec `Idempotency-Key` ;
- fournisseur obligatoire, actif et du même tenant lors de la création d’un produit ;
- rattachement contrôlé de fournisseurs/produits aux documents C3 et aux analyses C5 existantes ;
- interface minimale de consultation, création, édition et archivage ;
- migration `d3c8a6e1b409_tenant_catalog_idempotency`.

## Hors périmètre explicite

- registre de preuves persistant, certificats déclarés et liens claim → preuve (C6.2) ;
- validation humaine de claims, suggestions de preuves ou demandes fournisseur (C6.3) ;
- score, risque, recommandation, évaluation réglementaire persistante ou verdict juridique ;
- vérification en ligne de fournisseurs/certificats, PIM/ERP, e-mail ou enrichissement externe ;
- LLM, RAG, PDF et modification des résultats hashés C5.

## Ressources et invariants

### Fournisseur

Un `Supplier` contient la raison sociale, le nom commercial optionnel, une référence interne optionnelle, le pays, un e-mail de contact et des métadonnées JSON bornées.

- `external_reference`, lorsqu’elle est fournie, est unique par organisation ;
- les métadonnées sont JSON strict, limitées à 16 Ko, profondeur 5 et 50 éléments par objet/liste ;
- le client ne fournit jamais `organization_id` ; il provient de la session SSO et de l’organisation active ;
- l’archivage est un soft delete : les documents et analyses historiques gardent leur clé étrangère.

### Produit

Un `Product` appartient obligatoirement à un fournisseur actif du même tenant.

- `reference` est unique par organisation ;
- le pays est un code ISO à deux lettres ;
- le cycle de vie est limité à `active`, `inactive` ou `discontinued` ;
- le rattachement produit → fournisseur est immuable via l’API : déplacer un produit réécrirait le contexte des documents et analyses historiques ; il faut archiver/créer une ressource traçable à la place.

Un fournisseur ne peut être archivé tant qu’il contient des produits non archivés. Un produit peut être archivé sans modifier les FKs historiques de `Document`, `Analysis`, puis ultérieurement de `Evidence`.

## Idempotence de création

Les routes de création exigent l’en-tête ASCII imprimable `Idempotency-Key` (1–128 caractères sans espace).

La migration ajoute à `suppliers` et `products` :

- `idempotency_key` ;
- `request_sha256` ;
- unicité `(organization_id, idempotency_key)` ;
- contrôle de longueur SHA-256.

L’empreinte canonique lie l’opération et les valeurs normalisées de la demande. Pour une même organisation et ressource :

- même clé + même empreinte → ressource initiale, `200`, `idempotent_replay=true` ;
- même clé + empreinte différente → `409` ;
- course concurrente → contrainte SQL puis relecture sûre de la ressource créée.

`PATCH` applique un état explicite et `DELETE` est un archive idempotent : un second `DELETE` ne modifie plus l’historique ni n’ajoute d’événement.

## API livrée

Toutes les lectures exigent `catalog:read`. Toutes les mutations exigent `catalog:manage`, session SSO, organisation active et CSRF.

| Route | Intention |
|---|---|
| `POST /api/v1/suppliers` | Crée un fournisseur (`201`) ou rejoue la création (`200`). `Idempotency-Key` obligatoire. |
| `GET /api/v1/suppliers` | Liste active cursor-based, recherche `q`, limite 1–100. |
| `GET /api/v1/suppliers/{id}` | Lit un fournisseur actif du tenant. |
| `PATCH /api/v1/suppliers/{id}` | Met à jour les champs autorisés et journalise les noms de champs modifiés. |
| `DELETE /api/v1/suppliers/{id}` | Archive sans supprimer l’historique ; refuse si un produit actif existe. |
| `POST /api/v1/products` | Crée un produit sous fournisseur actif (`201`) ou rejoue (`200`). `Idempotency-Key` obligatoire. |
| `GET /api/v1/products` | Liste active cursor-based, filtre optionnel `supplier_id`, recherche `q`. |
| `GET /api/v1/products/{id}` | Lit un produit actif du tenant. |
| `PATCH /api/v1/products/{id}` | Met à jour les champs autorisés, jamais `supplier_id`. |
| `DELETE /api/v1/products/{id}` | Archive sans rompre les références historiques. |

Les erreurs d’accès à une ressource étrangère utilisent le même `404` que les ressources absentes. Les collisions de référence ou de clé idempotente retournent `409`.

## RBAC, RLS et audit

Les permissions système ajoutées sont :

| Rôle | `catalog:read` | `catalog:manage` |
|---|:---:|:---:|
| owner | oui | oui |
| admin | oui | oui |
| analyst | oui | oui |
| viewer | oui | non |

`suppliers` et `products` étaient déjà dans les tables RLS tenant-owned de la migration C2. Chaque service filtre aussi explicitement `organization_id`, afin que les tests SQLite conservent l’isolation applicative.

Chaque première mutation utile ajoute un événement append-only hash-chaîné :

- `catalog.supplier_created`, `catalog.supplier_updated`, `catalog.supplier_archived` ;
- `catalog.product_created`, `catalog.product_updated`, `catalog.product_archived`.

Les payloads d’audit conservent des IDs, types, clés de champs et hashes nécessaires au diagnostic, sans recopier e-mail ou métadonnées complètes.

## Intégration documentaire et analyse

Les APIs C3 et C5 possédaient déjà les contrôles de cohérence tenant/fournisseur/produit. C6.1 fournit enfin les ressources à sélectionner :

1. le coffre documentaire peut associer facultativement une nouvelle pièce à un fournisseur puis à un produit de ce fournisseur ;
2. `POST /documents` vérifie que les IDs sont accessibles et que le produit dépend du fournisseur choisi ;
3. au lancement depuis le coffre, l’analyse C5 reçoit ce même contexte ;
4. C5 vérifie encore que chaque document d’entrée n’est pas en contradiction avec le fournisseur/produit sélectionné.

Aucune association rétroactive n’est automatique. Une ressource archivée ne peut plus être choisie pour une nouvelle pièce ou analyse, mais ses références historiques restent consultables dans le tenant.

## Vérifications automatisées

`tests/test_catalog.py` couvre notamment :

- création, normalisation, replay idempotent et collision de clé ;
- unicité de référence, RBAC, CSRF et isolation inter-tenant ;
- pagination cursor-based ;
- édition auditée, interdiction de réaffecter un produit et soft archive ;
- refus d’archiver un fournisseur qui a des produits actifs ;
- rattachement document fournisseur/produit valide et rejet d’un couple incohérent.

Les validations C3/C5 existantes continuent de vérifier les références catalogue lors de la création de documents et analyses.
