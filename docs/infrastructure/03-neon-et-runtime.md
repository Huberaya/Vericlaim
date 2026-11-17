# 03 — Neon et accès runtime

**Date :** 12 novembre 2026
**SHA testé :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`
**Responsabilité opérationnelle Neon : assistant ChatGPT.**
**Statut Neon direct : NON VÉRIFIÉ ICI — `api.neon.tech` injoignable depuis mon environnement.**

## 1. Ce que je peux prouver, et comment

Je ne peux pas interroger Neon. En revanche, je peux faire tourner **le même
dispositif** que Neon : les scripts de rôles revus (`infra/neon/01_bootstrap_roles.sql`,
`infra/neon/02_after_migration_grants.sql`), appliqués **verbatim** par un vrai
`psql`, sur un vrai **PostgreSQL 16.2**, migré par la vraie chaîne Alembic.

C'est ce que fait `tools/recette/stack.py` à chaque exécution. Cela prouve les
droits, les policies et l'isolation ; cela ne prouve **pas** la configuration du
projet Neon lui-même (région, TLS, réseau, PITR). La distinction est reprise dans
le verdict.

## 2. Provisionnement observé

```
PostgreSQL 16.2 (serveur réel, socket unix, base jetable par exécution)
01_bootstrap_roles.sql   → appliqué par psql, puis post-condition re-vérifiée en SQL
alembic upgrade head     → exécuté en tant que vericlaim_migrator
02_after_migration_grants.sql → exécuté en tant que vericlaim_migrator
```

Le script de bootstrap refuse de s'exécuter deux fois, vérifie que la base
courante est bien la cible, refuse des rôles privilégiés ou préexistants avec
`LOGIN`, et refuse toute appartenance de rôle. Mon harnais **ne contourne pas**
ces gardes : il connecte `psql` à la base cible (le premier essai a d'ailleurs été
refusé, `\quit` renvoyant 0 — d'où la vérification explicite de la
post-condition en SQL, ajoutée après observation).

## 3. Preuves de privilèges (requêtes exécutées en tant que `vericlaim_app`)

| Contrôle | Requête | Résultat observé |
|---|---|---|
| rôle runtime | `current_user` | **`vericlaim_app`** |
| superuser | `usesuper` | **false** |
| création de base | `rolcreatedb` | **false** |
| contournement RLS | `rolbypassrls` | **false** |
| propriétaire des tables | `bool_or(tableowner = current_user)` | **false** |
| tables avec RLS activé | `pg_class.relrowsecurity` | **26** |
| policies déclarées | `pg_policies` | **26** |
| RLS **forcé** (même pour le propriétaire) | `relforcerowsecurity` | **26** |
| audit en écriture | `has_table_privilege(…,'audit_events','UPDATE')` | **false** (append-only) |
| création d'objet dans `public` | `has_schema_privilege(…,'CREATE')` | **false** |
| révision Alembic | `alembic_version` | **`a7d4c9e2b510`** (tête attendue par la CI) |
| lignes visibles sans contexte tenant | `select count(*) from documents` | **0** |

Les 26 tables incluent les tables tenant et les tables « globales ou tenant »
(`rules`, `rule_versions`) protégées par `p_<table>_global_or_tenant`.

## 4. Tests d'isolation rejoués (RLS réel)

```
pytest tests/test_postgres_tenant_isolation.py \
       tests/test_postgres_document_isolation.py \
       tests/test_postgres_operational_integrity.py \
       tests/test_postgres_purge_concurrency.py
→ 27 passed
```

Ces 27 tests étaient **ignorés (skipped)** dans toute exécution sans
`VERICLAIM_TEST_POSTGRES_URL`. Ils prouvent : absence de visibilité sans contexte
tenant, lecture isolée entre deux organisations, refus d'insertion dans une autre
organisation, update/delete inter-tenant sans effet, documents/versions/jobs
isolés, rôle runtime sans privilège élevé, absence de `CREATE` sur `public`,
caractère append-only des tables d'audit, et détection de régression RLS.

C'est aussi la **démonstration que ces preuves existent** : la CI les exécute
déjà ; elles sont ici reproduites sur une seconde instance indépendante.

## 5. Connexion runtime : ce que fait réellement le code

| Point | Implémentation | Observation |
|---|---|---|
| pool | SQLAlchemy, `pool_pre_ping=True` | reconnexion après coupure (voir §5.1) |
| sélection du rôle | `DATABASE_URL` / `DATABASE_URL_APP` | `vericlaim_app`, jamais le propriétaire |
| contexte tenant | `set_config('app.current_organization_id', …, true)` **transactionnel** | vérifié à chaque requête, jamais attaché à une connexion du pool |
| propagation | `after_begin` restaure le contexte de *cette* session uniquement | pas d'héritage entre requêtes (test dédié) |
| transactions | une par requête HTTP, `commit` en sortie, `rollback` sur erreur | 503 explicite si la base est injoignable |
| erreurs de connexion | `DatabaseUnavailableError` → **503**, jamais un repli SQLite | handler ASGI dans `app/main.py` |
| workers | même rôle, contexte posé par organisation, itération `round_robin` | voir `08-workers-et-traitements.md` |
| migrations | jamais par le runtime (`alembic_version` en lecture seule) | `REVOKE ALL` puis `GRANT SELECT` |
| redémarrage | sessions en base, jobs en base | E2E : AP → 200 après redémarrage complet |

### 5.1 Comportement en cas d'indisponibilité

`probe_database()` alimente `/readyz`. Une base arrêtée produit `status:
"unavailable"` et HTTP **503** ; les requêtes API renvoient 503 avec un message
neutre, sans texte de driver ni chaîne de connexion. Le repli SQLite silencieux a
été retiré (ADR-002) — c'était la bonne décision, et le code le documente.

## 6. Demandes à l'assistant (Neon) — état des confirmations

| # | Demande | Ce que j'attends | État |
|---|---|---|---|
| N1 | Rappeler la base staging réellement utilisée par la Preview (`VeriClaim-Recette-Isolated`) et sa **région** | nom + région, aucun DSN | en attente |
| N2 | Confirmer que le rôle runtime de la Preview est `vericlaim_app` et **non** le compte propriétaire | oui/non + preuve | en attente |
| N3 | Confirmer `sslmode=require` sur les deux URLs (migrator, runtime) | oui/non | en attente |
| N4 | Fournir la sortie de `verify_runtime.py --expected-revision a7d4c9e2b510` sur la base staging | succès/échec | en attente |
| N5 | Dire si des migrations ont été appliquées depuis `a7d4c9e2b510` | oui/non | en attente |
| N6 | Confirmer l'absence de données client réelles dans la base staging | oui/non | en attente |
| N7 | Indiquer si la Preview utilise la **même** base que l'environnement Vercel `staging` | oui/non | en attente |

**Pourquoi N7 est importante** : `00-etat-initial-recette.md` montre un
déploiement Vercel d'environnement `staging` sur `main`, distinct des Previews de
branche, avec ses propres variables. Si la Preview et l'environnement `staging`
partagent la même base, la « recette » n'est pas isolée de cet environnement.
C'est une question de périmètre, pas de sécurité immédiate.

**Correctif demandé sans délai si une Preview est servie par SQLite** : depuis
cette campagne, `get_settings()` refuse SQLite sur une plateforme managée
(`VERCEL`). Les Previews construites avant ce commit continuaient à démarrer sur
une base jetable. Il faut vérifier `DATABASE_URL` sur l'environnement *Preview* du
projet Vercel.

## 7. Ce que je n'ai pas fait

- Aucune migration sur la base persistante.
- Aucun accès en écriture à Neon.
- Aucun changement de rôle, de privilège ou de policy.
- Aucune rotation de secret.

## 8. Verdict de la phase

- **Le dispositif de rôles et de RLS est prouvé** sur un PostgreSQL 16 réel, avec
  les scripts revus et le vrai rôle runtime : 26 tables protégées, audit
  append-only, aucun privilège élevé, isolation inter-tenant vérifiée par 27 tests.
- **Neon lui-même n'est pas vérifié** : cela reste à la charge de l'assistant, et
  tant que les réponses N1–N7 manquent, la recette Neon n'est pas qualifiée par ce
  chantier.
