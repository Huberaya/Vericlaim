# Accord de sous-traitance (art. 28 RGPD)

> **MODÈLE NON PUBLIÉ.** À valider par un conseil : c'est le document contractuel qui
> engage le plus lourdement. Il est rédigé ici de façon à refléter **ce que le produit
> fait réellement**, afin qu'il ne promette pas une mesure technique inexistante.

## 1. Parties et qualité

- **Responsable de traitement** : le client (l'organisation abonnée).
- **Sous-traitant** : **[À COMPLÉTER — entité VeriClaim]**.
- **Objet** : hébergement et traitement des documents et analyses fournis par le client.
- **Durée** : celle de l'abonnement, plus les obligations de restitution/effacement.
- **Nature et finalité** : stockage, extraction de texte, détection d'allégations,
  confrontation aux preuves, notation, production de rapports signés.
- **Catégories de données** : documents professionnels et données personnelles qu'ils
  contiennent (par ex. signatures, coordonnées, noms de produits/manufacturiers).
  **Aucune donnée de l'art. 9 n'est recherchée** ; si le client en téléverse, la
  restriction de traitement lui incombe.

## 2. Instructions

Le sous-traitant traite les données **uniquement sur instructions documentées** du
responsable, y compris pour les transferts hors UE (aucun n'est réalisé par défaut —
voir `sous-traitants-et-transferts.md`). Il informe sans délai si une instruction lui
paraît violer le RGPD.

## 3. Mesures de sécurité (annexe technique, **vérifiable**)

| Mesure | Mise en œuvre | Vérification |
| --- | --- | --- |
| cloisonnement par client | politiques RLS PostgreSQL forcées, contexte de requête par organisation | migrations + tests de tenancy |
| chiffrement des données au repos | SSE activé obligatoirement hors développement (`aes256` ou `aws:kms`) ; démarrage refusé sinon | `app/core/config.py` (validation au démarrage) |
| mots de passe et sessions | scrypt ; jetons de session opaques stockés hachés (HMAC-SHA-256) | `app/identity/passwords.py`, `security.py` |
| anti-malware | ClamAV obligatoire hors développement, avant toute promotion d'un téléversement | `DOCUMENT_SCANNER_MODE`, tests C4 |
| intégrité | journal d'audit chaîné (chaque événement lie le précédent), vérifiable | `GET /api/v1/audit/verify` |
| scellement des livrables | signature HMAC-SHA-256 + empreinte du fichier, vérifiables sans compte | `GET /api/v1/reports/verify/{référence}` |
| **aucun tiers d'IA** | aucune dépendance d'inférence ; OCR et analyse exécutés localement | deux tests le vérifient |
| **sauvegarde** | ⚠️ **aucune sauvegarde du stockage objet n'existe** | à ne pas promettre dans le DPA en l'état |

## 4. Sous-traitants ultérieurs

Liste annexée (`sous-traitants-et-transferts.md`). Le client est informé **avant** tout
changement, avec droit d'opposition. **[À COMPLÉTER — délai de préavis.]**

## 5. Assistance au responsable

Le sous-traitant assiste le client pour : répondre aux demandes d'exercice des droits
(la procédure et ses limites sont publiées dans `procedure-droits-des-personnes.md`),
notifier une violation de données **[À COMPLÉTER — délai, canal]**, et réaliser les
analyses d'impact.

## 6. Effacement et restitution en fin de contrat

- **Export** : disponible à tout moment par le client (`GET /api/v1/privacy/export`),
  sans intervention de l'éditeur.
- **Effacement** : outillé (`POST /api/v1/privacy/erasures` pour un document, purge
  selon la politique déclarée). ⚠️ La purge n'est **pas planifiée** à ce jour : un
  effacement de fin de contrat est une opération **manuelle**, à organiser dans le
  délai convenu **[À COMPLÉTER]**.
- **Ce qui subsiste après effacement** : le journal d'audit (tomstones : identifiants et
  empreintes, sans le contenu) et, le cas échéant, les artefacts de rapports — ce
  dernier point est une limite publiée (C22) qui doit être traitée avant un engagement
  de « suppression totale ».

## 7. Audit

Le client peut obtenir : la documentation de sécurité (`docs/OPERATIONS_AND_SECURITY.md`),
les preuves de test des mesures ci-dessus, et **[À COMPLÉTER — modalités d'audit sur
site ou par questionnaire, fréquence, coût]**.
