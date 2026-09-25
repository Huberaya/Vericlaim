# Chantier 1 — Architecture et modèle de données

**Statut :** fondation technique livrée, sans activation des parcours métier ni de l’authentification au moment de ce chantier.
**Décision de périmètre :** conserver le moteur réglementaire déterministe existant et préparer son intégration dans un SaaS multi-tenant sans modifier ses verdicts.

> Ce document décrit l’état de sortie du Chantier 1. Le contrôle de tenant, le RBAC et les policies RLS ont ensuite été livrés au Chantier 2 : voir [`ch02-identity-tenancy.md`](ch02-identity-tenancy.md).

## 1. Décisions d’architecture (ADR synthétiques)

### ADR-001 — Monolithe modulaire, API-first

Le MVP reste un monorepo **Next.js + FastAPI + PostgreSQL**. Le backend est séparé en modules de domaine (`identity`, `catalog`, `documents`, `analysis`, `regulatory`, `audit`) et en workers asynchrones à venir. Cette solution réduit le coût d’exploitation tout en préservant une frontière API stable pour ERP/PIM/PLM.

### ADR-002 — PostgreSQL est la source de vérité ; aucune bascule silencieuse

Un échec PostgreSQL, de credentials ou de migration fait échouer le démarrage/traitement de façon visible. `DATABASE_URL` est obligatoire hors `development`/`test` ; l’ancien fallback silencieux vers SQLite est supprimé. SQLite demeure autorisé explicitement pour les tests et le développement isolé.

### ADR-003 — Alembic est la source de vérité du schéma

- `AUTO_CREATE_SCHEMA=true` n’est permis que pour `development`/`test`.
- Staging et production exécutent `alembic upgrade head` dans un job de migration unique avant le démarrage des réplicas API.
- Lorsque `AUTO_CREATE_SCHEMA=false`, le backend vérifie au démarrage que `alembic_version` est à la tête packagée ; il échoue de façon visible sinon.
- La révision initiale `f3efc9c81c8d` représente le schéma complet de base, y compris `audit_records` pour ne pas casser le prototype actuel.
- Aucune migration destructive n’est lancée automatiquement sur une base contenant des données client.

### ADR-004 — Tenancy explicite dès le modèle

Chaque agrégat détenu par un client porte directement `organization_id`, y compris les versions et sous-ressources (`document_versions`, `document_segments`, `analysis_versions`, `claims`). Cela permettra des requêtes filtrées, des index de sécurité et des politiques PostgreSQL RLS au Chantier 2.

Les objets globaux restent hors tenant : `users`, `roles`, `regulations` et les règles réglementaires globales. `rules.organization_id IS NULL` désigne une règle administrée globalement ; une valeur non nulle désignera une politique interne d’organisation.

### ADR-005 — Version immuable des artefacts probatoires

- `Document` est un conteneur logique ; `DocumentVersion` référence le binaire immuable et son SHA-256.
- `Analysis` est un dossier logique ; `AnalysisVersion` référence le manifest d’entrée, la version moteur, la version du Rule Book et le résultat hashé.
- `Rule` est un identifiant stable ; `RuleVersion` porte le texte/passage/source, les dates et la logique de décision versionnée.
- `Report` est versionné par `AnalysisVersion`.

Une modification n’écrase donc pas un résultat antérieur. Le stockage objet sécurisé et la quarantaine ont été livrés au Chantier 3 ; l’extraction durable, les hashes de transcript et les segments citables sont livrés au Chantier 4 (voir [`ch04-durable-document-extraction.md`](ch04-durable-document-extraction.md)).

### ADR-006 — IA assistive, décision déterministe

Le modèle prévoit `ClaimSource` (`deterministic`, `ai_candidate`, `human`) et des confidence scores, mais aucune IA ne délivre un verdict juridique. Toute future sortie IA devra être reliée à des segments, sources et une validation humaine.

## 2. Bounded contexts et responsabilité des tables

| Contexte | Tables | Responsabilité |
|---|---|---|
| Identity / tenancy | `organizations`, `users`, `roles`, `memberships` | Identité globale, appartenance et rôles. Aucun mécanisme de login n’est encore exposé. |
| Catalogue achats | `suppliers`, `products` | Fournisseur, référence produit, catégories et métadonnées organisationnelles. |
| Documents | `documents`, `document_versions`, `document_extraction_jobs`, `document_segments` | Identité logique, versions immuables, queue d’extraction durable et provenance page/offset/boîte de texte. |
| Preuves | `certificates`, `evidence`, `evidence_links`, `evidence_requests` | Pièces, validité, périmètre et relation explicite claim → preuve. |
| Analyses | `analyses`, `analysis_documents`, `analysis_versions`, `claims`, `risks`, `recommendations`, `validations`, `reports` | Dossier d’analyse, snapshots, résultats, score, validation humaine et export. |
| Référentiel | `regulations`, `rules`, `rule_versions` | Sources officielles, juridiction, force, dates et logique versionnée. |
| Audit | `audit_events`, `audit_records` | Journal métier append-only futur et compatibilité du hash chain du prototype existant. |

Le diagramme relationnel est disponible dans [`erd.mmd`](erd.mmd).

## 3. Invariants à respecter dans les services futurs

Ces invariants sont documentés et testés au niveau structurel ; leur enforcement transactionnel/RLS arrive au Chantier 2.

1. Toute requête sur une table tenant-owned doit toujours recevoir un `organization_id` issu du contexte authentifié, jamais du body client.
2. Les relations parent/enfant doivent rester dans la même organisation : par exemple `DocumentVersion.organization_id == Document.organization_id`, `Claim.organization_id == AnalysisVersion.organization_id` et `EvidenceLink.organization_id` égale les deux côtés de la relation.
3. Une version finalisée de document, analyse, règle ou rapport est immuable. Une correction crée une nouvelle version.
4. Le hash de l’original, de l’extraction, du manifest d’entrée et du rapport est conservé avant toute conclusion à destination de l’utilisateur.
5. Les résultats de modèles IA sont des candidats (`ai_candidate`) tant qu’un moteur déterministe et/ou un humain ne les a pas confirmés.
6. Les règles réglementaires ne deviennent actives qu’après source officielle, date de vérification et publication d’une `RuleVersion`.
7. `audit_events` doit être écrit de manière append-only et hash-chaînée **par organisation**. La sérialisation de l’append et la signature externe sont des travaux du Chantier 11.

## 4. Modèle des versions et de la traçabilité

```text
Document ──< DocumentVersion ──< DocumentSegment
                                  │
Analysis ──< AnalysisVersion ──< Claim ──< EvidenceLink >── Evidence
     │                 │              │                         │
     └──< AnalysisDocument             ├──< Risk                └── Certificate
                                       ├──< Recommendation
                                       └──< Validation

Rule ──< RuleVersion ──> Regulation
AnalysisVersion ──< Report
Organization ──< AuditEvent
```

`AuditRecord` reste volontairement séparé : il assure la compatibilité de l’endpoint `/api/v1/engine/evaluate` existant et n’est pas considéré comme le journal SaaS cible. Aucun nouveau module métier ne doit s’y appuyer.

## 5. Stratégie de migration

### Base neuve

```bash
cd backend
DATABASE_URL='postgresql+psycopg://…' AUTO_CREATE_SCHEMA=false alembic upgrade head
alembic current
```

### Instance prototype existante

Il n’existe pas de migration historique avant le Chantier 1. Ne pas exécuter aveuglément la baseline sur une instance ayant déjà une table `audit_records` :

1. exporter/sauvegarder la base et valider la restauration ;
2. comparer le schéma effectif à `audit_records` de la baseline ;
3. soit recréer une base non cliente et migrer, soit produire une migration de bridge validée ;
4. seulement ensuite `alembic stamp f3efc9c81c8d` si et uniquement si le schéma est démontré identique ;
5. documenter l’opération dans le journal de déploiement.

Pour l’environnement Compose local, une seule instance backend exécute la migration avant Uvicorn. Cette pratique ne doit pas être réutilisée telle quelle dans un déploiement multi-réplicas.

## 6. Architecture opérationnelle cible

```text
Navigateur Next.js
      │  OIDC / SSO + organisation active (Chantier 2)
      ▼
FastAPI API v1 ────────────────────── PostgreSQL + RLS (Chantier 2)
      │                                      │
      │                                      ├─ modèle métier/versionné
      │                                      └─ audit append-only
      ├─ stockage objet chiffré / AV (Chantier 3)
      └─ queue + workers isolés (Chantier 4)
             ├─ extraction / OCR / segments citables (pas de tables structurées)
             ├─ moteur déterministe
             ├─ AIProvider + RAG sourcé (Chantiers 5/7)
             └─ rapport PDF (Chantier 10)
```

## 7. Compatibilité avec le prototype actuel

| Comportement actuel | Statut après Chantier 1 |
|---|---|
| `POST /api/v1/engine/evaluate` | Inchangé ; son test de non-régression reste obligatoire. |
| `GET /api/v1/engine/rules` | Inchangé. |
| `audit_records` hash-chaînés | Conservés dans la baseline. |
| Création de tables locale/test | Disponible seulement lorsque `AUTO_CREATE_SCHEMA=true`. |
| Fallback automatique SQLite | Supprimé ; erreur visible à corriger par configuration/migration. |
| Authentification / RBAC | Toujours absents : ne pas exposer à des clients. |

## 8. API cible (contrat, non implémenté)

Les futurs endpoints seront sous `/api/v1` et porteront un contexte organisationnel issu de l’identité, non de paramètres libres :

- `POST /documents`, `GET /documents`, `GET /documents/{id}`, `POST /documents/{id}/versions`
- `POST /analyses`, `GET /analyses/{id}`, `GET /analyses/{id}/versions/{version}`
- `GET/POST /suppliers`, `GET/POST /products`
- `GET /claims/{id}`, `POST /evidence`, `POST /evidence-requests`
- `GET /regulations`, `GET /rules`, `POST /validations`, `POST /reports`

Le contrat détaillé et la compatibilité avec le moteur actuel sont décrits dans [`../api/target-v1.md`](../api/target-v1.md).

## 9. Hors périmètre assumé du Chantier 1

- login/OIDC/SSO, session, RBAC appliqué, RLS et API keys ;
- endpoint CRUD métier ;
- upload durable, antivirus, chiffrement objet et OCR asynchrone ;
- ingestion de corpus réglementaire/RAG et évolution des règles actuelles ;
- UI Dashboard/Documents/Produits/Fournisseurs ;
- génération PDF et messagerie fournisseur.

Ces éléments ne sont pas simulés : seules les structures nécessaires à leur implémentation ultérieure ont été ajoutées.
