# Chantier 3 — ingestion documentaire sécurisée

> **Statut : MVP de stockage et contrôle de sécurité.** Cette brique conserve des pièces fournies par les organisations pour les besoins de pré-audit. Elle ne vérifie pas leur authenticité, ne certifie pas leur contenu et ne produit pas d’avis juridique.

## Objectif et limites

Le parcours persistant accepte une pièce fournisseur ou un support marketing uniquement dans le tenant de l’organisation active. Un fichier ne devient jamais une `DocumentVersion` exploitable parce qu’un navigateur l’a téléversé : il doit d’abord être placé en quarantaine, contrôlé, scanné par ClamAV puis promu dans un bucket propre privé.

Ce chantier livre le stockage, la quarantaine, l’antivirus, les empreintes et le versioning immuable. Lors de sa promotion, une version propre est créée avec `extraction_status=pending`. Le Chantier 4 prend ensuite ce point de départ : il crée transactionnellement le job PostgreSQL durable, exécute l’extraction hors API et conserve les segments citables. Voir [`ch04-durable-document-extraction.md`](ch04-durable-document-extraction.md). L’association automatique aux claims reste hors périmètre.

## Frontière de confiance

| Élément | Décision |
|---|---|
| Clé objet | Créée exclusivement côté serveur à partir d’UUIDs, jamais reçue depuis le client et jamais exposée comme attribut d’une ressource API. Un POST S3 présigné contient techniquement le champ `key` opaque indispensable au formulaire : ce n’est ni une clé choisie par le client ni une autorisation réutilisable hors de sa policy courte. |
| Buckets | Deux buckets privés distincts : quarantaine et documents propres. Aucun listing ni URL publique. |
| Téléversement | URL POST présignée à durée courte vers `quarantine/{organization}/{document}/{upload}/source`. |
| Téléchargement | URL GET présignée courte, émise seulement après RBAC sur une version propre. |
| Formats | PDF, TXT UTF-8, PNG, JPEG, TIFF et WEBP uniquement. Extension, MIME déclaré et signature binaire doivent correspondre. |
| Taille | La policy S3 limite le POST; `head_object` puis une lecture bornée reconfirment la taille côté serveur. |
| Antivirus | ClamAV `INSTREAM` est appelé avant tout parser PDF, image ou OCR et avant la promotion. Une indisponibilité échoue fermé (`503`). |
| Promotion | La copie S3 utilise l’ETag obtenu avant scan comme condition `CopySourceIfMatch`; un remplacement/rejeu de l’objet pendant le scan fait échouer la promotion, plutôt que de promouvoir des octets non scannés. |
| Chiffrement | SSE S3/KMS obligatoire en staging/production. Le compose local de développement peut rester sans SSE si MinIO n’est pas relié à un KMS. |
| Isolation | Toutes les requêtes applicatives filtrent `organization_id`; PostgreSQL impose en plus la RLS sur `document_uploads`, `documents` et `document_versions`. |

Le flux historique `POST /api/v1/engine/evaluate` ne persiste pas le fichier, mais il applique désormais la même validation de signature et le même scan antivirus avant de passer les octets à `DocumentTextExtractor`. Il n’est donc pas un contournement du contrôle malware.

## Flux

```text
membre RBAC + CSRF
        │
        ├─ POST /documents ───────────────► Document(status=quarantined)
        │
        ├─ POST /documents/{id}/versions ─► DocumentUpload(pending_upload, expiration)
        │                                      │
        │ ◄──── URL POST + champs présignés ───┘
        │
 navigateur ── POST direct ───────────────► bucket de quarantaine privé
        │
        └─ POST /document-uploads/{id}/complete
                 │ head + lecture bornée + signature + SHA-256
                 │ ClamAV INSTREAM
                 ├─ rejet / indisponible : objet reste ou est supprimé de la quarantaine,
                 │                         sans version propre
                 └─ clean : copie vers bucket propre, DocumentVersion immuable,
                            audit event, suppression best-effort de la quarantaine
```

Les identifiants et les clés de stockage ne sont pas interchangeables. L’API renvoie un `upload_id` et, après validation, un `document_version_id`; les représentations de document/version ne renvoient jamais `quarantine_storage_key` ni `storage_key`. Le champ opaque `key` éventuellement présent dans les champs d’un POST présigné est l’exception protocolaire nécessaire au navigateur et doit être traité comme une capacité courte, non comme une référence d’objet.

## États importants

- `Document.status`: `quarantined` à la création; `uploaded` après promotion propre. Un rejet initial peut être marqué `failed` afin de rester visible à l’utilisateur et dans l’audit.
- `DocumentUpload.status`: `pending_upload` → `scanning` → `clean`, ou `rejected` / `failed` / `expired`.
- Une complétion propre est idempotente : répéter l’appel renvoie la même version. Une complétion rejetée ou expirée renvoie `409`; un upload expiré au moment de la complétion renvoie `410` et est supprimé au mieux.
- Une erreur de scanner, de stockage ou de promotion renvoie `503`; l’objet n’est pas promu et demeure inaccessible dans la quarantaine pour investigation/retention contrôlée.

Chaque création d’intent, rejet, expiration, échec de scan, promotion et émission d’une URL de téléchargement ajoute un `AuditEvent` tenant-scoped. Les empreintes SHA-256 sont des traces d’intégrité, pas des preuves de provenance ou d’authenticité.

## API et permissions

Toutes les routes demandent une session OIDC, une organisation active et, pour les mutations, l’en-tête CSRF valide.

| Route | Permission | Usage |
|---|---|---|
| `POST /api/v1/documents` | `documents:manage` | Crée le conteneur logique en quarantaine. |
| `GET /api/v1/documents/{document_id}` | `documents:read` | Consulte document, versions et intents sans clés objet. |
| `POST /api/v1/documents/{document_id}/versions` | `documents:manage` | Crée l’intent et l’URL POST présignée. |
| `POST /api/v1/document-uploads/{upload_id}/complete` | `documents:manage` | Lance les contrôles, le scan et la promotion. |
| `POST /api/v1/document-versions/{version_id}/download-url` | `documents:read` | Émet une URL GET courte vers un objet propre. |

Les rôles système ont les permissions suivantes : `owner`, `admin` et `analyst` peuvent gérer les documents; `viewer` peut les consulter/télécharger. La migration ajoute ces permissions aux rôles système existants par fusion, et le bootstrap maintient la même règle pour les schémas locaux. Les rôles personnalisés conservent leurs permissions existantes; aucune permission n’est retirée automatiquement.

## Configuration de déploiement

L’infrastructure retenue pour ce chantier est **MinIO S3-compatible + ClamAV**. En environnement partagé :

1. exécuter `alembic upgrade head` avant l’API ;
2. déployer MinIO derrière TLS, sur un réseau privé pour l’API et avec un endpoint public distinct seulement si le navigateur doit joindre les URLs présignées ;
3. définir `DOCUMENT_STORAGE_BACKEND=s3`, deux buckets MinIO distincts et privés, et les endpoints interne/externe correspondants ;
4. utiliser un service account MinIO de moindre privilège : `PutObject` seulement en quarantaine, `GetObject/DeleteObject` en quarantaine, `GetObject` sur propre et `PutObject` de promotion vers propre; pas de `ListBucket` public ;
5. raccorder MinIO à un KMS/KES, définir `DOCUMENT_STORAGE_SSE_MODE=aws:kms` et `DOCUMENT_STORAGE_SSE_KMS_KEY_ID`, puis imposer la même exigence dans la policy des buckets (refus de tout `PutObject` sans en-tête SSE attendu) ;
6. définir `DOCUMENT_SCANNER_MODE=clamav` et un endpoint ClamAV surveillé ;
7. restreindre `MINIO_API_CORS_ALLOW_ORIGIN` à l’origine web exacte et configurer `DOCUMENT_STORAGE_PUBLIC_ENDPOINT` lorsqu’un endpoint interne n’est pas joignable depuis le navigateur.

`DOCUMENT_STORAGE_ENDPOINT` sert aux appels backend. `DOCUMENT_STORAGE_PUBLIC_ENDPOINT` sert uniquement à signer les URLs visibles du navigateur. Dans Compose, le premier vaut `http://minio:9000` et le second `http://localhost:9000`; les deux utilisent le même stockage sans publier l’hôte Docker interne. Dans un déploiement partagé, les deux endpoints doivent être en HTTPS et le premier ne doit pas être exposé au navigateur. Les TTL sont volontairement plafonnées par l’application à 60 minutes pour un upload et 15 minutes pour un téléchargement; la taille de document est plafonnée à 50 Mo (15 Mo par défaut).

Le Compose fournit une topologie MinIO/ClamAV mono-nœud pour le développement. En staging/production, conserver MinIO et ClamAV mais déployer une topologie adaptée à la disponibilité attendue, avec images épinglées par digest, KMS, supervision, service account à privilèges minimaux et jamais des identifiants root dans l’API. La politique de rétention/quarantaine doit être validée par les responsables sécurité et protection des données.

## Garanties qui ne sont pas fournies

- Un verdict antivirus propre ne prouve pas qu’un document est sûr, exact, authentique ou juridiquement suffisant.
- L’API ne réalise pas encore de limite de débit globale, de DLP, de sandboxing de contenu actif, de validation de signature électronique ou de contrôle d’authenticité fournisseur.
- La suppression best-effort de quarantaine nécessite une tâche de réconciliation/retention avant une exploitation à grande échelle.
- Le téléchargement présigné est une capacité temporaire : un utilisateur habilité peut partager l’URL durant sa fenêtre de validité. Réduire la TTL et journaliser l’émission; ne pas y mettre de pièces plus sensibles que la politique de partage ne l’autorise.
