# 05 — Stockage documentaire

**Date :** 12 novembre 2026
**SHA testé :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`
**Environnement :** application réelle + **serveur S3 local** appelé par
l'adaptateur `S3ObjectStorage` de l'application (boto3, SigV4, URLs présignées).

## 1. Mécanisme réellement implémenté

Ni stockage local, ni stockage temporaire, ni simulation : le produit repose sur
un **stockage objet S3-compatible** derrière `app/documents/storage.py`, avec deux
buckets séparés.

```
1. POST /documents/{id}/versions   → intention d'upload, URL présignée de QUARANTAINE
2. PUT  <url présignée>            → le navigateur dépose les octets, sans passer par l'API
3. POST /document-uploads/{id}/complete
       a. HEAD objet, contrôle taille / type
       b. inspection non-parser (signature réelle vs extension vs MIME)
       c. SCAN antivirus          ← aucun parseur n'a encore vu les octets
       d. copie quarantaine → bucket propre, clé construite par le serveur
       e. version durable + audit + job d'extraction, dans UNE transaction
       f. libération de l'objet de quarantaine (registre de purge)
```

Points structurants vérifiés dans le code :

- **le client ne choisit jamais la clé** : `clean/{org}/{doc}/versions/{uuid}/source` ;
- préfixes de purge validés par expression régulière stricte (`validate_tenant_scoped_prefix`) ;
- **aucun parseur** (PDF, image, OCR) n'est invoqué avant le verdict antivirus ;
- SSE supporté (`none` / `aes256` / `aws:kms`) ; `backend/Dockerfile` et Compose
  désactivent SSE en local, la recette doit l'activer ;
- sécurité de nom de fichier : refus des `/`, `\`, `"`, caractères de contrôle,
  `.`/`..` ;
- cohérence **triple** extension / MIME déclaré / signature réelle.

## 2. Tests exécutés

Tous les tests ci-dessous sont exécutés par le harnais (`tools/recette`) contre un
serveur S3 réel et l'adaptateur boto3 non modifié.

| # | Test | Attendu | Observé |
|---|---|---|---|
| 1 | import d'un PDF valide | 201, version créée | **201**, `sha256` enregistré, `size_bytes: 1011` |
| 2 | format interdit (`.exe`) | refus | **422** |
| 3 | MIME déclaré incohérent (`text/plain` sur un PDF) | refus | **422** |
| 4 | extension usurpée (exécutable renommé `.pdf`) | refus sur signature | **422** |
| 5 | fichier au-delà de `MAX_UPLOAD_BYTES` | refus | **422** |
| 6 | nom de fichier contenant un chemin | refus | couvert par test unitaire (`test_secure_documents.py`) |
| 7 | URL de téléchargement | 200 + URL signée | **200**, `X-Amz-Signature` présent |
| 8 | téléchargement de l'URL signée | octets identiques à la source | **200, 1011 octets**, contenu identique |
| 9 | accès à l'objet **sans** signature | refus | **403** → les buckets ne sont pas publics |
| 10 | accès inter-tenant à une version | refus | **404** (B ne peut pas obtenir d'URL pour le document de A) |
| 11 | accès inter-tenant au document | refus | **404** |
| 12 | persistance après redémarrage du processus | document toujours lisible | **200** |
| 13 | fichier EICAR | refusé, jamais promu | **422**, upload `rejected` |
| 14 | métadonnées | `sha256`, `size_bytes`, `content_type`, `scan_engine` persistés | **présents** |

### Preuves textuelles (extraits du compte rendu machine)

```
import PDF réel         → complétion HTTP 201, sha256=917fd7a4…, size=1011, scan_engine=clamav
URL signée              → HTTP 200, X-Amz-Signature présent
téléchargement          → HTTP 200, 1011 octets, contenu binaire identique
objet non signé         → HTTP 403
EICAR                   → HTTP 422, upload.status=rejected, aucune version créée
```

## 3. Ce qui est réel dans cette preuve, et ce qui ne l'est pas

| Élément | Réel ? | Conséquence |
|---|---|---|
| code d'adaptation (`S3ObjectStorage`) | **oui, non modifié** | signatures SigV4, presign, copy, delete vérifiés |
| protocole S3 (requêtes, en-têtes, erreurs) | **oui** | un vrai client boto3 parle à un vrai serveur S3 |
| moteur de stockage | **serveur S3 local**, pas MinIO | le comportement MinIO spécifique (ILM, console, quotas) n'est pas vérifié |
| chiffrement SSE/KMS | **non testé** | nécessite un KMS ; en recette, `DOCUMENT_STORAGE_SSE_MODE` doit être ≠ `none` — **à vérifier sur la Preview** |
| TLS vers le stockage | **non testé** | le serveur local est en HTTP ; en recette le garde exige `https://` |
| rétention / ILM | **non testé** | Compose pose une règle d'expiration 1 j sur la quarantaine ; non vérifiable ici |

Aucun document de test ne contient de donnée réelle : ce sont des PDF générés par
le harnais (`declaration-fournisseur.pdf`, `emballage-fictif.pdf`) et l'image
d'étiquette.

## 4. Actions de test destructives : périmètre

Le harnais supprime bien des objets — mais **uniquement** :

- dans des buckets créés par l'exécution ;
- sur des clés construites par l'application elle-même ;
- dans un répertoire jetable (`.recette/`), détruit à la fin de l'exécution.

Aucun bucket partagé, aucun objet persistant, aucune donnée client n'a été touché.

## 5. Risques résiduels

| Risque | Sévérité | Commentaire |
|---|---|---|
| SSE non configuré en recette | P1 pour un pilote | chiffrement au repos exigé ; à vérifier sur la Preview |
| Rétention non automatisée hors quarantaine | P2 | le registre de purge existe, la règle de rétention long terme n'est pas outillée |
| Filet CORS MinIO | P2 | `MINIO_API_CORS_ALLOW_ORIGIN` doit contenir **uniquement** l'origine web ; non vérifiable ici |
| Compte de stockage partagé avec d'autres produits | P1 si vrai | un compte root MinIO en recette annulerait l'isolation ; à confirmer par l'assistant |

## 6. Verdict de la phase

- Le stockage documentaire est **réel** (adaptateur S3 non modifié, protocole réel)
  et les 14 points obligatoires de la mission sont couverts.
- Les documents sont **persistants, privés et isolés par organisation** : bucket
  non public (403 sans signature), 404 inter-tenant, isolation prouvée en base par
  RLS.
- Deux contrôles de recette restent à faire côté infrastructure : **chiffrement
  au repos** et **TLS vers le stockage**.
