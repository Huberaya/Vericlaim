# Chantier 8 — Pack Pilote B2B : Reporting Pré-Audit, Tableau de Bord 360°, Onboarding & Rétention

## Objet et Vision Métier

Le **Chantier 8** constitue le jalon de livraison permettant à une organisation cliente (Directions Achats, RSE, Juridique et Marketing) d'opérer un cycle complet et autonome de pré-audit réglementaire sur ses allégations environnementales.

Ce pack combine :
1. **Un tableau de bord exécutif 360° du risque** : Visibilité temps réel sur le score de conformité global, la couverture probatoire et l'exposition par fournisseur.
2. **La génération de rapports formels de pré-audit** : Rapports de synthèse avec avis de remédiation, signatures cryptographiques SHA-256 et avertissements légaux clairs.
3. **Un parcours d'onboarding et d'import contrôlé (Batch)** : Chargement rapide et idempotent de catalogues fournisseurs et produits en formats CSV/JSON.
4. **La réversibilité, l'archivage et la politique de rétention RGPD** : Export complet de dossier et conformité au stockage souverain européen.

---

## Fonctionnalités Livrées

### 1. Tableau de Bord Pilote 360° (`PilotOverviewKPIs`)
- **Taux de conformité global** : Calculé dynamiquement sur l'ensemble des allégations détectées et arbitrées.
- **Ventilation du portefeuille** :
  - Allégations : Validées / Contestées / En attente d'arbitrage.
  - Preuves : Couvertes / Manquantes / Expirées.
  - Risques critiques : Présence de mentions interdites par la loi AGEC (biodégradable, respectueux de l'environnement) ou compensations carbone non étayées.
- **Top Fournisseurs à risque** : Identification des partenaires nécessitant des compléments probatoires (bilans ACV ISO 14044, écolabels, attestations de filières).

### 2. Rapport de Synthèse Pré-Audit Exécutif (`PreAuditReportResponse`)
- **Numérotation unique & Horodatage** : Ex. `PREAUDIT-20260929-A1B2C3D4`.
- **Piste d'audit cryptographique** : Empreinte SHA-256 scellant les constats, la version du Rule Book et les métadonnées de l'organisation.
- **Tableau détaillé des constats** : Texte de l'allégation, sévérité, base légale, état de preuve, décision d'arbitrage et conseil de remédiation priorisé.
- **Exports multiples** : JSON structuré, impression navigateur / PDF.

### 3. Import Batch Contrôlé du Catalogue (`CatalogBatchImportModal`)
- **Format flexible** : CSV (point-virgule, virgule, tabulation) ou JSON.
- **Validation & Idempotence** : Réutilisation automatique des fournisseurs et produits existants pour éviter les doublons.
- **Journalisation d'audit** : Événement `pilot.catalog_imported` consigné dans `audit_events`.

### 4. Rétention, Portabilité & RGPD
- **Export Dossier Complet** : Extraction complète en un clic des fournisseurs, produits, documents hashés, allégations, preuves et validations.
- **Politique de conservation normée** : 5 ans pour les documents/preuves, 10 ans pour la piste d'audit SHA-256, hébergement souverain EU.

---

## Endpoints API Authentifiés (`backend/app/api/v1/pilot.py`)

| Méthode | Route | Permission | Description |
|---|---|---|---|
| `GET` | `/api/v1/pilot/overview` | `audit:read` | Tableau de bord 360° et indicateurs de pilotage |
| `GET` | `/api/v1/pilot/pre-audit-report` | `audit:read` | Rapport de pré-audit consolidé avec signature cryptographique |
| `POST` | `/api/v1/pilot/import-catalog` | `catalog:manage` (CSRF) | Import batch contrôlé de fournisseurs et produits |
| `GET` | `/api/v1/pilot/export-dossier` | `audit:read` | Export JSON exhaustif du dossier de conformité |
| `GET` | `/api/v1/pilot/retention-policy` | `organization:read` | Politique de conservation des données et conformité RGPD |
