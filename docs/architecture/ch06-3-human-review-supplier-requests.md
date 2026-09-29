# Chantier 6.3 — Arbitrage Humain (Validations) & Demandes de Preuves Fournisseurs

## Objet et Vision Human-in-the-Loop

Le Chantier 6.3 complète la chaîne d'évaluation de la conformité en implémentant deux mécanismes essentiels de gouvernance et de remédiation :

1. **L'Arbitrage Humain (`Validation`)** :
   - Conformément aux exigences éthiques et réglementaires européennes (Directive EmpCo 2024/825 et AI Act), l'IA n'émet aucun verdict juridique automatique.
   - Les auditeurs, juristes et analystes de conformité disposent d'un parcours d'arbitrage explicite permettant d'enregistrer des décisions motivées (`validated`, `contested`, `pending`) sur chaque allégation détectée.
   - L'historique d'arbitrage est immuable et auditable, conservant l'identifiant du relecteur, son commentaire d'expertise et son fondement légal.

2. **Les Demandes de Preuves Fournisseurs (`EvidenceRequest`)** :
   - Lorsqu'une allégation manque de pièces probatoires (bilan ACV, certificat écolabel, attestation de recyclabilité), l'analyste peut générer et suivre une demande formelle contradictoire auprès du fournisseur du catalogue.
   - Un générateur déterministe pré-remplit l'objet, le texte juridique de mise en demeure et la liste des documents normés requis en fonction de l'allégation ciblée (référençant explicitement les articles de loi applicables : Loi AGEC Art. L. 541-9-1, Décret 2022-748, Directive 2024/825).
   - Cycle de vie complet de la demande : `draft` → `sent` → `received` / `fulfilled` / `overdue` / `cancelled`, avec gestion de relances journalisées (`reminded`).

## Modèle de données & Invariants

### 1. `Validation` (Arbitrage Humain)
- `id` : UUID unique.
- `organization_id` : Tenant propriétaire obligatoire avec RLS.
- `analysis_version_id` : Référence immuable vers la version d'analyse C5 évaluée.
- `claim_id` : Référence vers l'allégation auditée.
- `decision` : `pending` | `validated` | `contested`.
- `reviewer_user_id` : Identifiant de l'utilisateur authentifié ayant validé ou contesté.
- `comment` : Commentaire explicatif libre de l'expert.
- `rationale` : Référence ou base juridique (ex. *Conformité Art. L. 229-68 Code Environnement*).
- `decided_at` : Timestamp ISO 8601 UTC.

### 2. `EvidenceRequest` (Demande Fournisseur)
- `id` : UUID unique.
- `organization_id` : Tenant propriétaire obligatoire avec RLS.
- `supplier_id` : Clé étrangère vers le fournisseur du catalogue.
- `product_id` : Clé étrangère optionnelle vers le produit audité.
- `claim_id` : Clé étrangère optionnelle vers l'allégation nécessitant une justification.
- `status` : `draft` | `sent` | `received` | `fulfilled` | `cancelled` | `overdue`.
- `subject` : Objet formel du message.
- `message` : Contenu détaillé précisant le cadre réglementaire et les pièces exigées.
- `requested_items_json` : Liste typée des pièces demandées (`type`, `name`, `notes`).
- `due_at` : Date d'échéance contractuelle.
- `sent_at` : Timestamp de l'envoi formel.
- `last_reminded_at` : Timestamp de la dernière relance effectuée.

## API REST & Sécurité

### Routes Validations
- `POST /api/v1/validations` (`validation:manage`, CSRF) : Enregistre ou met à jour une décision d'arbitrage.
- `GET /api/v1/analyses/{analysis_id}/versions/{version_number}/validations` (`validation:read`) : Liste les arbitrages d'une version d'analyse.

### Routes Demandes Fournisseurs
- `POST /api/v1/evidence-requests` (`evidence_requests:manage`, CSRF) : Crée une nouvelle demande de justificatif.
- `GET /api/v1/evidence-requests` (`evidence_requests:read`) : Liste paginée avec filtres (fournisseur, statut).
- `GET /api/v1/evidence-requests/{request_id}` (`evidence_requests:read`) : Détail d'une demande.
- `PATCH /api/v1/evidence-requests/{request_id}` (`evidence_requests:manage`, CSRF) : Mise à jour du statut ou des métadonnées.
- `POST /api/v1/evidence-requests/{request_id}/send` (`evidence_requests:manage`, CSRF) : Passe le statut en `sent` avec horodatage.
- `POST /api/v1/evidence-requests/{request_id}/remind` (`evidence_requests:manage`, CSRF) : Enregistre une relance formelle avec horodatage et audit trail.
- `DELETE /api/v1/evidence-requests/{request_id}` (`evidence_requests:manage`, CSRF) : Soft delete d'une demande.
- `POST /api/v1/evidence-requests/generate-template` (`evidence_requests:read`) : Générateur de modèles contextuels sans hallucination.

## Journal d'Audit & Conformité
Toutes les actions d'arbitrage et de demandes de preuves sont tracées dans `audit_events` :
- `validation.created` / `validation.updated`
- `evidence_request.created` / `evidence_request.sent` / `evidence_request.reminded` / `evidence_request.updated` / `evidence_request.deleted`
