# Manuel d'Exploitation & Référentiel Sécurité — VeriClaim

## 1. Vue d'Ensemble & Posture de Sécurité

VeriClaim est conçu selon le principe de **Défense en Profondeur** (*Defense in Depth*) pour répondre aux exigences réglementaires européennes (RGPD, NIS 2, DORA) et aux standards d'audit SOC 2 Type II / ISO 27001.

---

## 2. Piliers d'Isolation & Cloisonnement Multi-Tenant

1. **Isolation PostgreSQL RLS (Row-Level Security)** :
   - Chaque requête SQL s'exécute dans un contexte de session où la variable `app.current_organization_id` est positionnée.
   - Les politiques RLS sont configurées en mode `FORCE ROW LEVEL SECURITY` sur l'ensemble des tables métier (`analyses`, `documents`, `suppliers`, `products`, `evidence_registry`, `evidence_requests`, `api_keys`, `legal_holds`).
   - Aucune fuite de données inter-organisations n'est techniquement possible, même en cas de mauvaise jointure au niveau de l'ORM.

2. **Scellement Cryptographique des Documents** :
   - Tout document téléversé est haché (SHA-256) avant ingestion.
   - Analyse antivirale asynchrone (ClamAV) en zone de quarantaine avant promotion en stockage d'archive chiffré (AES-256-GCM / AWS SSE-S3).

3. **Protection contre les Vulnérabilités Web OWASP Top 10** :
   - Jetons de session opaques stockés en cookie `HttpOnly`, `SameSite=Lax/Strict`, `Secure`.
   - Double vérification CSRF : en-tête `x-csrf-token` obligatoire pour toute mutation d'état.
   - Content Security Policy (CSP) stricte sans injection de scripts non autorisés.

---

## 3. Gestion des Clés d'API & Accès Machine-to-Machine

- **Format** : `vck_<8_hex_prefix>_<32_hex_secret>`.
- **Hachage** : Condensat `sha256` non réversible stocké en base de données.
- **Révocation** : Effet immédiat via l'endpoint `/api/v1/enterprise/api-keys/{id}` ou l'interface d'administration.
- **Rate Limiting** : Limitation du débit par minute (120 req/min par défaut, configurable par clé).

---

## 4. E-Discovery & Gel Légal (Legal Hold)

En cas de contrôle DGCCRF, d'injonction judiciaire ou de litige contractuel :
1. Un administrateur ou juriste active un **Legal Hold** via le panneau d'administration Entreprise ou l'API `/api/v1/enterprise/legal-holds`.
2. L'activation du gel suspend automatiquement toutes les purges et destructions programmées par les règles de rétention standard.
3. L'export probatoire complet (`/api/v1/pilot/export-dossier`) génère un dossier ZIP opposable contenant l'intégralité des preuves, rapports d'audit et journaux de scellement horodatés.

---

## 5. Procédure de Réponse à Incident (SecOps Runbook)

### 5.1 Détection d'une compromission de clé d'API
1. Identifier la clé compromise à l'aide de son préfixe (`vck_abcd1234_...`).
2. Révoquer la clé immédiatement via l'API d'administration.
3. Auditer les journaux d'accès (`audit_events`) sur la période suspecte.
4. Générer une nouvelle clé et notifier le responsable technique du client.

### 5.2 Alerte d'intégrité ou d'échec d'analyse virale
1. Le document suspect reste isolé dans le bucket de quarantaine `quarantine-uploads`.
2. L'événement est enregistré dans les alertes système avec sévérité `high`.
3. L'utilisateur reçoit une notification de rejet sans dégradation du service.

---

## 6. Sauvegardes & Reprise d'Activité (DRP / BCP)

- **PostgreSQL (Neon Cloud)** : Snapshots automatisés en continu (PITR - Point-in-Time Recovery) avec conservation de 30 jours.
- **Stockage Objets (S3 / MinIO)** : Versioning activé et réplication géographique multi-régions.
- **RPO cible (Recovery Point Objective)** : < 5 minutes.
- **RTO cible (Recovery Time Objective)** : < 30 minutes.
