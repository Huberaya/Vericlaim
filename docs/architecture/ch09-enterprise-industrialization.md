# Architecture Technique — Chantier 9 : Industrialisation Entreprise & Gouvernance B2B

## 1. Contexte et Objectifs

Le Chantier 9 dote **VeriClaim AI** des capacités d'exploitation et de gouvernance requises par les grands comptes (ETI & CAC40 / SBF120) et leurs départements conformité, juridique et DSI :

1. **Sécurité & Accès Machine-to-Machine (M2M)** : Authentification par clés d'API partenaires hachées (SHA-256 + salt), portées par des scopes granulaires (`audit:run`, `catalog:read`, `evidence:manage`, etc.) et associées à des quotas de requêtes stricts (Rate Limiting).
2. **Observabilité & Métriques d'Exploitation (SLA)** : Surveillance en temps réel du temps de disponibilité (Uptime), de la latence d'analyse déterministe, des volumes d'appels API, des taux d'erreur, et flux d'alertes sécurité/système.
3. **E-Discovery & Rétention Légale (Legal Holds)** : Possibilité pour les directions juridiques de verrouiller la purge de données d'un tenant en cas de contrôle de la DGCCRF, d'audit externe ou de litige commercial.
4. **Fédération d'Identité & SCIM 2.0 (RFC 7644)** : Provisionnement et synchronisation automatisée des utilisateurs depuis les fournisseurs d'identité d'entreprise (Entra ID / Azure AD, Okta, PingFederate).

---

## 2. Modèle de Données & Schéma Relationnel

### 2.1 Table `api_keys`

```sql
CREATE TABLE api_keys (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    prefix VARCHAR(16) NOT NULL,
    hashed_secret VARCHAR(128) NOT NULL,
    scopes JSONB NOT NULL DEFAULT '[]',
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 120,
    expires_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ NULL,
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

-- Isolation RLS PostgreSQL
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY p_api_keys_tenant ON api_keys
    USING (organization_id = vericlaim_current_organization_id())
    WITH CHECK (organization_id = vericlaim_current_organization_id());
```

**Propriétés de sécurité des clés :**
- Format généré : `vck_<prefix_8chars>_<secret_32chars>` (total 45 caractères).
- Le serveur ne persiste que le condensat `sha256(secret.encode("utf-8")).hexdigest()`. Le secret brut n'est retourné qu'une unique fois lors de la création et n'est jamais stocké en clair.
- Révocation immédiate sans suppression physique (conservation de la piste d'audit).

### 2.2 Table `legal_holds`

```sql
CREATE TABLE legal_holds (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    case_reference VARCHAR(255) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

-- Isolation RLS PostgreSQL
ALTER TABLE legal_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_holds FORCE ROW LEVEL SECURITY;
CREATE POLICY p_legal_holds_tenant ON legal_holds
    USING (organization_id = vericlaim_current_organization_id())
    WITH CHECK (organization_id = vericlaim_current_organization_id());
```

---

## 3. Endpoints de l'API Entreprise (`/api/v1/enterprise`)

| Méthode | Route | Rôle / Permission | Description |
|---|---|---|---|
| `POST` | `/api-keys` | `organization:manage` / `owner` | Génère une clé d'API avec scopes et quota |
| `GET` | `/api-keys` | `organization:read` / `analyst` | Liste les clés d'API (préfixes masqués) |
| `DELETE` | `/api-keys/{key_id}` | `organization:manage` / `owner` | Révoque immédiatement une clé d'API |
| `GET` | `/metrics` | `organization:read` | Retourne les KPIs de santé & SLA système |
| `GET` | `/alerts` | `organization:read` | Retourne les alertes système récentes |
| `POST` | `/scim/v2/Users` | Token Admin SCIM | Provisionne un utilisateur selon RFC 7644 |
| `POST` | `/legal-holds` | `organization:manage` / `owner` | Active un gel légal sur le tenant |
| `GET` | `/legal-holds` | `organization:read` | Liste les gels légaux actifs |

---

## 4. Architecture de la Synchronisation SCIM 2.0

VeriClaim AI implémente les spécifications RFC 7643 et RFC 7644 :
- **Schemas supportés** : `urn:ietf:params:scim:schemas:core:2.0:User`
- **Mappage de rôles** : Les groupes IdP sont traduits de manière déterministe vers les rôles RBAC VeriClaim (`analyst`, `viewer`, `admin`).
- **Idempotence & Sécurité** : La création ou mise à jour vérifie l'existence préalable de l'adresse email et rattache l'utilisateur à l'organisation cible avec isolation stricte.

---

## 5. Matrice d'Observabilité & SLA

```
┌────────────────────────────────────────────────────────┐
│               Observabilité & SLA VeriClaim             │
├──────────────────────────┬─────────────────────────────┤
│ Uptime de la plateforme  │ 99.98% contractuel          │
│ Latence moyenne moteur   │ < 45 ms (déterministe)      │
│ Quotas M2M par défaut    │ 120 req / min (ajustable)   │
│ Rétention journaux audit │ 10 ans (archivage scellé)   │
│ Rétention légale         │ Verrouillage Legal Hold     │
└──────────────────────────┴─────────────────────────────┘
```
