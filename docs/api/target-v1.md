# API cible v1 — contrat d’architecture

> Mise à jour Chantier 6.1 : les routes d’identité/organisation/RBAC du Chantier 2, le parcours documentaire sécurisé du Chantier 3, l’extraction durable du Chantier 4, la détection déterministe persistante des claims du Chantier 5 et le catalogue fournisseurs/produits du Chantier 6.1 sont implémentés. Les autres routes métier versionnées ci-dessous restent des cibles de contrat.

## Principes transverses

- Préfixe : `/api/v1`.
- Toute ressource métier est filtrée par l’organisation active résolue depuis l’identité authentifiée. `organization_id` ne provient jamais du client.
- UUIDs publics ; pagination cursor-based ; `Idempotency-Key` sur les créations longues/importantes.
- Toute réponse d’analyse expose son `analysis_version`, `engine_version`, `rulebook_version`, hashes et limitations.
- Toute mutation produit un `AuditEvent` avec `actor`, `request_id`, ressource, action et hash de payload.
- Les fichiers passent par une demande d’upload/quarantaine et non par une injection binaire dans le serveur d’analyse.
- Les routes signalées **implémentées** sont disponibles; les autres restent des cibles de contrat et ne doivent pas être supposées disponibles.

## Routes d’identité et organisation — implémentées au Chantier 2

- `GET /auth/status`, `GET /auth/login`, `GET /auth/callback`, `GET /auth/me`, `POST /auth/logout`
- `POST /auth/active-organization`
- `GET/POST /organizations`, `GET/PATCH /organizations/current`
- `GET /organizations/current/roles`, `GET /organizations/current/members`
- `POST /organizations/current/members/invitations`
- `PATCH /organizations/current/members/{userId}/role`, `DELETE /organizations/current/members/{userId}`

Ces routes utilisent une session OIDC opaque, une organisation active stockée côté serveur et un jeton CSRF pour toute mutation. Le détail de sécurité et la matrice RBAC sont documentés dans [`../architecture/ch02-identity-tenancy.md`](../architecture/ch02-identity-tenancy.md).

## Documents

| Route | Statut | Intention / réponse |
|---|---|---|
| `POST /documents` | **Implémentée** | Crée le conteneur logique tenant-scoped (`201`), sans accepter d’octets ni de clé objet. Permission `documents:manage` + CSRF. |
| `POST /documents/{documentId}/versions` | **Implémentée** | Crée un intent de quarantaine et un formulaire POST S3 signé court (`201`). Extension/MIME/taille/empreinte déclarée sont contrôlés; la clé reste générée serveur. Permission `documents:manage` + CSRF. |
| `POST /document-uploads/{uploadId}/complete` | **Implémentée** | Relit l’objet borné, valide signature et SHA-256, appelle ClamAV puis promeut une version propre (`201`; `200` idempotent; `422` rejet; `410` expiration; `503` indisponibilité). La même transaction crée le job d’extraction durable ; aucun OCR n’est exécuté dans la requête API. |
| `GET /documents/{documentId}` | **Implémentée** | Métadonnées tenant-safe, versions, statut d’extraction/job et statuts d’intent sans clé de stockage (`200`). Permission `documents:read`. |
| `POST /document-versions/{versionId}/download-url` | **Implémentée** | Émet une URL GET présignée courte vers une version propre (`200`). Permission `documents:read` + CSRF. |
| `GET /document-versions/{versionId}/segments` | **Implémentée** | Retourne la version, le job et les segments ordonnés (page, offsets, type, hash source) sans clé objet. Permission `documents:read`. |
| `POST /document-versions/{versionId}/extraction/retry` | **Implémentée** | Relance explicitement une extraction `failed`; résultat/idempotence de job (`200`). Permission `documents:manage` + CSRF. Une extraction réussie n’est jamais relancée par cette route. |
| `GET /documents` | Cible | Recherche paginée par fournisseur, produit, tag, statut, type, date. |

## Analyses et résultats

| Route | Statut / intention |
|---|---|
| `POST /analyses` | **Implémentée (C5)**. Accepte `document_version_ids` (seulement extraction `completed`/`review_required`), `supplier_id`/`product_id` optionnels et l’en-tête obligatoire `Idempotency-Key`. Crée transactionnellement `Analysis`, `AnalysisVersion`, manifeste SHA-256 et job PostgreSQL tenant-scoped, puis retourne `202`. Permission `audit:run` + CSRF. |
| `GET /analyses/{analysisId}` | **Implémentée (C5)**. État du dossier et versions persistées, tenant-safe. Permission `audit:read`. |
| `GET /analyses/{analysisId}/versions/{version}` | **Implémentée (C5)**. Snapshot de la version : manifeste d’entrée, hashes, job et claims citables/provenance. Permission `audit:read`. |
| `GET /claims/{claimId}` | **Implémentée (C5)**. Claim, segment/page/offsets/hash source et attributs du détecteur, sans relation de preuve/risk/validation. Permission `audit:read`. |
| `POST /analyses/{analysisId}/retry` | **Implémentée (C5)**. `Idempotency-Key` obligatoire, crée une nouvelle version et un nouveau job sans modifier les résultats antérieurs. Permission `audit:run` + CSRF. |

### Limite de périmètre C5

Le worker C5 réutilise `FactExtractor` **sur les `DocumentSegment` persistés**, jamais le binaire stocké et jamais `InferenceEvaluator`. Il persiste des candidats citables déterministes et leur provenance uniquement. Les claims de segment OCR sont `review_required`; `confidence_score` reste `null` (aucun score arbitraire). Cette version ne crée pas de verdict réglementaire, score juridique, lien de preuve, risque, recommandation, déduplication sémantique, sortie LLM/RAG ni rapport PDF.

## Preuves et fournisseurs

| Route cible | Intention |
|---|---|
| `POST /suppliers`, `GET /suppliers`, `GET/PATCH/DELETE /suppliers/{supplierId}` | **Implémentées (C6.1)**. CRUD tenant-scoped du fournisseur ; créations idempotentes, soft archive et audit. `catalog:read` / `catalog:manage`. Aucun Supplier Risk Profile. |
| `POST /products`, `GET /products`, `GET/PATCH/DELETE /products/{productId}` | **Implémentées (C6.1)**. Produit rattaché à un fournisseur actif du tenant ; relation immuable, créations idempotentes, soft archive et audit. `catalog:read` / `catalog:manage`. |
| `POST /evidence` | Cible C6.2 : déclarer/lier une preuve à une pièce existante ; jamais l’auto-déclarer comme vérifiée. |
| `POST /claims/{claimId}/evidence-links` | Créer une relation claim → preuve avec couverture, validité et statut de revue. |
| `POST /evidence-requests` | Brouillon/envoi de demande fournisseur, échéance et relances. |

## Référentiel et validations humaines

| Route cible | Intention |
|---|---|
| `GET /regulations`, `GET /rules` | Consulter la version active, sources et dates de vérification. |
| `POST /rules` / `POST /rules/{id}/versions` | Administration réservée, workflow d’approbation juridique. |
| `POST /validations` | Valider, contester ou surcharger un résultat avec justification. |
| `GET /audit-events` | Consultation filtrée et autorisée du journal métier. |
| `POST /reports` | Demander un rapport versionné ; retourne `202` puis URL signée lorsqu’il est prêt. |

## Compatibilité du moteur actuel

`POST /api/v1/engine/evaluate` et `GET /api/v1/engine/rules` restent des endpoints de prototype pendant la migration. Depuis le Chantier 2, ils exigent une session SSO, une organisation active et les permissions respectives `audit:run` / `rules:read` (`evaluate` exige aussi CSRF). Depuis le Chantier 3, un multipart envoyé à `evaluate` est validé (extension/MIME/signature) et scanné par ClamAV avant `DocumentTextExtractor`, mais reste éphémère : il ne crée pas de `DocumentVersion`. Ils ne doivent pas devenir les endpoints multi-tenant définitifs : la future façade `POST /analyses` encapsulera le moteur après stockage de versions et orchestration asynchrone.

## Exemple de ressource d’analyse cible

```json
{
  "analysis": {
    "id": "7cf2e52d-5af5-4d49-86e2-9d61c0f06153",
    "status": "completed"
  },
  "latest_version": {
    "version_number": 3,
    "status": "completed",
    "engine_version": "vericlaim-fact-extractor-v1",
    "rulebook_version": "not-applicable-deterministic-claims-v1",
    "input_manifest_sha256": "…",
    "result_sha256": "…"
  },
  "disclaimer": "VeriClaim AI fournit une détection automatisée d’allégations pour le pré-audit et la gestion du risque. Ce résultat ne constitue pas un avis juridique, une certification ni une décision d’autorité."
}
```
