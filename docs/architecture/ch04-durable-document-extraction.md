# Chantier 4 — extraction documentaire durable et asynchrone

> **Statut : MVP d’extraction traçable.** Cette brique produit une transcription technique et des passages citables à partir d’une version documentaire déjà promue comme propre. Elle n’établit pas l’authenticité d’une pièce, ne qualifie pas juridiquement une allégation et ne remplace pas une revue humaine, en particulier après OCR.

## Objectif et périmètre

Le Chantier 4 sépare l’extraction potentiellement longue (PDF, image, Tesseract) du processus API et la rend durable sans Redis, Celery ni RabbitMQ. La file est une table PostgreSQL tenant-scoped et le worker est un processus Compose distinct.

Il couvre :

- l’enqueue transactionnel après la promotion propre d’une `DocumentVersion` ;
- TXT, PDF avec texte natif, PDF/image nécessitant OCR Tesseract ;
- la conservation privée de la transcription canonique UTF-8 et de son SHA-256 ;
- des `DocumentSegment` citables avec numéro de page, offsets de caractères dans la transcription canonique et type de segment ;
- leases, retries bornés, reprise après arrêt d’un worker, idempotence, RLS et audit ;
- la consultation des segments et une relance manuelle explicite après échec.

Il ne couvre **pas** : LLM, RAG, résumé, extraction structurée de tableaux, détection automatique de claims, analyse réglementaire, création de preuve, OCR considéré comme preuve fiable, ni écrasement d’une extraction réussie. Ces capacités devront être versionnées et validées dans des chantiers ultérieurs.

## Frontière de confiance

Le worker ne lit jamais un upload de quarantaine. Il lit uniquement l’objet de `DocumentVersion` déjà promu dans le bucket propre par le Chantier 3. Juste avant de donner les octets à PyMuPDF, Pillow ou Tesseract, il :

1. relit l’objet de manière bornée (`MAX_UPLOAD_BYTES`) ;
2. recalcule son SHA-256 et le compare au hash immuable de `DocumentVersion` ;
3. refait le contrôle cohérent extension / MIME / signature binaire ;
4. rejette définitivement la tâche si l’objet propre a disparu, a été modifié ou ne respecte plus le format attendu.

Ainsi, un objet remplacé après le scan ClamAV ne parvient pas au parseur ou à l’OCR. Les hashes restent des traces d’intégrité : ils ne prouvent ni la provenance fournisseur, ni l’authenticité, ni la valeur juridique du document.

Le texte extrait est écrit dans le bucket propre privé sous une clé dérivée opaque côté API, puis référencé par `DocumentVersion.extracted_text_storage_key`. Cette clé et la clé de l’original ne sont pas renvoyées par l’API. Le hash `extracted_text_sha256` permet de contrôler l’intégrité du transcript conservé.

## Modèle durable et invariants

La migration Alembic `e17c4f5a9b02` ajoute `document_extraction_jobs`.

| Invariant | Mise en œuvre |
|---|---|
| Une extraction par version immuable | Contrainte unique sur `document_version_id`. |
| Isolation organisation | `organization_id` obligatoire, index tenant, contrôles applicatifs et policy RLS PostgreSQL `p_document_extraction_jobs_tenant`. |
| Pas de succès écrasé | Le claim ignore une version `completed` / `review_required`; le retry API refuse un job terminé. Une nouvelle capacité d’extraction devra créer une nouvelle version/version de résultat. |
| Claim exclusif | `SELECT … FOR UPDATE SKIP LOCKED` sur les jobs dus et lease horodaté. |
| Reprise après crash | Un lease `running` expiré est remis en queue si le budget d’essais le permet, sinon marqué `failed`. |
| Retry borné | `attempt_count`, `max_attempts`, backoff exponentiel plafonné à une heure. Les erreurs de stockage transitoires sont retryables ; les erreurs d’intégrité/format sont terminales. |
| Traçabilité | Événements `document.extraction_queued`, `started`, `completed` ou `review_required`, `retry_scheduled`, `lease_requeued`, `failed` et `retry_requested`. |

La création de la `DocumentVersion` propre, du job et de l’événement `queued` se produit dans la même transaction PostgreSQL. Un worker ne peut donc pas voir une version durablement validée sans job correspondant.

Une écriture objet et une transaction de base ne forment pas une transaction distribuée : si une validation DB échoue après l’écriture du transcript, l’objet dérivé reste privé et non référencé. Les tentatives utilisent une clé dérivée par job/numéro d’essai et une tâche de réconciliation/rétention doit supprimer ces artefacts non référencés en exploitation.

## Flux et états

```text
navigateur / API
  └─ Chantier 3 : quarantaine → validation → ClamAV → promotion propre
                                                    │
                                                    ├─ DocumentVersion(extraction_status=pending)
                                                    ├─ DocumentExtractionJob(status=queued)
                                                    └─ audit document.extraction_queued

processus document-worker
  └─ découvre les organisations actives
       └─ installe le contexte RLS de l’organisation
            └─ claim PostgreSQL + lease
                 └─ relit / re-hashe l’objet propre
                      └─ TXT | PDF texte | OCR PDF/image
                           ├─ texte canonique privé + SHA-256
                           ├─ DocumentSegment(page, offsets, type)
                           └─ statut final + audit
```

### États fonctionnels

| Ressource | États pertinents | Sens |
|---|---|---|
| `DocumentVersion.extraction_status` | `pending`, `running`, `completed`, `review_required`, `failed` | `review_required` signifie qu’au moins une page a nécessité Tesseract ; cela n’est pas un échec, mais impose une comparaison humaine avec l’original. |
| `DocumentExtractionJob.status` | `queued`, `running`, `completed`, `failed` | État opérationnel de la file. Un job terminé reste terminé. |
| `Document.status` | `processing`, `ready`, `failed` | Vue de commodité sur la dernière version : `ready` vaut aussi pour une extraction `review_required`, afin que les passages puissent être relus. |

Les segments sont produits par paragraphes normalisés, et sont coupés de manière bornée lorsque nécessaire. `start_offset` et `end_offset` sont des offsets de caractères dans le transcript canonique, avec une fin exclusive. `page_number` est présent pour l’extraction actuelle. Les coordonnées (`bounding_box_json`) restent nulles dans ce MVP ; il n’y a pas de reconnaissance ni de restitution de structure tabulaire.

## Worker, RLS et équité

Le worker est lancé par :

```bash
cd backend
python -m app.workers.document_extraction_worker
```

En Compose, le service `document-worker` utilise la même image backend, ne publie aucun port, attend la santé de l’API (et donc les migrations), puis tourne avec l’UID numérique non privilégié `10001`.

L’annuaire des organisations actives est le seul accès global nécessaire. Pour chaque organisation, le worker installe ensuite `set_db_request_context()` avec un identifiant système non persistant et l’`organization_id` concerné avant le claim, le traitement ou l’enregistrement d’un échec. Les lectures/écritures de jobs, versions, documents et segments sont ainsi soumises aux policies RLS habituelles ; le worker ne dispose pas d’un contournement RLS global.

Les organisations sont parcourues en round-robin entre deux claims pour éviter qu’un tenant ayant beaucoup de jobs ne monopolise le worker unique. Plusieurs workers peuvent être lancés : `SKIP LOCKED` évite qu’ils exécutent le même claim simultanément. L’exécution est **au moins une fois**, pas exactement une fois ; les clés dérivées par tentative, le lease et les garde-fous d’immuabilité rendent les reprises sûres.

## API exposée

Toutes les routes suivent le préfixe `/api/v1`, exigent une session OIDC et une organisation active. Les identifiants cross-tenant sont volontairement indiscernables d’une ressource inexistante (`404`).

| Route | Permission | CSRF | Réponse / comportement |
|---|---|---:|---|
| `GET /document-versions/{version_id}/segments` | `documents:read` | non | Version tenant-safe, job éventuel et liste ordonnée de segments. Disponible aussi pendant `pending`/`running` mais sans extrapoler de texte non publié. |
| `POST /document-versions/{version_id}/extraction/retry` | `documents:manage` | oui | Requeue seulement une extraction `failed`, conserve le total historique des essais, ajoute un nouveau budget borné et journalise l’acteur. Un job `queued`/`running` est renvoyé idempotemment ; un succès reste non relançable. |

Le coffre documentaire frontend affiche l’état asynchrone, sonde uniquement `pending`/`running`, maintient le téléchargement de l’original propre et propose la relance uniquement aux rôles de gestion lors d’un échec. Pour l’OCR, il affiche explicitement la nécessité d’une revue humaine.

## Configuration et exploitation

Les paramètres sont documentés dans [`.env.example`](../../.env.example) :

- `DOCUMENT_EXTRACTION_MAX_ATTEMPTS` — budget initial d’essais, `3` par défaut ;
- `DOCUMENT_EXTRACTION_RETRY_BASE_SECONDS` — délai du premier retry (`30` s par défaut), doublé par tentative et plafonné à une heure ;
- `DOCUMENT_EXTRACTION_LEASE_SECONDS` — durée du lease ; elle doit couvrir au moins `MAX_PDF_PAGES × DOCUMENT_OCR_TIMEOUT_SECONDS + 60` ;
- `DOCUMENT_EXTRACTION_POLL_SECONDS` — pause sans travail ;
- `DOCUMENT_OCR_TIMEOUT_SECONDS`, `DOCUMENT_OCR_RENDER_SCALE`, `DOCUMENT_MAX_IMAGE_PIXELS`, `DOCUMENT_SEGMENT_MAX_CHARS` — bornes de coût et de sûreté pour OCR/rendu/segments.

En cas d’échec, ne pas modifier directement la base ni l’objet. Consulter le code d’erreur, les `AuditEvent` et le hash de la version ; corriger l’infrastructure (par exemple stockage indisponible), puis utiliser la relance authentifiée si l’extraction est `failed`. Une altération de hash/format ou l’absence de l’objet propre doit être investiguée comme un incident d’intégrité avant toute nouvelle version documentaire.

Les mêmes exigences que le Chantier 3 restent applicables : PostgreSQL avec RLS, MinIO/S3 privé chiffré, ClamAV avant promotion, TLS et identifiants de moindre privilège. Le worker doit avoir uniquement les accès objet nécessaires à la lecture du bucket propre et à l’écriture/suppression best-effort des transcripts dérivés dans ce bucket ; aucun accès public ou bucket listing n’est requis.

## Limites connues et suites nécessaires

- L’OCR peut mal lire un chiffre, une négation, un symbole ou une unité. `review_required` ne doit jamais être interprété comme une validation de contenu.
- Le transcript ne conserve pas encore de coordonnées de mots, de tableau ni de géométrie de mise en page.
- Une réconciliation opérationnelle des artefacts dérivés privés non référencés et une politique de rétention doivent être ajoutées avant passage à l’échelle.
- L’extraction n’associe aucune claim, preuve, règle ou conclusion réglementaire. Ces liens devront conserver une référence vers le segment, les sources et une validation humaine.
