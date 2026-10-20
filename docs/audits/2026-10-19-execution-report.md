# VeriClaim AI — rapport d'exécution du chantier de validation staging

- **Date :** 19 octobre 2026
- **Branche :** `arena/81e77eaa-vericlaim`
- **Cible :** `main` @ `36cd8313434a3b51a2428e676d5e5d8a851ee8e2`
- **Pull request :** [#9](https://github.com/Huberaya/Vericlaim/pull/9)
- **Statut final :** **partiellement validé** — corrections livrées et prouvées ; validation staging bloquée par un environnement externe inexistant.
- **Règle :** aucune valeur de secret, de jeton ou d'URL de connexion dans ce document.

## 1. Diagnostic initial

Le dépôt a été réexaminé avant toute modification. Constats :

| Élément | Constat vérifié |
|---|---|
| `main` | `36cd8313434a3b51a2428e676d5e5d8a851ee8e2` |
| `ffa6c58` / `d68194c` | présents sur la branche de travail, **absents de `main`** |
| PR existante pour cette branche | aucune |
| Working tree | propre, aligné sur la tête distante `6ac4530` avant travaux |
| Staging applicatif | **inexistant** (voir §8) |

Les deux commits antérieurs contenaient déjà un premier durcissement ; ils ont été conservés, repris et complétés.

## 2. Défauts reproduits

| ID | Gravité | Défaut | Cause racine | Reproduction | Attendu | Observé (avant) |
|---|---|---|---|---|---|---|
| D1 | Critique | `POST /api/v1/auth/dev-login` délivre une session admin sans authentification | Routeur monté inconditionnellement ; aucune garde d'environnement dans le handler | Import de l'app avec `APP_ENV=staging` ; inspection des routes | Endpoint absent ou refusé | Route présente, `security: aucune`, création/réutilisation de `pilote@vericlaim.ai` en owner |
| D2 | Élevée | Le contournement est actif **par défaut** en développement | `dev_login_allowed()` ne testait que `is_production_like` | `dev_login_allowed()` avec `APP_ENV=development` | Désactivé sauf opt-in | `True` |
| D3 | Élevée | `api_keys` et `legal_holds` absentes de la vérification RLS | Liste d'attentes maintenue à la main | Comparaison migrations ↔ `EXPECTED_RLS_TABLES` | 26/26 tables | 24/26 (corrigé en `ffa6c58`) |
| D4 | Élevée | Le vérificateur ne détecte pas un prédicat permissif ni une nouvelle table non protégée | Il comptait les policies au lieu d'inspecter `USING`/`WITH CHECK` | Base réelle + `DROP POLICY`/`USING (true)`/table ajoutée | Échec signalé | **0 échec** dans les trois cas |
| D5 | Élevée | Aucune preuve RLS PostgreSQL, ni locale ni en CI | Tous les tests tournent sur SQLite, qui n'implémente pas RLS | `pytest` : aucun test PostgreSQL | Isolation prouvée | Non prouvée |
| D6 | Moyenne | `memberships` porte `organization_id` sans RLS | Table d'identité « service-only » par conception | `pg_class` + `information_schema` | Documenté | Pas de RLS (risque résiduel assumé) |
| D7 | Faible | Commentaire Alembic obsolète (`d3c8a6e1b409`) | Documentation non mise à jour | `03_verify_runtime.sql` | `e8f9a1b2c3d4` | Corrigé en `d68194c` |

## 3. Corrections appliquées

### `488edc6` — `fix(auth)`
- `AUTH_DEV_LOGIN_ENABLED` (défaut `false`) ajouté à `Settings` ;
- refus **au démarrage** si le drapeau est activé en `staging`/`production` ;
- `dev_login_allowed()` = non production-like **et** opt-in explicite ;
- handler en `404` avant tout accès base ; endpoint retiré d'OpenAPI ;
- `/api/v1/auth/status` expose `dev_login_enabled` ; l'UI conditionne les boutons pilote ;
- `.env.example` documenté, `docker-compose.yml` opt-in local uniquement.

### `087ff25` — `fix(neon)`
- `collect_failures()` extrait du `main()` (testable sans TLS/DSN) ;
- toute table portant `organization_id` doit avoir RLS activée, forcée et au moins une policy ; `memberships` est la seule exception, explicitement listée ;
- chaque policy doit référencer `vericlaim_current_organization_id()` dans `USING` **et** `WITH CHECK`.

### `bcf993d` — `test(postgres)`
- `backend/tests/test_postgres_tenant_isolation.py` : base jetable construite par `alembic upgrade head`, grants livrés appliqués, assertions avec `vericlaim_app` ;
- job CI `PostgreSQL tenant isolation (RLS)` sur `postgres:16`.

## 4. Preuves exécutées

| Commande | Code de sortie | Résultat |
|---|---|---|
| `pytest -q` (SQLite) | 0 | **96 passed, 11 skipped** (tests PG ignorés sans serveur) |
| `pytest -q` avec `VERICLAIM_TEST_POSTGRES_URL` | 0 | **107 passed** |
| `pytest -q tests/test_postgres_tenant_isolation.py` | 0 | **11 passed** |
| `npm run typecheck` | 0 | OK |
| `npm run build` | 0 | OK (3 pages statiques) |
| `python -m alembic heads` | 0 | `e8f9a1b2c3d4 (head)` |
| `alembic upgrade head --sql` | 0 | 52 instructions `ROW LEVEL SECURITY` |
| `alembic upgrade head` sur PostgreSQL 16.2 vierge | 0 | 8 révisions appliquées |
| scan secrets (regex CI) | 0 | aucun secret |

### Isolation prouvée sur PostgreSQL réel

| Assertion | Résultat observé |
|---|---|
| Lignes visibles sans contexte d'organisation | **0** sur `suppliers`, `products`, `analyses`, `api_keys`, `legal_holds` |
| Lecture par tenant | 1 ligne pour son organisation, **0** pour l'autre (`suppliers`, `products`, `api_keys`, `legal_holds`) |
| `INSERT` avec `organization_id` de l'autre tenant | `InsufficientPrivilege` (WITH CHECK) |
| `UPDATE` croisé | **0 ligne** |
| `DELETE` croisé | **0 ligne** |
| Ligne victime toujours lisible par son propriétaire | oui |
| Attributs du rôle runtime | `(super=False, createrole=False, createdb=False, bypassrls=False)` |
| `CREATE` sur `public` | refusé |
| `alembic_version` | `SELECT` oui / `UPDATE` non |
| `audit_events` / `audit_records` | `UPDATE` et `DELETE` refusés |

### Le vérificateur détecte les régressions (contrôles négatifs)

| Régression injectée | Échec remonté |
|---|---|
| `ALTER TABLE reports DISABLE ROW LEVEL SECURITY` | `RLS is disabled on reports` |
| policy réécrite `USING (true) WITH CHECK (true)` | `policy p_claims_tenant on claims does not use the tenant helper` |
| nouvelle table `experimental_notes` avec `organization_id` | `table with organization_id has no RLS policy: experimental_notes` |
| base saine | **0 échec** |

## 5. CI sur le dernier commit

- **SHA :** `bcf993da225cdbcf32ef4fb21106d4648e51aca4`
- **Run :** [39250805240](https://github.com/Huberaya/Vericlaim/actions/runs/39250805240) — `success`

| Job | Conclusion |
|---|---|
| `Backend verification` (tests, tête Alembic, scan secrets, whitespace) | **success** |
| `PostgreSQL tenant isolation (RLS)` | **success** |

## 6. État de la pull request

- **[#9](https://github.com/Huberaya/Vericlaim/pull/9)** — `OPEN`, `mergeable=MERGEABLE`, tête `bcf993da` → base `main`.
- Aucune fusion effectuée (non autorisée).
- Un déploiement **Preview** Vercel a été créé automatiquement par la PR (`7173499020`, commit `bcf993da`). Ce n'est pas la production. Il est protégé par l'authentification Vercel (SSO) : aucune requête anonyme n'y accède, donc aucun contrôle HTTP n'a pu y être fait.

## 7. Risques restants

1. **R1 — compte pilote en production.** Le commit déployé en production (`36cd831`) contient le contournement. Tant que `main` n'est pas mis à jour, `POST /api/v1/auth/dev-login` reste joignable sur l'application de production. Procédure de réponse à incident recommandée (non exécutée, aucune autorisation) :
   - vérifier `auth_sessions` pour l'utilisateur `pilote@vericlaim.ai` (dates, `user_agent_hash`, adresses) ;
   - vérifier `audit_events` pour les actions de ce compte ;
   - révoquer les sessions actives (`revoked_at`) ;
   - après déploiement du correctif, `AUTH_DEV_LOGIN_ENABLED` reste absent/false.
2. **R2 — `memberships` sans RLS.** L'isolation dépend du filtrage applicatif pour cette table. Acceptable par conception (résolution d'identité avant contexte), mais toute requête future oubliant le filtre exposerait des appartenances.
3. **R3 — configuration d'environnement.** Si la production tournait avec `APP_ENV=development`, les garde-fous de configuration (dont D1) seraient contournés. Non vérifiable depuis cet environnement.
4. **R4 — aucun test frontend automatisé.** Seuls `tsc --noEmit` et `next build` couvrent l'interface.
5. **R5 — pas de staging applicatif.** Voir §8.

## 8. Staging applicatif : absence établie

| Preuve | Résultat |
|---|---|
| URL applicative dans le dépôt, les docs, les workflows, l'historique | aucune |
| Déploiement GitHub `staging` #7167225128 | `success`, **`environment_url` vide** (créé par le workflow de migration) |
| Déploiement applicatif au commit `36cd831` | **Production** Vercel (#7163305631) |
| Déploiements Preview | présents, **protégés par SSO Vercel** |
| Egress de l'environnement d'audit | GitHub / PyPI / npm uniquement ; tout `vercel.app` échoue en TLS |

**Conclusion :** aucune application staging indépendante n'existe. Tester la production a été exclu. Aucun contrôle HTTP de bout en bout n'est donc revendiqué.

## 9. Contrôles non exécutés

| Contrôle | Raison |
|---|---|
| Parcours HTTP sur staging | Pas d'application staging ; Preview protégé par SSO |
| Connexion à la base staging (`alembic current`) | Aucun identifiant disponible ; secrets GitHub en écriture seule |
| RLS sur la base staging réelle | Accès réseau et credentials absents (la preuve a été faite sur PostgreSQL 16.2 local et en CI) |
| SSO OIDC réel | Fournisseur externe non configuré dans l'environnement d'audit |
| Upload S3/MinIO + ClamAV | Infrastructure absente |
| Tests de charge / rate limiting | Non implémentés dans le produit |
| Rejeu du workflow staging après D3 | Exige un `workflow_dispatch` sur `main` avec le secret `DATABASE_URL_APP` — non autorisé ici |

## 10. Matrice de clôture

| ID | Problème | Cause racine | Correction appliquée | Test exécuté | Preuve | Statut |
|---|---|---|---|---|---|---|
| D1 | Session admin sans authentification via `dev-login` | Routeur monté sans garde | `488edc6` : 404 hors opt-in, retrait OpenAPI | `test_dev_login_is_refused_unless_explicitly_opted_in` | 107 tests verts ; CI verte | **Corrigé** |
| D2 | Bypass actif par défaut | Garde limitée à `is_production_like` | `488edc6` : `AUTH_DEV_LOGIN_ENABLED=false` + refus au boot | `test_production_like_configuration_refuses_to_start_with_dev_login_enabled` | `RuntimeError` attendue | **Corrigé** |
| D3 | Tables RLS non vérifiées | Liste manuelle | `ffa6c58` + test de dérive | `test_runtime_verification_covers_every_rls_protected_table` | 26/26 | **Corrigé** |
| D4 | Vérificateur aveugle aux prédicats permissifs | Comptage de policies | `087ff25` : inspection `USING`/`WITH CHECK` | 3 contrôles négatifs PostgreSQL | échecs attendus remontés | **Corrigé** |
| D5 | Aucune preuve RLS PostgreSQL | Tests SQLite uniquement | `bcf993d` + job CI | `test_postgres_tenant_isolation.py` | 11 passed local, job CI success | **Corrigé** |
| D6 | `memberships` sans RLS | Conception service-only | Documentée, exclue explicitement du garde-fou | — | §7 R2 | **Risque accepté** |
| D7 | Commentaire Alembic obsolète | Documentation | `d68194c` | test de cohérence du runbook | — | **Corrigé** |
| D8 | Staging applicatif inexistant | Hors dépôt | — | Inspection API/plateforme | §8 | **Bloqué (externe)** |

## 11. Conditions restant à satisfaire avant production

1. Faire relire et **fusionner** la PR #9 (hors périmètre de cette mission).
2. Traiter R1 : auditer et révoquer les sessions du compte pilote, puis déployer le correctif.
3. Confirmer `APP_ENV=production` sur le déploiement réel.
4. Créer une application staging indépendante (URL, `APP_ENV=staging`, base dédiée) et rejouer le workflow de migration pour couvrir `api_keys` et `legal_holds`.
5. Rejouer l'audit fonctionnel HTTP sur ce staging (auth, RBAC, isolation, documents, analyses, audit, rapports).
6. Ajouter un socle de tests frontend avant de considérer l'interface couverte.
