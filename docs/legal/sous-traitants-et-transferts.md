# Sous-traitants, localisation et transferts

**But de ce document** : pouvoir répondre à une question de DPO ou d'acheteur sans
improviser. Chaque ligne est soit **vérifiée dans le code**, soit marquée comme
dépendant d'un choix d'exploitation non encore fait.

## 1. Ce que le produit fait par lui-même, et où

| Composant | Technologie | Où tourne-t-il | Ce qui en sort |
| --- | --- | --- | --- |
| API | FastAPI / Python (`backend/`) | Conteneur `backend` (Docker Compose, ou plateforme de l'exploitant) | Réponses HTTP vers le navigateur du client |
| Base de données | PostgreSQL | Conteneur `postgres` ou service managé **[À COMPLÉTER — région]** | Rien : aucun appel sortant |
| Stockage objet | S3 ou MinIO (`boto3`) | Conteneur `minio` (local) ou service S3 **[À COMPLÉTER — région]** | Les documents et rapports du client |
| Analyse antivirus | ClamAV | Conteneur `clamav` | Rien |
| OCR | Tesseract, **exécuté localement dans le worker** (`pytesseract`) | Conteneur `document-worker` | Rien : les images ne quittent pas l'instance |
| Analyse réglementaire | Moteur déterministe (règles du Rule Book) | Conteneur `analysis-worker` | Rien |
| Génération des rapports | Workers dédiés (C22) | Conteneur `report-worker` | Écrit dans le stockage objet |
| Frontend | Next.js | Conteneur `frontend` | Rendu HTML/JS vers le navigateur |

## 2. Ce qui sort réellement de l'instance

| Flux | Destinataire | Contenu | État |
| --- | --- | --- | --- |
| E-mails transactionnels (vérification, réinitialisation, invitation, alertes) | Relais SMTP **[À COMPLÉTER — fournisseur et pays]** | Adresse du destinataire, objet, lien à jeton **à usage unique** | Implémenté, file d'attente en base ; **envoi réel non éprouvé** (aucun relais configuré en test) |
| Paiement et abonnement | Prestataire de paiement **[À COMPLÉTER — Stripe ou équivalent UE]** | Identité de l'organisation, plan, moyen de paiement (jamais stocké chez nous) | Adaptateur écrit, **jamais exécuté avec de vraies clés** |
| Journaux techniques | Plateforme d'exploitation **[À COMPLÉTER]** | Identifiants d'organisation, codes d'erreur, durées — sur la sortie standard du conteneur | Implémenté |
| Mesure d'audience, publicité, CDN, polices distantes | **aucun** | — | **Vérifié** : aucun script tiers dans le frontend, aucune police distante |

## 3. « Aucun transfert de document à un tiers IA » — ce qui le rend vérifiable

L'audit pré-lancement relevait ce point comme un avantage à documenter. Il est ici
factuel, et vérifiable par quiconque :

1. **Aucune dépendance d'inférence** dans `backend/requirements.txt` (ni client OpenAI,
   ni Anthropic, ni Google, ni Hugging Face, ni modèle local) ;
2. **aucun appel réseau sortant** dans le pipeline documentaire : les seuls clients HTTP
   du backend sont `boto3` (stockage **du** client) et le client de paiement ;
   l'OCR est exécuté localement par Tesseract ;
3. donc le texte extrait d'un document, les allégations détectées et les verdicts **ne
   quittent jamais l'instance**.

Vérification reproductible :

```bash
grep -rn "openai\|anthropic\|google.generativeai\|transformers\|httpx\|requests" backend/app/ backend/requirements.txt
```

Cette propriété est un argument contractuel : elle doit être écrite dans le DPA comme
un engagement, avec la procédure de vérification — et elle **devient fausse** le jour où
un service d'IA est ajouté. Deux tests la surveillent, et échouent si elle cesse d'être
vraie :

* `test_no_inference_dependency_is_installed` — interdit toute dépendance d'inférence
  dans `requirements.txt` ;
* `test_the_document_pipeline_makes_no_outbound_call` — interdit tout appel réseau
  sortant dans les paquets qui traitent les documents (`engine`, `analyses`,
  `documents`, `reports`, `privacy`).

Une clause contractuelle que rien ne vérifie est une clause qui périme sans prévenir.

## 4. Transferts hors Union européenne

| Cas | Statut |
| --- | --- |
| Déploiement avec PostgreSQL, MinIO, ClamAV et workers auto-hébergés dans l'UE | **Aucun transfert** |
| Stockage objet managé hors UE (S3 hors région européenne, R2, etc.) | Transfert — nécessiterait clauses contractuelles types et analyse d'impact |
| Relais e-mail hors UE | Transfert des adresses et objets de messages |
| Prestataire de paiement | Dépend du prestataire et de son établissement |

**Rien de tout cela n'est configuré aujourd'hui** : l'environnement de référence est un
Compose local, et la région par défaut déclarée est `eu` (`Organization.data_region`,
défaut `"eu"` ; `storage_region` déclaré par l'organisation dans sa politique de
rétention). Toute mise en production doit remplir ce tableau **avant** de publier une
politique de confidentialité qui affirmerait une localisation.

## 5. Sous-traitants ultérieurs (art. 28.2)

Le DPA (`docs/legal/dpa.md`) doit prévoir l'information préalable du client en cas de
changement de sous-traitant, avec droit d'opposition. Ce document est la liste annexée.
