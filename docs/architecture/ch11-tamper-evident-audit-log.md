# Architecture Technique — Chantier 11 : Journal d'Audit Immuable & Chaîne Cryptographique (Tamper-Evident Ledger)

## 1. Contexte et Objectifs de Sécurité

Le **Chantier 11** fournit à **VeriClaim AI** un moteur de traçabilité cryptographique opposable (*Tamper-Evident Ledger*) répondant aux exigences des réglementations européennes, des auditeurs de conformité (ISO 14001, CSRD/ESRS) et des autorités de contrôle (DGCCRF, juridictions commerciales) :

1. **Sérialisation Immuable & Append-Only** : Tout événement métier critique (`analysis`, `claim`, `document`, `evidence`, `validation`, `apikey`, `legal_hold`) est persisté sans possibilité de modification ou de suppression en base.
2. **Hash-Chaining SHA-256 par Organisation** : Chaque événement intègre l'empreinte cryptographique de son prédécesseur immédiat (`previous_event_hash`) et calcule son propre condensat scellé (`event_hash`), formant une blockchain locale et cloisonnée par tenant.
3. **Vérification Mathématique d'Intégrité en Temps Réel** : L'API `/api/v1/audit/verify` recalcule instantanément l'intégralité de la chaîne et détecte toute tentative d'altération (modification de payload, suppression de ligne, insertion frauduleuse).
4. **Certificat d'Intégrité Cryptographique** : Émission d'attestations certifiées avec racine de condensat Merkle (`/api/v1/audit/integrity-certificate`).

---

## 2. Modèle Mathématique de Hash-Chaining

Pour tout événement $E_i$ d'une organisation $Org$ survenu à l'instant $T_i$ :

$$P_i = \text{SHA256}(\text{CanonicalJSON}(\text{Payload}_i))$$

$$\text{PrevHash}_i = \begin{cases} \text{"GENESIS"} & \text{si } i = 1 \\ E_{i-1}.\text{event\_hash} & \text{si } i > 1 \end{cases}$$

$$E_i.\text{event\_hash} = \text{SHA256}(Org : \text{EntityType} : \text{EntityId} : \text{Action} : T_{i,\text{ISO}} : P_i : \text{PrevHash}_i)$$

### Invariants Cryptographiques :
1. **Unicité & Intégrité** : Contraintes de clé unique `(organization_id, event_hash)` et `CHECK (length(event_hash) = 64)`.
2. **Isolation Tenant RLS** : Une organisation ne peut ni lire ni étendre la chaîne d'une autre organisation.
3. **Détection de Rupture** : La suppression de l'événement $E_k$ rend invalide le lien entre $E_{k-1}$ et $E_{k+1}$ ($\text{PrevHash}_{k+1} \neq E_{k-1}.\text{event\_hash}$).

---

## 3. Schéma Relationnel & Tables PostgreSQL

```sql
CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    entity_type VARCHAR(128) NOT NULL,
    entity_id UUID NULL,
    action VARCHAR(128) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id VARCHAR(128) NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_sha256 VARCHAR(64) NOT NULL,
    previous_event_hash VARCHAR(64) NULL,
    event_hash VARCHAR(64) NOT NULL,
    
    CONSTRAINT uq_audit_events_organization_hash UNIQUE (organization_id, event_hash),
    CONSTRAINT ck_audit_events_payload_sha256_length CHECK (length(payload_sha256) = 64),
    CONSTRAINT ck_audit_events_event_hash_length CHECK (length(event_hash) = 64),
    CONSTRAINT ck_audit_events_previous_hash_length CHECK (previous_event_hash IS NULL OR length(previous_event_hash) = 64)
);

-- Isolation PostgreSQL RLS
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY p_audit_events_tenant ON audit_events
    USING (organization_id = vericlaim_current_organization_id())
    WITH CHECK (organization_id = vericlaim_current_organization_id());
```

---

## 4. Endpoints API (`/api/v1/audit`)

| Méthode | Route | Permission / Rôle | Description |
|---|---|---|---|
| `GET` | `/events` | `audit:read` | Liste paginée et filtrée des événements scellés |
| `GET` | `/verify` | `audit:read` | Recalcul mathématique et validation de la chaîne |
| `GET` | `/integrity-certificate` | `audit:read` | Génération de l'attestation scellée avec condensat Merkle |

---

## 5. Interface Utilisateur & Explorateur de Piste d'Audit

Le composant React `frontend/src/components/AuditTrailExplorer.tsx` met à disposition :
- **Badge d'Intégrité en Temps Réel** : Statut vert (« Chaîne d'Audit Scellée & Mathématiquement Intègre ») avec condensat HEAD et horodatage de contrôle.
- **Bouton d'Audit Instantané** : Permet à un auditeur de relancer la vérification sur l'ensemble de l'historique du tenant.
- **Visualiseur de Certificat** : Modal de présentation des paramètres de scellement opposable.
- **Timeline & Table d'Événements** : Visualisation du chaînage des hash (`prev_hash ➔ event_hash`) pour chaque opération métier.
