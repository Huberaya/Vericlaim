# Audit de sécurité et d’architecture — branche distante `origin/main`

- **Date :** 25 septembre 2026
- **Branche auditée :** `origin/main`
- **Commit audité :** `be90de97fc026605bdd17ecd34388fb427a39df9`
- **Baseline contrôlée comparée :** `736660929fbe47448004af827792d75281fcf42b`
- **Méthode :** analyse statique et Git en lecture seule. Aucun code de la branche auditée n’a été exécuté, aucune migration ni déploiement n’a été lancé.

## Verdict

**REJET — ne pas fusionner, déployer ni appliquer les migrations de cette branche.**

La branche distante n’est pas une évolution compatible avec la baseline VeriClaim contrôlée. Elle remplace la chaîne de migrations C2–C6.1, retire l’architecture OIDC/RBAC/RLS/audit validée, réintroduit Clerk, expose des secrets de production et ajoute des comportements contraires au positionnement du produit.

## Résumé des constatations critiques

| Priorité | Constat | Preuve statique | Impact |
|---|---|---|---|
| P0 | Secrets réels dans une branche publique | `render.yaml` contient une URL PostgreSQL avec rôle propriétaire et mot de passe ; `render.yaml` et `backend/app/core/config.py` contiennent une clé secrète Clerk | Compromission possible de base, d’identité et de déploiement |
| P0 | Remplacement destructif de l’architecture validée | 7 migrations contrôlées supprimées, dont `d3c8a6e1b409`; modèles, services OIDC, documents, workers et C6.1 retirés | Incompatibilité totale avec la base cible et perte de contrôles de sécurité |
| P0 | Absence de RLS et de relations référentielles | 0 instruction RLS/policy PostgreSQL, 0 `ForeignKey`, 0 `UniqueConstraint` dans le backend distant | Isolation organisationnelle non démontrable et non imposable en base |
| P0 | Mode public avec privilèges administratifs implicites | Contexte sans authentification bascule vers tenant `default` avec scopes `admin`; plusieurs routes métier restent publiques | Écriture/lecture non authentifiée, confusion inter-tenant, exposition fonctionnelle |
| P0 | Création publique de tenant et de clé API | `POST /api/v1/engine/tenants` n’exige aucune authentification et retourne une clé API initiale | Création arbitraire de comptes et clés à privilèges élevés |
| P0 | Promesses de certification et décisions juridiques | Statuts `CERTIFIED`, « registre officiel », « attestation certifiée conforme », PDF « rapport d’audit réglementaire » et « infraction détectée » | Contradiction avec le positionnement pré-audit, risque juridique et commercial |
| P0 | Certificats/écolabels fabriqués par heuristique | `ecolabel_connector.py` précharge des licences et synthétise des enregistrements « officiellement reconnus » à partir du format d’un numéro | Références/certificats non vérifiés présentés comme fiables |

## Constatations détaillées

### 1. Secrets exposés — P0

Les fichiers suivants de la branche distante contiennent des secrets ou credentials réels :

```text
render.yaml
backend/app/core/config.py
```

Ils comprennent un accès PostgreSQL avec rôle propriétaire et une clé secrète d’identité Clerk. La présence dans l’historique Git public doit être traitée comme une exposition effective, même si les ressources correspondantes semblent inactives.

**Actions obligatoires avant toute autre opération :**

1. Révoquer/rotater immédiatement le mot de passe PostgreSQL concerné.
2. Révoquer/rotater immédiatement la clé secrète Clerk concernée, ou supprimer l’instance si elle n’est plus utilisée.
3. Révoquer tout déploiement Render associé et supprimer ses variables d’environnement compromises.
4. Préserver les preuves nécessaires, puis planifier une réécriture de l’historique Git uniquement après rotation ; supprimer le fichier seul ne révoque pas un secret.
5. Ne jamais recopier les nouvelles valeurs dans une issue, un commit, un log ou cette conversation.

### 2. Chaîne de migrations et modèle de données incompatibles — P0

La branche distante supprime la chaîne Alembic contrôlée :

```text
f3efc9c81c8d
7b3b4c985738
a4f6b2d8e901
c91d2e7f4a3
e17c4f5a9b02
f6a2d9b41c07
d3c8a6e1b409
```

Elle la remplace par :

```text
001_initial_schema
002_multi_tenant_and_api_keys
003_webhooks_table
004_compliance_watcher
```

Cette nouvelle chaîne ne contient ni RLS, ni policies, ni contraintes de clés étrangères. Elle utilise des identifiants texte, des organisations par défaut, et ne représente pas les organisations, memberships, rôles, sessions, documents, versions, analyses et catalogues de la baseline C6.1.

**Décision :** ne jamais appliquer `001`–`004` à la base `vericlaim` européenne cible.

### 3. Régression d’identité, RBAC et isolation organisationnelle — P0

La branche :

- retire le contrat OIDC générique, la gestion de session, CSRF, memberships et RBAC VeriClaim ;
- ajoute Clerk côté frontend et backend ;
- désactive la vérification JWT audience (`verify_aud=False`) ;
- auto-provisionne des organisations depuis des claims non suffisamment contraints ;
- accorde des scopes incluant `admin` aux contextes publics et aux organisations Clerk provisionnées ;
- ne définit aucune RLS PostgreSQL ni policy ;
- n’impose pas de relation référentielle entre données et organisation.

Cela viole les décisions ADR-0001 et la stratégie active : OIDC fournisseur-neutre, autorité RBAC VeriClaim, sessions/CSRF/audit sous contrôle VeriClaim, rôle runtime `NOBYPASSRLS` et RLS forcée.

### 4. Endpoints publics non conformes — P0

L’analyse AST des routes révèle notamment :

```text
POST /evaluate                 → tenant optionnel / fallback public
POST /evaluate/url             → tenant optionnel / fallback public
POST /evaluate/batch           → tenant optionnel / fallback public
GET  /audits                   → tenant optionnel / fallback public
POST /tenants                  → aucune authentification
POST /export/pdf               → aucune authentification
POST /export/excel             → aucune authentification
POST /ecolabels/sync           → aucune authentification
GET  /verify/{audit_id}        → aucune authentification
POST /watcher/run-batch        → aucune authentification
```

`POST /tenants` crée une organisation et retourne une clé API initiale portant les scopes :

```text
admin
audit:read
audit:write
batch:run
```

Cette exposition contrevient explicitement à l’interdiction de conserver une démo publique après le chantier identité.

### 5. Certification, verdict juridique et données réglementaires inventées — P0

La branche ajoute :

- un vérificateur public produisant `CERTIFIED` et parlant de « registre officiel » ;
- un générateur PDF qualifié de rapport/audit réglementaire, affichant « conforme » ou « infraction détectée » ;
- des estimations d’exposition financière et des suggestions contractuelles ;
- un connecteur d’écolabels qui marque des licences préchargées ou synthétisées comme reconnues officiellement.

En particulier, un format de licence correspondant à une expression régulière suffit à créer un record avec dates de validité, émetteur, URL et marqueur `officially_recognised=True`.

Cela contrevient aux règles fondamentales VeriClaim : ne pas inventer de certificats, références ou conclusions juridiques ; ne pas présenter le produit comme autorité, certificateur, avocat ou moteur de verdict automatique.

### 6. SSRF, exécution de JavaScript tiers et webhooks — P0/P1

#### Scraping URL — P1

Le scraper :

- accepte une URL HTTP(S) utilisateur ;
- contrôle les IP littérales et quelques hostnames ;
- ne résout pas le nom de domaine avant connexion ;
- ne revalide pas les redirections ;
- suit automatiquement les redirections HTTP.

Il reste donc vulnérable aux scénarios DNS rebinding et redirections vers des ressources privées.

#### Exécution JavaScript — P1

Le renderer assemble les scripts d’une page distante et les exécute via :

```text
subprocess.run(["node", "-e", ...])
vm.runInContext(...)
```

Aucune isolation OS/conteneur dédiée, règle réseau ni sandbox navigateur robuste n’est configurée. En outre, le Dockerfile backend n’installe pas Node.js : la fonction est donc à la fois fragile fonctionnellement et dangereuse si Node devient disponible.

#### Webhooks — P0

Les webhooks stockent leur secret en clair, le renvoient lors de la consultation, et envoient des requêtes HTTP vers des URLs configurées par l’utilisateur sans validation SSRF. Ils permettent donc d’atteindre des adresses internes depuis le runtime.

### 7. Runtime, migrations et persistance non sûrs — P0/P1

La branche :

- exécute `Base.metadata.create_all()` au démarrage ;
- tente des `ALTER TABLE` dynamiques avec exceptions ignorées ;
- bascule silencieusement vers SQLite quand PostgreSQL est indisponible ;
- lance Alembic au démarrage, mais démarre quand même l’application si la migration échoue ;
- configure un rôle propriétaire dans le manifest de déploiement ;
- configure `CORS_ORIGINS=*` dans ce manifest.

Ces comportements empêchent la traçabilité des migrations, peuvent créer un split-brain PostgreSQL/SQLite et violent le principe d’échec fermé.

### 8. CI, tests et qualité — P1/P2

- 7 modules de tests contrôlés sont supprimés ; un seul module de test Python demeure.
- La CI ne teste pas PostgreSQL, RLS, restauration, RBAC, isolation inter-tenant, CSRF, scanner de documents ou workflow de preuve.
- Les actions GitHub sont référencées par tags et non par SHA.
- Aucun secret scan, SAST, audit de dépendances, scan de conteneur ou vérification de migration PostgreSQL ne figure dans la CI.
- `backend/app/core/config.py` importe `python-dotenv`, absent de `backend/requirements.txt` : un déploiement propre peut échouer au démarrage.
- `git diff --check` signale plusieurs erreurs de whitespace.

## Écarts avec les décisions validées

| Décision active | État de la branche distante |
|---|---|
| OIDC générique fournisseur-neutre | Violée : Clerk réintroduit |
| RBAC/RLS/audit sous autorité VeriClaim | Violée : RLS/policies absentes, RBAC réduit à scopes implicites |
| Pas de démo publique après identité | Violée : endpoints métier et création tenant publics |
| Système de pré-audit, pas de verdict juridique | Violée : certification, conformité, infraction et exposition juridique affichées |
| Preuve vérifiée et validation humaine | Violée : certificats synthétisés, watcher et décisions automatiques |
| Migrations contrôlées, runtime non propriétaire | Violée : rôle propriétaire dans deployment manifest, `create_all`, migrations ignorables |
| Documents sécurisés/OCR contrôlé | Régressée : modèle de stockage/quarantaine/scanner et workers supprimés |
| C6.1 catalogue tenant-scoped | Supprimé : modèles/services/API/tests C6.1 retirés |

## Décision d’architecture recommandée

1. **Geler immédiatement la branche distante** : aucun déploiement, migration ou merge.
2. **Rotater les secrets exposés** avant tout nettoyage Git.
3. **Préserver la branche distante** sous une branche de quarantaine pour investigation et conservation de preuve.
4. **Restaurer `main` vers la lignée contrôlée** `08ce3b8afb61e5f252eac68175e22f274d980397`, qui contient C6.1 et les contrôles de migration, une fois l’autorisation GitHub d’écriture rétablie.
5. **Ne pas cherry-pick** les commits distants tels quels.
6. Si certaines fonctions métier sont souhaitées (batch, export, intégrations, scraping), les re-spécifier une par une depuis la baseline contrôlée, avec ADR, modèle de menace, tests RLS/RBAC et validation humaine.
7. Reprendre les migrations Neon uniquement après restauration de la lignée, activation sécurisée de `vericlaim_migrator`/`vericlaim_app`, et configuration des secrets dans GitHub Environment `production`.

## État après audit

```text
Fusion distante : interdite
Migrations distantes 001–004 : interdites
Déploiement Render/Railway distant : interdit
Migrations C6.1 sur Neon : suspendues jusqu’à restauration de main
```
