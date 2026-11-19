# Guide Utilisateur — Pack Pilote VeriClaim (B2B SaaS)

Bienvenue dans le programme pilote de **VeriClaim**, la plateforme d'intelligence réglementaire et de pré-audit des allégations environnementales.

Ce guide accompagne les directions **Achats, RSE, Juridique et Marketing** tout au long du cycle d'évaluation de la conformité (Loi AGEC, Directive EmpCo 2024/825, Normes ISO 14021/14044).

---

## 1. Démarrage Rapide : Les 5 Étapes Clés

```text
[1. Importer le Catalogue] ➔ [2. Déposer les Documents] ➔ [3. Détecter & Rapprocher] ➔ [4. Arbitrer & Valider] ➔ [5. Générer le Rapport]
```

### Étape 1 : Import du Catalogue Fournisseurs & Produits
1. Dans le **Tableau de Bord Pilote**, cliquez sur **« 📥 Import Catalogue (Batch) »**.
2. Chargez vos données au format CSV (ou cliquez sur *« Charger un exemple de lot pilote »*).
3. Validez l'importation : les fournisseurs et produits sont immédiatement créés et isolés au sein de votre organisation.

### Étape 2 : Dépôt Sécurisé des Pièces & Emballages
1. Accédez à la section **Pièces Fournisseurs (Coffre-fort)**.
2. Déposez vos supports marketing, packagings ou fiches techniques (PDF, TXT, PNG, JPG).
3. Le fichier est automatiquement scanné contre les logiciels malveillants (antivirus ClamAV) et extrait durablement par le worker.

### Étape 3 : Détection & Rapprochement Probatoire
1. Cliquez sur **« Détecter les allégations »** pour lancer l'analyse déterministe.
2. Ouvrez la **« 📊 Matrice Allégation ↔ Preuve »** pour associer les pièces probatoires de votre Registre (bilans ACV, écolabels, attestations Citeo).

### Étape 4 : Arbitrage Humain (*Human-in-the-Loop*)
1. Ouvrez le panneau **« ⚖️ Arbitrage & Validations »**.
2. Pour chaque allégation détectée :
   - Cliquez sur **« Valider »** si la pièce probatoire est complète et conforme.
   - Cliquez sur **« Contester »** en cas de manquement réglementaire ou risque de greenwashing.
   - Cliquez sur **« ✉ Demander preuve »** pour notifier formellement le fournisseur avec un modèle pré-rédigé.

### Étape 5 : Rapport Exécutif de Pré-Audit & Export
1. Dans le **Tableau de Bord Pilote**, cliquez sur **« 📄 Rapport Pré-Audit Exécutif »**.
2. Consultez la synthèse des constats, la signature cryptographique SHA-256 et le plan d'action de remédiation priorisé.
3. Exportez le rapport en JSON ou imprimez-le en PDF pour vos revues de direction.

---

## 2. Aide-Mémoire Réglementaire

| Type d'allégation | Règle applicable | Statut | Preuve exigée |
|---|---|---|---|
| **« Biodégradable »** | Art. L. 541-9-1 & R. 541-230 Code Env. | **Interdiction stricte** (Loi AGEC) | Aucune dérogation possible sur emballages neufs. |
| **« Respectueux de l'environnement »** | Art. L. 541-9-1 & Directive 2024/825 | **Interdiction / Examen strict** | Écolabel officiel pertinent (Ecolabel UE / Type I). |
| **« Neutre en carbone » (par compensation)** | Directive 2024/825 & Art. L. 229-68 | **Interdiction européenne** (27 sept 2026) | Trajectoire de réduction Scope 1/2/3 avant compensation. |
| **« 100% Recyclable »** | Art. R. 541-228 VI & ISO 14021 | **Preuve de filière requise** | Attestation Citeo / Léko ou filière industrielle effective. |
| **Chiffres d'impact (ex. « -30% CO₂ »)** | Art. L. 121-2 Code Conso & ISO 14044 | **Justification obligatoire** | Rapport ACV ISO 14044 avec unité fonctionnelle définie. |

---

## 3. Support & Assistance Pilote

- **Support Technique & Pilotage :** support@vericlaim.ai
- **Délégué à la Protection des Données (DPO) :** dpo@vericlaim.ai
- **Hébergement & Sécurité :** Région Union Européenne (Paris / Francfort), chiffrement AES-256 & TLS 1.3.
