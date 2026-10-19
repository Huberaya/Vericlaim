# Audit fonctionnel et de sécurité — validation staging

- **Date :** 19 octobre 2026
- **Dépôt :** `Huberaya/Vericlaim`
- **Commit de référence audité :** `36cd8313434a3b51a2428e676d5e5d8a851ee8e2` (`main`)
- **Run de migration :** `Staging database migration` #39210216211, `success`, 06:29:31 → 06:30:14 UTC
- **Branche de travail :** `arena/81e77eaa-vericlaim` (aucun push vers `main`)
- **Règle :** aucune valeur de secret, de mot de passe ou d'URL de connexion
  n'est reproduite dans ce document.

## 1. Verdict

**Statut : BLOQUÉ** pour la validation fonctionnelle de staging telle que
demandée.

Le blocage n'est pas un manque d'effort de test : **il n'existe pas
d'application staging** à interroger. Les contrôles réalisables ont néanmoins
été exécutés, deux anomalies ont été trouvées et corrigées, et une troisième
est documentée sans correction.

## 2. Réalité de l'environnement « staging »

| Élément | Constat | Preuve |
|---|---|---|
| Application staging | **Inexistante** | aucune URL applicative dans le dépôt, les docs, les workflows ou l'historique Git ; l'environnement GitHub `staging` n'héberge qu'une base |
| Déploiement GitHub `staging` (#7167225128) | créé par le workflow de migration, **sans `environment_url`** | `GET /repos/.../deployments/7167225128/statuses` → `success`, URL vide |
| Déploiement applicatif au commit `36cd831` | **Production** (Vercel), ref `36cd831` | `GET /repos/.../deployments/7163305631` |
| Environnements GitHub | `Preview`, `Production`, `PRODUCTION_DATABASE_HOST`, `staging` | `GET /repos/.../environments` |

Conséquence : la seule application déployée au commit audité est la
**production**. Toute requête HTTP aurait porté sur la production, ce qui est
une condition d'arrêt explicite.

## 3. Limites d'accès de l'environnement d'audit

| Limite | Constat |
|---|---|
| Sortie réseau | seuls `github.com`, `api.github.com`, `pypi.org`, `registry.npmjs.org` répondent ; tout hôte `vercel.app` ou de test échoue en TLS (`SSL_ERROR_SYSCALL`) |
| Journaux Actions | `gh run view --log` échoue : l'hôte `results-receiver.actions.githubusercontent.com` est hors du périmètre réseau. Seules les **conclusions d'étapes** sont lisibles via l'API |
| Base staging | aucun identifiant disponible ; les secrets GitHub sont en écriture seule (`gh secret list` → 403) |
| PostgreSQL local | aucun binaire `postgres`/`initdb`, pas de Docker → RLS non exécutable localement |
| Production | hors périmètre, jamais contactée |

## 4. Contrôles exécutés

| # | Contrôle | Méthode | Résultat |
|---|---|---|---|
| 1 | Suite backend au commit audité | `pytest -q` sur un worktree détaché de `36cd831`, SQLite, `APP_ENV=test` | **92 passed** |
| 2 | Suite backend après corrections | même commande sur la branche de travail | **95 passed** |
| 3 | Types et build frontend | `npm run typecheck` puis `npm run build` | **OK** (compilation et génération statique) |
| 4 | Étapes du run staging | `gh api .../actions/runs/39210216211/jobs` | **11 étapes `success`**, dont préflight (n°5) et vérification RLS (n°9) |
| 5 | Comportement fail-closed du préflight | run #39183387110 | **échec à l'étape 5** → le garde-fou refuse bien une cible non conforme |
| 6 | Démarrage applicatif en configuration staging | app importée avec `APP_ENV=staging` et variables complètes | `/healthz` → **200**, `/api/v1/auth/status` → **200** |
| 7 | Surface d'authentification | introspection des 85 routes des 22 routeurs | 4 routes sans dépendance d'authentification : `status`, `login`, `callback`, `dev-login` |
| 8 | Couverture RLS | comparaison migrations ↔ attentes du vérificateur | **26 tables protégées, 24 vérifiées** → écart |
| 9 | Changement d'environnement | génération OpenAPI avec `APP_ENV=staging` | `dev-login` **exposé**, sans schéma de sécurité |

## 5. Anomalies

### A1 — CRITIQUE — contournement d'authentification `/api/v1/auth/dev-login`

**Constat.** L'endpoint `POST /api/v1/auth/dev-login` était monté
inconditionnellement. Sans authentification, il :

1. lit ou crée l'utilisateur `pilote@vericlaim.ai` ;
2. crée une organisation et une adhésion de rôle **owner/admin** si elle
   n'existe pas ;
3. émet les cookies de session et CSRF.

Si l'utilisateur pilote existe déjà en base, l'appelant récupère **sa session
existante** : ce n'est pas une simple inscription, mais une prise de contrôle
de compte administrateur.

**Preuve avant correction** (configuration `APP_ENV=staging`,
`is_production_like=True`) :

```
route dev-login   : ['/api/v1/auth/dev-login']
méthodes          : ['post']
security déclarée : aucune
```

**Portée.** Le déploiement de production pointe le commit `36cd831`, celui-là
même qui contient cet endpoint. Le bouton « Accéder à l'espace pilote » était
de plus affiché sans condition dans l'interface.

**Correction** (commit `ffa6c58`) :

- refus `404` dès que l'environnement est `staging` ou `production`
  (`dev_login_allowed()`), avant toute lecture en base ;
- endpoint retiré du schéma OpenAPI ;
- `/api/v1/auth/status` expose `dev_login_enabled` ;
- l'interface n'affiche les boutons pilote que si le backend l'annonce ;
- tests de régression : refus 404, statut, et environnement local toujours
  autorisé.

**Preuve après correction** (même configuration staging) :

```
dev_login_allowed()     : False
présent dans OpenAPI    : False
GET /api/v1/auth/status : 200 {'oidc_configured': True, 'authentication_required': True, 'dev_login_enabled': False}
```

### A2 — ÉLEVÉE — angle mort dans la vérification RLS de staging

**Constat.** `infra/neon/verify_runtime.py` vérifie 24 tables. La chaîne de
migrations en protège **26** : `api_keys` et `legal_holds`, ajoutées par
`e8f9a1b2c3d4`, étaient absentes des attentes. Une régression RLS sur ces deux
tables serait passée inaperçue, alors que `api_keys` porte des secrets
d'authentification machine.

**Correction** (commit `ffa6c58`) : ajout des deux tables et test
`test_runtime_verification_covers_every_rls_protected_table`, qui dérive
l'ensemble protégé depuis les fichiers de migration — la dérive future casse
la CI.

**Effet de bord assumé :** la prochaine exécution du workflow staging
contrôlera deux tables de plus. La base les a déjà (migration appliquée), donc
aucun risque de faux échec.

### A3 — MOYENNE — la CI n'exerce jamais PostgreSQL

Les 95 tests tournent sur SQLite avec `AUTO_CREATE_SCHEMA=true`. Les policies
RLS, les grants et le rôle runtime ne sont donc **jamais** testés par la CI :
le seul contrôle est le workflow staging manuel. Recommandation : ajouter un
service PostgreSQL au job de CI et y rejouer la chaîne Alembic + un test
d'isolation inter-tenant réel. Non corrigé ici (changement d'infrastructure,
hors périmètre minimal).

### A4 — MINEURE — commentaire obsolète

`infra/neon/03_verify_runtime.sql` annonçait une tête Alembic `d3c8a6e1b409`
au lieu de `e8f9a1b2c3d4`. Corrigé (commit `d68194c`).

## 6. Ce que les preuves RLS et privilèges établissent — et ce qu'elles n'établissent pas

L'étape n°9 du run #39210216211 est **`success`**. Le script exécuté vérifie,
en lecture seule, avec le rôle `vericlaim_app` :

- rôle runtime exact, non superutilisateur, sans `CREATEROLE`, `CREATEDB` ni
  `BYPASSRLS` ;
- révision Alembic identique à la source (`e8f9a1b2c3d4`) ;
- pour chaque table attendue : RLS activée, **forcée**, au moins une policy ;
- pas de `CREATE` sur le schéma `public` ;
- `SELECT` mais pas `UPDATE` sur `alembic_version` ;
- `audit_events` et `audit_records` non modifiables ni supprimables ;
- zéro ligne visible sans contexte d'organisation sur `suppliers`, `products`,
  `documents`, `analyses`.

**Établi :** ces assertions ont abouti sur la base staging au commit `36cd831`.
Un échec aurait rendu l'étape rouge, comme l'a fait le préflight au run
précédent.

**Non établi :**

- la sortie exacte du script (journaux inaccessibles depuis cet environnement) ;
- l'état réel des deux tables ajoutées par la correction A2 ;
- la solidité des politiques au-delà de leur existence : le contrôle vérifie
  « au moins une policy », pas la correction du prédicat. Celui-ci est lisible
  dans les migrations (`organization_id = vericlaim_current_organization_id()`
  avec `WITH CHECK`) mais n'a pas été exercé en base ;
- l'isolation applicative réelle en staging : elle n'est couverte que par les
  tests SQLite, où RLS n'existe pas.

## 7. Tests non exécutés

| Test | Raison |
|---|---|
| Parcours HTTP de bout en bout sur staging | aucune application staging ; la seule URL applicative au commit audité est la production |
| Connexion base staging, `alembic current` | aucun identifiant disponible, secrets en écriture seule |
| Vérification RLS/privileges en direct | PostgreSQL inaccessible (réseau et binaires) |
| SSO OIDC réel | exige un fournisseur d'identité configuré ; testé uniquement en simulation |
| Upload S3/MinIO + ClamAV | infrastructure absente de l'environnement d'audit |
| Requêtes sur la production | interdites par la mission |
| Tests de charge, rate limiting | non prévus par le projet ; aucun mécanisme de limitation présent |

## 8. Conditions à satisfaire avant d'envisager la production

1. **Traiter A1 en priorité.** Le contournement est présent dans le commit
   actuellement déployé en production. Tant qu'il n'est pas remplacé :
   - considérer toute session `pilote@vericlaim.ai` comme potentiellement
     forgée ;
   - vérifier `auth_sessions` (créations, `user_agent`, adresses) et
     `audit_events` pour ce compte, révoquer les sessions actives ;
   - déployer le commit corrigé, avec `APP_ENV=production`.
2. Confirmer que la production tourne bien avec `APP_ENV=production` : si elle
   tournait en `development`, les garde-fous de configuration seraient
   contournés et la base pourrait être SQLite.
3. Fournir une **URL de staging** et un accès réseau, ou autoriser l'exécution
   des tests depuis un runner disposant de cet accès.
4. Rejouer le workflow staging après A2 pour couvrir `api_keys` et
   `legal_holds`.
5. Ajouter PostgreSQL à la CI (A3) avant de considérer la RLS comme
   régressivement protégée.
6. Rejouer l'audit fonctionnel sur une application staging réelle : auth, RBAC,
   isolation inter-organisations, documents, analyses, audit, rapports.

## 9. Traçabilité des corrections

| Commit | Contenu |
|---|---|
| `ffa6c58` | `fix(auth)`: dev-login fail-closed + statut + UI + tests ; RLS `api_keys`/`legal_holds` + test de couverture |
| `d68194c` | `docs(neon)`: tête Alembic alignée dans le script manuel |

Aucune fusion vers `main`, aucun déploiement, aucune écriture en base.
