# Qualification définitive — PostgreSQL/RLS, Neon staging et Preview

**État : QUALIFIÉ POUR LE PÉRIMÈTRE CI + BASE STAGING ; E2E NAVIGATEUR NON QUALIFIÉ.**  
**Dernière vérification : 12 novembre 2026.**  
**Branche candidate :** `arena/c5488624-vericlaim`  
**SHA candidat vérifié :** `115c1863ce02496180b8b31a2c3a7ff4844ef163`  
**PR :** [#13](https://github.com/Huberaya/Vericlaim/pull/13) — ouverte, non fusionnée.

Ce document est le point de reprise de référence. Ne pas refaire cette qualification tant que les déclencheurs de requalification listés plus bas ne sont pas intervenus.

## 1. Preuves CI attachées au SHA candidat

Workflow [Continuous integration — run 43259466287](https://github.com/Huberaya/Vericlaim/actions/runs/43259466287) : **completed / success**, exécuté sur le SHA candidat ci-dessus.

Les cinq jobs sont réussis :
- Backend verification : lint, suite backend, vérification de la tête Alembic, scan des signatures de secrets et intégrité whitespace.
- PostgreSQL tenant isolation (RLS) : service PostgreSQL 16 éphémère ; migrations Alembic complètes, grants runtime, tests exécutés avec le rôle `vericlaim_app`.
- Frontend verification : installation verrouillée, audit npm, typecheck et build.
- Python dependency audit : audit du lock hashé.
- Backend container build : build sans déploiement et vérification UID non-root 10001.

Le job PostgreSQL exécute explicitement :
`tests/test_postgres_tenant_isolation.py`,
`tests/test_postgres_document_isolation.py`,
`tests/test_postgres_operational_integrity.py` et
`tests/test_postgres_purge_concurrency.py`.

Les tests RLS couvrent notamment :
- absence de visibilité sans contexte tenant ;
- lecture isolée entre deux organisations ;
- refus d'insertion dans une autre organisation ;
- update/delete inter-tenant sans effet ;
- documents, versions et jobs d'extraction isolés ;
- rôle runtime sans superuser, CREATEDB, CREATEROLE ni BYPASSRLS ;
- absence de CREATE sur le schéma public et caractère append-only des tables d'audit ;
- détection de régressions RLS (RLS désactivée, policy permissive, nouvelle table tenant non protégée).

Le service PostgreSQL de ce job est jetable et indépendant de toute donnée client. Le job ne doit jamais être redirigé vers Neon ou une base persistante.

## 2. Preuve Neon staging

Workflow [Staging database migration — run 43272249768](https://github.com/Huberaya/Vericlaim/actions/runs/43272249768) : **completed / success**, lancé sur `main` au SHA `b289652a383bfe385c531ff4fb25a5f1535f74c7`.

Étapes réussies : précontrôle de la cible/TLS/rôle migrator, migrations Alembic, grants runtime, dérivation de la révision attendue depuis le code revu, puis vérification read-only de RLS et des privilèges par `vericlaim_app`.

La tête Alembic attendue est `a7d4c9e2b510`. Le fichier `infra/neon/verify_runtime.py` est identique sur `main` et sur la branche candidate (blob SHA `de96099663fde8df255992c27b5bde287d7bdee3`). La vérification CI sur la branche candidate réexécute aussi le même vérificateur sur une base PostgreSQL jetable.

**Limite exacte :** le workflow Neon valide le schéma, les policies, le rôle runtime et les grants sur la base staging. Les scénarios A/B avec vraies lignes synthétiques sont exécutés en CI PostgreSQL jetable, pas injectés dans la base Neon persistante. Aucune donnée client ni fixture multi-tenant n'a été ajoutée à Neon.

## 3. Preuve Vercel Preview

Déploiement [Vercel Preview](https://vericlaim-4iv842mqy-baya-ibraimmayoanouedu-8164s-projects.vercel.app) :
- deployment ID : `dpl_9yB7ePSfhh4sV1J3efNBVRA5BV5S`
- état : `READY`
- branche : `arena/c5488624-vericlaim`
- SHA : `115c1863ce02496180b8b31a2c3a7ff4844ef163`

**Limite exacte :** READY prouve la réussite du build/déploiement, pas le parcours utilisateur. La Preview reste protégée par Vercel SSO ; aucun contournement ni désactivation de protection n'a été effectué. Les tests interactifs login OIDC, création d'organisation, changement d'organisation et dépôt documentaire ne sont donc pas déclarés réussis ici. Ils relèvent d'une qualification E2E distincte, après configuration d'un IdP/OIDC et des services documentaires réels.

## 4. Rejouer les contrôles sans refaire l'audit

Depuis la racine du dépôt, dans la CI :
- lancer le workflow `Continuous integration` sur le commit candidat ;
- vérifier que le job `PostgreSQL tenant isolation (RLS)` est exécuté et vert, pas ignoré ;
- vérifier les cinq jobs et le SHA exact du commit.

Pour un contrôle local PostgreSQL, depuis `backend/` avec une instance PostgreSQL **jetable et administrable uniquement** :
```bash
export VERICLAIM_TEST_POSTGRES_URL='postgresql://…'
python -m pytest -q tests/test_postgres_tenant_isolation.py \
  tests/test_postgres_document_isolation.py \
  tests/test_postgres_operational_integrity.py \
  tests/test_postgres_purge_concurrency.py
```
Sans `VERICLAIM_TEST_POSTGRES_URL`, ces tests sont ignorés : une suite verte SQLite seule ne vaut jamais preuve RLS.

Pour la base staging, utiliser exclusivement le workflow manuel `Staging database migration` selon sa procédure et ses protections. Le mode APPLY écrit des migrations : ne pas le relancer pour un simple contrôle de routine. Le contrôle read-only runtime est :
```bash
python infra/neon/verify_runtime.py --expected-revision a7d4c9e2b510
```
Il exige `DATABASE_URL_APP` vers la base staging et vérifie TLS, le rôle runtime, la révision, les policies RLS et les privilèges. Ne jamais afficher ni copier une URL de connexion dans les logs ou les rapports.

## 5. Déclencheurs obligatoires de requalification

Rejouer la qualification concernée uniquement si l'un de ces éléments change :
1. une migration Alembic, une table portant `organization_id`, une policy RLS ou la fonction `vericlaim_current_organization_id()` ;
2. les rôles PostgreSQL, memberships de rôles, grants/default privileges ou scripts `infra/neon/0*` ;
3. le code de contexte tenant, de session/organisation, ou les services d'accès aux documents/analyses ;
4. les tests PostgreSQL cités, leur fixture, leur rôle DB ou le job CI qui les exécute ;
5. le lock de dépendances PostgreSQL, la version majeure de PostgreSQL ou le workflow de migration ;
6. la configuration de la base staging ou le déploiement Preview.

Pour une modification purement documentaire ou frontend sans impact sur ces surfaces, ne pas relancer les migrations Neon ni l'ensemble de la qualification RLS ; exécuter seulement les checks CI applicables au changement.

## 6. Verdict de clôture

- **Code RLS et isolation multi-tenant sur PostgreSQL jetable : VALIDÉ** au SHA candidat et par CI distante.
- **Migration et privilèges runtime Neon staging : VALIDÉS** par le workflow dédié.
- **Déploiement Preview : READY, mais parcours utilisateur E2E non validé** en raison de la protection SSO et de l'absence de preuve IdP/services réels.
- **Production : NON QUALIFIÉE et non modifiée.**
- **PR #13 : ouverte, non fusionnée.**

La qualification technique PostgreSQL/RLS + Neon staging est clôturée pour les versions et SHA référencés. Une reprise ultérieure doit consulter ce document et ne rouvrir le chantier que si un déclencheur ci-dessus est constaté.