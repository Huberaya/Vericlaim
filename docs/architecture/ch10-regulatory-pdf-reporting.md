# Architecture Technique — Chantier 10 : Génération des Rapports PDF d'Audit Réglementaire & Dossier Probatoire Opposable

## 1. Contexte et Objectifs Métier

Le **Chantier 10** fournit le moteur de restitution documentaire certifié de **VeriClaim**, permettant aux directions juridiques, RSE et achats d'exporter :

1. **Un Rapport PDF d'Audit Pré-Réglementaire Haute Fidélité** :
   - Mise en page normée A4 multipages avec en-têtes et pieds de page numérotés (« Page X / Y »).
   - Mentions légales impératives : *"Document interne de travail pré-audit et d'aide à la décision — Ne constitue ni un avis juridique formel ni un certificat officiel"*.
   - Synthèse exécutive des risques (Scorecard, verdict global, répartition des allégations par sévérité).
   - Ventilation exhaustive des allégations détectées avec citations juridiques exactes (Code de l'environnement, Directive UE 2024/825 EmpCo, normes ISO 14021) et recommandations de remédiation.
   - Piste d'audit cryptographique : empreinte SHA-256 du texte analysé, horodatage UTC / Paris, chaîne de scellement.
2. **Un Pack Probatoire Scellé au format ZIP** :
   - Rapport PDF d'audit (`rapport_pre_audit.pdf`).
   - Manifeste d'audit machine-readable signé (`manifeste_audit_scelle.json`).
   - Matrice tabulaire d'évaluation (`matrice_probatoire.csv` en UTF-8 avec BOM pour Excel).
   - Notice d'accompagnement juridique (`README_DOSSIER_AUDIT.txt`).

---

## 2. Architecture Technique du Moteur PDF (`backend/app/reports/`)

```
┌────────────────────────────────────────────────────────┐
│             Pipeline de Restitution Chantier 10         │
└────────────────────────────────────────────────────────┘
                          │
       ┌──────────────────┴──────────────────┐
       ▼                                     ▼
POST /api/v1/reports/pdf             POST /api/v1/reports/dossier
GET  /analyses/{id}/pdf              GET  /analyses/{id}/dossier
       │                                     │
       ▼                                     ▼
RegulatoryPdfReportGenerator         create_regulatory_dossier_zip
       │                                     ├─ rapport_pre_audit.pdf
       │ (PyMuPDF / fitz engine)             ├─ manifeste_audit_scelle.json
       ▼                                     ├─ matrice_probatoire.csv
Flux binaire PDF (%PDF-1.4)                  └─ README_DOSSIER_AUDIT.txt
                                             ▼
                                     Flux binaire ZIP (PK..)
```

### 2.1 Moteur PyMuPDF / Fitz
- **Performances** : Génération native en mémoire sans dépendance externe ni appel réseau, produisant un document complet en moins de 15 ms.
- **Règles de mise en page & pagination dynamique** :
  - Détection automatique des sauts de page (`_ensure_space`).
  - Palette colorimétrique corporate contrastée (Bleu institutionnel `#3d59bf`, Rouge critique `#d12626`, Ambre warning `#d98c0d`, Vert conformité `#149e59`).
  - Text-wrapping typographique avec césure automatique des textes d'allégation et des clauses de remédiation.

---

## 3. Matrice des Endpoints API (`/api/v1/reports`)

| Méthode | Route | Permission / Rôle | Content-Type | Usage |
|---|---|---|---|---|
| `POST` | `/pdf` | `audit:read` | `application/pdf` | Téléchargement immédiat du PDF depuis un rapport live |
| `POST` | `/dossier` | `audit:read` | `application/zip` | Téléchargement du pack probatoire ZIP (PDF+JSON+CSV) |
| `GET` | `/analyses/{id}/pdf` | `audit:read` | `application/pdf` | Export PDF d'une analyse persistante |
| `GET` | `/analyses/{id}/dossier` | `audit:read` | `application/zip` | Export pack ZIP d'une analyse persistante |

---

## 4. Sécurité Multi-Tenant & Intégrité Probatoire

1. **Isolation PostgreSQL RLS** :
   - L'accès aux analyses persistantes (`_get_analysis_or_404`) filtre strictement par `organization_id == principal.organization_id`. Toute tentative de requête par une organisation tierce déclenche un `404 Not Found` sans fuite de métadonnées.
2. **Scellement Cryptographique** :
   - Chaque PDF et pack ZIP intègre l'empreinte SHA-256 du texte brut analysé ainsi que l'identifiant de traçabilité de l'analyse.
3. **Double Avertissement Juridique** :
   - Présence systématique du disclaimer dans l'en-tête de page, dans le pied de page de chaque feuillet et dans le fichier `README_DOSSIER_AUDIT.txt`.
