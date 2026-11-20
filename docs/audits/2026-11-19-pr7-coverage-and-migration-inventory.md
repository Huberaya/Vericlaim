# Récupération de la PR #7 — couverture et inventaire des migrations

Date : 2026-11-19. Branche : `arena/2d0b984d-vericlaim`. Base : `main` à
`6f2cade`. PR concernée : #7 (`3f56ee0`), jamais fusionnée.

Ce document répond à deux questions séparément, parce qu'elles se confondent
facilement : **qu'est-ce qui a été récupéré ?** et **qu'est-ce que cela change
dans la base ?**

---

## 1. Ce qui a été récupéré, domaine par domaine

Le décompte ci-dessous sort du diff avec `main`, pas d'une liste d'intentions :

```bash
git diff --name-only origin/main..HEAD | wc -l          # 208 fichiers
git diff --name-only origin/main..HEAD -- backend/app  # moteur, coffre, rapports…
```

| Domaine | Fichiers | Ce que la branche apporte | Preuve exécutée |
| --- | --- | --- | --- |
| Moteur de détection, confiance, couverture | 8 (`backend/app/engine`) | détection par règles, score de confiance, matrice de couverture probatoire | `test_evidence_coverage.py` (15 tests, 1 046 lignes) |
| Verdicts réglementaires | 12 (`models`, `api`, `analyses`) | verdict persisté, référentiel versionné, traçabilité de la règle appliquée | `test_analysis_regulatory_verdicts.py` (11) |
| Coffre documentaire | 5 (`documents`) | extraction, antivirus fail-closed, rétention par organisation | `test_secure_documents.py`, `test_document_*.py` (61) |
| Analyses immuables | 2 | rejeu à l'identique, verdict figé au moment de l'évaluation | `test_persistent_analyses.py`, `test_public_claims.py` |
| Rapports signés + file | 7 (`reports`) | signature Ed25519, file de génération asynchrone, vérification publique | `test_pdf_reporting.py`, `test_report_integrity.py`, `test_report_queue_and_scalability.py` (42) |
| Droits RGPD | 3 (`privacy`) | demande de droits, effacement, registre | `test_data_subject_rights.py`, `test_privacy_lifecycle.py` |
| Identité self-service | 5 (`identity`) | inscription, vérification d'adresse, réinitialisation, verrouillage | `test_self_service_identity.py` |
| Support et observabilité | 3 (`support`, `core`) | demandes de support, métriques, règles d'alerte | `test_support_*.py`, `test_a09_metrics_and_alerts.py` |
| Abonnements, quotas, journal d'usage | 7 (`billing`) | plans, quotas par métrique, idempotence des événements, factures | `test_billing_*.py` |
| Revue humaine, pages publiques | 17 (`api`, `frontend/src`) | validations, demandes de preuve, pages publiques et vérification de rapport | `test_validation_*.py`, build frontend 12 pages |
| Documentation et preuves d'audit | 21 (`docs`) | documents légaux, runbooks, architecture | — |

**Aucune fonctionnalité métier de la PR #7 n'a été écartée sans justification.**
Les seuls éléments retirés sont des doublons de fusion (deux blocs identiques dans
`core/metrics.py`, `get_db` importé deux fois, `read_bytes` déclaré deux fois) et
du code mort (blocs de rendu synchrone remplacés par la file C22, quatre symboles
utilisés sans import).

---

## 2. Inventaire des migrations

La mission annonçait huit migrations. Il y en a **douze** apportées par cette
branche, et **vingt et une** au total dans la chaîne, dont la tête est
`c5f1a8e37b910` (ajoutée en mission 10 : séparation entre accès locataire et
accès système pour l'identité). L'écart n'est pas cosmétique : deux migrations supplémentaires
sont des corrections de parité de schéma et de contraintes, découvertes en
comparant une base créée par `create_all` à une base migrée.

### Chaîne complète (de la racine à la tête)

| # | Révision | Fichier | Origine |
| --- | --- | --- | --- |
| 1 | `7b3b4c985738` | `identity_and_tenant_security` | `main` |
| 2 | `a4f6b2d8e901` | `seed_system_roles` | `main` |
| 3 | `c91d2e7f4a3` | `secure_document_ingestion` | `main` |
| 4 | `e17c4f5a9b02` | `durable_document_extraction` | `main` |
| 5 | `f6a2d9b41c07` | `durable_analysis_claim_detection` | `main` |
| 6 | `d3c8a6e1b409` | `tenant_catalog_idempotency` | `main` |
| 7 | `e8f9a1b2c3d4` | `enterprise_api_keys_and_legal_holds` | `main` |
| 8 | `a7d4c9e2b510` | `document_upload_purge_ledger` | `main` |
| 9 | `b2c7e4d9a310` | `analysis_regulatory_verdicts` | **branche** |
| 10 | `c8d1f6a02e57` | `report_probative_signature` | **branche** |
| 11 | `d41a7c05b93f` | `organization_retention_policy` | **branche** |
| 12 | `e52b7f1a9c34` | `certification_claim_type` | **branche** |
| 13 | `f63c9a1b7d20` | `self_service_identity` | **branche** |
| 14 | `b7d2e5a91c04` | `billing_plans_quotas_and_invoices` | **branche** |
| 15 | `d4a7c1f30b52` | `report_generation_queue` | **branche** |
| 16 | `e91c4b7a25d0` | `data_subject_rights_requests` | **branche** |
| 17 | `c4d8b1e60f27` | `schema_parity_api_keys_and_legal_holds` | **branche** |
| 18 | `f7b3e21c8a90` | `support_requests` | **branche** |
| 19 | `a1b9c7e34d52` | `rls_identity_and_billing_tables` | **branche** |

Chaîne strictement linéaire, une seule tête. Vérifié par
`scripts/ci/check_migration_discipline.py` et par
`test_postgres_tenant_isolation.py::test_migrations_reach_the_expected_head`.

### Ce que chaque migration de la branche change réellement

| Révision | Tables créées | Colonnes ajoutées / autres changements |
| --- | --- | --- |
| `b2c7e4d9a310` | `analysis_verdicts` | 2 colonnes sur `claims` |
| `c8d1f6a02e57` | — | 7 colonnes sur `reports` (référence de vérification, signature, empreinte, versions de référentiel et de moteur) |
| `d41a7c05b93f` | `organization_retention_policies` | — |
| `e52b7f1a9c34` | — | contrainte `CHECK` sur `analysis_verdicts.claim_type` (ajout du type « certification ») |
| `f63c9a1b7d20` | `identity_tokens`, `email_messages`, `password_login_attempts` | 3 colonnes sur `users` |
| `b7d2e5a91c04` | `billing_subscriptions`, `billing_usage_events`, `billing_provider_events`, `billing_invoices`, `billing_checkout_sessions` | — |
| `d4a7c1f30b52` | `report_jobs` | 2 colonnes sur `reports` |
| `e91c4b7a25d0` | `data_subject_requests` | — |
| `c4d8b1e60f27` | — | 7 colonnes sur `api_keys` et `legal_holds` (parité entre base migrée et base créée par `create_all`) |
| `f7b3e21c8a90` | `support_requests` | — |
| `a1b9c7e34d52` | — | `ENABLE` + `FORCE ROW LEVEL SECURITY` et une politique sur `identity_tokens`, `email_messages`, `billing_subscriptions`, `billing_provider_events`, `billing_checkout_sessions` |

### Compatibilité

* **Aucune migration de `main` n'a été modifiée.** Les onze révisions de la
  branche sont ajoutées en queue ; l'historique existant est intact.
* **Aucune migration n'a été appliquée à une base réelle.** `alembic_version`
  n'a pas été touché, ni en local, ni sur Neon.
* Les migrations sont écrites pour PostgreSQL **et** SQLite : `e52b7f1a9c34` et
  `c4d8b1e60f27` passent par `batch_alter_table` parce que SQLite ne sait pas
  supprimer une contrainte en place ; `a1b9c7e34d52` sort immédiatement si le
  dialecte n'est pas PostgreSQL, la RLS n'existant pas ailleurs.
* `c4d8b1e60f27` existe précisément parce que `create_all` et la chaîne migrée
  divergeaient : une base créée par les modèles avait des colonnes qu'une base
  migrée n'avait pas. La migration comble l'écart dans le sens « base migrée →
  modèles », qui est le sens sûr.

### Risque de migration

Aucune migration destructrice : pas de `DROP COLUMN`, pas de `DROP TABLE`, pas de
`NOT NULL` ajouté sur une colonne existante peuplée, pas de réécriture de type.
Les ajouts de colonnes sont nullables ou portent une valeur par défaut. Le
`downgrade` est écrit pour chaque révision, y compris la suppression des
politiques RLS.

---

## 3. Ce qui n'a pas pu être prouvé ici

| Élément | Pourquoi | Ce qu'il faudrait |
| --- | --- | --- |
| Application des migrations sur Neon | Interdiction d'intervention sur la base de production ; aucune base de ce type accessible ici | Une fenêtre de maintenance et une sauvegarde vérifiée |
| Stockage S3 réel | Services externes bloqués | Un compartiment de test avec credentials dédiés |
| ClamAV réel | Idem | Un démon accessible depuis l'environnement de test |
| Planification des sauvegardes | Aucun ordonnanceur ne déclenche de sauvegarde : les `crons` déclarés le 19/11 concernent les workers, en cadence quotidienne | Un ordonnanceur dédié, ou un plan supérieur |
| Déploiement Vercel | **Résolu le 19/11** : l'échec venait d'un `crons` en `* * * * *`, refusé par Hobby au déploiement. Déploiement `success` au SHA final ; Preview READY | Reste à l'ouvrir depuis un accès autorisé (TLS bloqué ici) |
| Validation juridique | Aucun juriste n'a relu | Une revue externe |
| Encaissement réel | Aucun paiement n'a été fait ni simulé | Un compte de test chez le prestataire |

Ces éléments sont des **prérequis de mise en service**, pas des bloqueurs de
fusion : aucun ne remet en cause l'intégrité du code livré, et chacun est
aujourd'hui dit explicitement plutôt que supposé acquis.
