# Chantier 6.2 — Registre Probatoire Persistant & Matrice Allégation ↔ Preuve

## Objet et frontière produit

Le Chantier 6.2 introduit le **Registre Probatoire d'Entreprise** (`Evidence`) et la **Matrice d'Imputation Allégation ↔ Preuve** (`EvidenceLink`), permettant à un opérateur de conformité ou un juriste d'associer de manière explicite et vérifiable les allégations détectées aux pièces probatoires (bilans ACV, certificats ISO 14044, attestations de recyclabilité Citeo/Léko, déclarations d'émissions GES).

L'outil reste un système d'aide à la décision et de gestion du risque de conformité :
- Il ne délivre aucune certification légale ;
- Il calcule l'état de couverture probatoire de manière déterministe (présente, manquante, expirée, hors périmètre) ;
- Tout lien d'imputation conserve sa traçabilité (utilisateur, date, niveau de confiance, justification).

## Modèle de données & Invariants

### Preuve (`Evidence`)

1. **Isolation Multi-tenant & RLS** : Toute preuve possède un `organization_id` obligatoire, indexé et filtré.
2. **Typologie Réglementaire Normée** :
   - `lca_report` : Analyse de Cycle de Vie (ISO 14040 / 14044).
   - `certificate` : Écolabel, certification tierce partie accréditée.
   - `recycling_route` : Attestation de filière de recyclage opérationnelle (AGEC R541-221).
   - `ghg_inventory` : Bilan d'émissions GES (Scope 1, 2, 3).
   - `ghg_reduction_plan` : Trajectoire de décarbonation alignée SBTi / SNBC.
   - `carbon_offset` : Crédits de compensation carbone (date-gated).
   - `environmental_declaration` : Déclaration fournisseur formelle.
   - `lab_report` : Rapports d'essais en laboratoire indépendant.
3. **Périmètre & Validité Temporelle** :
   - Invariant : `expires_on >= issued_on`.
   - Contrôle dynamique de péremption : si `expires_on < today`, la couverture bascule automatiquement en `expired`.
4. **Intégrité Référentielle** :
   - Lien optionnel vers un `Supplier` actif et/ou un `Product` du même tenant.
   - Lien optionnel vers une version de document propre (`DocumentVersion`).

### Imputation Allégation ↔ Preuve (`EvidenceLink`)

1. **Relation Qualifiée** :
   - `supports` : La pièce étaye directement et complètement l'allégation.
   - `partially_supports` : La pièce couvre un sous-ensemble du périmètre (ex. Scope 1/2 sans Scope 3).
   - `contradicts` : La pièce infirme l'allégation (ex. ACV montrant une hausse d'empreinte).
   - `not_related` : Pièce non pertinente.
   - `review_required` : Nécessite une expertise humaine approfondie.
2. **Matrice de Couverture Dynamique (`get_evidence_matrix`)** :
   - Synthétise pour une version d'analyse le taux de couverture des allégations.
   - Fournit un état consolidé : `verified`, `expired`, `missing`, `out_of_scope`.
   - Fournit des explications claires et sans jargon juridique trompeur.

## Sécurité & RBAC

- `evidence:read` : Accès en lecture au registre probatoire et à la matrice.
- `evidence:manage` : Création, modification, archivage des preuves et gestion des liaisons (protégé CSRF).
- Journalisation systématique des créations, modifications et suppressions dans `audit_events`.
