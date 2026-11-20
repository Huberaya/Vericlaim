# Registre des traitements (art. 30 RGPD)

**Établi à partir du code, pas d'une intention.** Chaque ligne cite la source qui la
rend vérifiable. Les durées ne sont pas choisies ici : elles sont **déclarées par
l'organisation cliente** (politique de rétention) et **plafonnées par son offre**.

> Ce registre doit être validé par un conseil (C19/C12). Il ne constitue pas une
> déclaration de conformité.

## 1. Traitements dont VeriClaim est responsable

| # | Finalité | Base légale (à confirmer) | Données | Personnes | Durée | Preuve dans le code |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | Fournir le service (comptes, organisations, habilitations) | Exécution du contrat (art. 6.1.b) | Nom, e-mail, mot de passe haché (scrypt), rôle, organisation | Utilisateurs clients | Tant que le compte existe, puis suppression sur demande | `app/identity/**`, `app/models/domain.py` (`users`, `memberships`, `roles`) |
| T2 | Authentifier et sécuriser l'accès | Intérêt légitime (art. 6.1.f) — sécurité du service | Identifiants de session (hachés), **empreinte SHA-256 de l'agent utilisateur** (pas l'agent en clair), horodatages. **Aucune adresse IP n'est stockée** — vérifié : aucune colonne ni lecture de `client_host`/`X-Forwarded-For` | Utilisateurs clients | Session : `AUTH_SESSION_TTL_SECONDS` ; journaux de session : **[À COMPLÉTER — décision d'exploitation]** | `app/identity/security.py` (`token_hmac`, cookies), `app/models/domain.py` (`auth_sessions`) |
| T3 | Facturer et recouvrer | Obligation légale (art. 6.1.c, comptabilité) + contrat | Coordonnées de facturation, plan, journal d'usage, factures | Contacts clients | **[À COMPLÉTER — durée comptable légale, par pays]**, aucune purge automatique aujourd'hui | `app/billing/**`, `app/models/domain.py` (`billing_subscriptions`, `billing_invoices`, `usage_events`) |
| T4 | Journaux techniques, métriques, support | Intérêt légitime (art. 6.1.f) | Identifiant de corrélation, organisation, identifiant de travail, codes d'erreur, durées | Utilisateurs clients (identifiants indirects) | **[À COMPLÉTER — durée d'exploitation des journaux]** ; les logs sont écrits sur la sortie standard, leur conservation appartient à la plateforme | `app/core/logging.py`, `app/core/metrics.py` |
| T5 | Traiter les demandes d'exercice des droits | Obligation légale (art. 12 et s.) | Identité du demandeur, texte de la demande, échéances, décision, référence de ce qui a été remis | Personnes concernées | Conservées comme preuve de la réponse ; durée à figer **[À COMPLÉTER]** | `app/privacy/rights.py`, `app/api/v1/data_rights.py`, table `data_subject_requests` |
| T6 | Sécurité de la chaîne d'audit | Obligation de sécurité (art. 32) + preuve contractuelle | Événements horodatés et chaînés (acteur, action, empreinte) | Utilisateurs clients | Selon la durée d'audit déclarée par l'organisation ; **jamais purgée automatiquement** (limite publiée) | `app/identity/service.py` (`append_audit_event`), `app/audit/service.py` |

**Aucun profilage, aucune décision automatisée produisant un effet juridique** : le
moteur produit des indices de risque explicables, et une revue humaine est journalisée
(`app/api/v1/review.py`). Il n'y a **aucun modèle d'apprentissage automatique** dans le
produit (voir `docs/legal/sous-traitants-et-transferts.md`, §3).

## 2. Traitements dont VeriClaim est sous-traitant

Pour les documents téléversés par un client et les analyses produites, **l'organisation
cliente est responsable du traitement** et VeriClaim agit sur ses instructions
(art. 28). Le périmètre exact est décrit dans `docs/legal/dpa.md`.

| # | Finalité | Données | Durée | Preuve |
| --- | --- | --- | --- | --- |
| S1 | Stocker et analyser les documents fournis par le client | Documents, textes extraits, allégations détectées, preuves, verdicts | Durée **déclarée par le client**, plafonnée par l'offre (`plan_retention`) ; purge **à la demande**, non planifiée | `app/privacy/service.py` (`build_purge_plan`, `_cap_windows_by_plan`), C20 |
| S2 | Produire les rapports signés et dossiers probatoires | Verdicts, empreintes, signatures HMAC, artefacts PDF/ZIP | Les artefacts ne sont **pas** purgés aujourd'hui (limite publiée en C22) | `app/reports/**`, `app/privacy/purge_job.py` |
| S3 | Effacement sur demande | Suppression des objets stockés et des lignes dérivées, journal d'audit conservé (tomstone) | Immédiat, sauf gel légal actif | `app/privacy/service.py` (`erase_document`), tests C20 |

## 3. Durées : ce qui est réellement appliqué

- L'organisation **déclare** des durées (`documents_retention_years`,
  `evidence_archive_retention_years`, `audit_trail_retention_years`).
- L'offre **plafonne** ces durées ; la durée appliquée est `min(déclarée, couverte)` et
  l'API publie les deux (`plan_retention`). Une durée déclarée plus longue que l'offre
  n'est pas appliquée en silence : elle est refusée ou plafonnée **visiblement**.
- **Rien n'est purgé** si aucune durée n'est déclarée : le produit ne choisit pas une
  durée à la place du client.
- La purge est **manuelle** (job exécutable) et non planifiée par le produit : tant
  qu'aucun ordonnanceur n'est câblé, une durée déclarée est une politique, pas encore
  une exécution automatique. C'est une limite, et elle est écrite ici.

## 4. Sécurité mise en œuvre (vérifiable)

| Mesure | Où | Remarque |
| --- | --- | --- |
| Cloisonnement par organisation (RLS PostgreSQL) | migrations + `set_db_request_context` | Chaque table cliente porte `organization_id` et une politique RLS |
| Mots de passe hachés (`scrypt`, paramètres auto-décrits) | `app/identity/passwords.py` | Jamais stockés en clair, jamais journalisés |
| Sessions opaques hachées + CSRF | `app/identity/security.py` | Cookie de session `HttpOnly`, CSRF `SameSite=Strict` |
| Chiffrement au repos des objets | `DOCUMENT_STORAGE_SSE_MODE` | **Obligatoire en staging/production** (démarrage refusé sinon), `aes256` ou `aws:kms` |
| Chaîne d'audit chaînée (SHA-256) | `app/identity/service.py`, `app/audit/service.py` | Vérifiable par `GET /api/v1/audit/verify` ; l'effacement ne casse pas la chaîne (C20) |
| Rapports scellés (HMAC-SHA-256) | `app/reports/signing.py` | Clé `REPORT_SIGNING_KEY` **obligatoire** hors développement ; la rotation invalide les rapports déjà émis |
| Antivirus sur téléversement | `DOCUMENT_SCANNER_MODE=clamav` | Obligatoire en staging/production |
| Journal d'audit non modifiable | aucune API de suppression d'événement | La suppression d'un document laisse un tomstone |

## 5. Ce qui manque, et qui doit être décidé

1. **Sauvegarde du stockage objet** : absente. Un sinistre sur le stockage perd les
   documents et les rapports. Manque majeur, hors périmètre des chantiers traités.
2. **Purge planifiée** : job existant, aucun ordonnanceur.
3. **Durées d'exploitation** (journaux, sessions, factures) : à figer avec l'hébergeur
   et l'expert-comptable, puis à reporter ici.
4. **Analyse d'impact (AIPD)** : à évaluer avec un DPO — le traitement porte sur des
   allégations environnementales et des documents fournisseurs, pas sur des données de
   santé ou des données sensibles au sens de l'art. 9, mais l'appréciation doit être
   faite et écrite.
