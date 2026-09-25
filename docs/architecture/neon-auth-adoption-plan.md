# Projet d’adoption de Neon Auth pour VeriClaim

> **Statut : différé pour l’identité de production par ADR-0001.** Ce document conserve l’analyse de faisabilité et le plan de reprise éventuel ; il n’autorise aucune modification de code, de branche Neon, de rôle PostgreSQL ou de données.
>
> Objectif étudié : remplacer Clerk et le flux OIDC générique actuel par **Managed Better Auth / Neon Auth**, tout en préservant les garanties tenant, RBAC, RLS, CSRF, audit et immutabilité déjà livrées dans VeriClaim. La décision actuelle est de conserver un contrat OIDC générique indépendant de Neon Auth.

## 1. Décision à confirmer

La migration est techniquement possible, mais elle remplace une frontière de sécurité centrale livrée au Chantier 2. Elle doit être validée comme un chantier d’authentification distinct avant toute implémentation.

La cible recommandée si elle est validée est :

- Neon Auth est le système d’**identité et de session navigateur** ;
- VeriClaim reste la source d’autorité des **organisations, memberships, rôles, permissions et événements d’audit** ;
- PostgreSQL applique le cloisonnement tenant avec les policies RLS VeriClaim ;
- aucune clé Clerk n’est requise ni conservée ;
- le mot de passe local n’est ni exposé dans l’UI ni accepté comme voie de contournement sans décision produit explicite.

## 2. État constaté avant migration

### Base Neon existante

La base actuellement accessible est un schéma legacy, non une base VeriClaim :

- révision Alembic présente : `004_compliance_watcher` ;
- tête VeriClaim attendue : `d3c8a6e1b409` ;
- tables legacy existantes : `organizations`, `audit_records`, `api_keys`, `monitored_targets`, `monitoring_logs`, `webhooks` ;
- les tables `organizations` et `audit_records` entrent en collision de nom et de type avec le modèle VeriClaim ;
- les migrations, tables RLS, rôles système et permissions C2–C6.1 de VeriClaim n’y sont pas présents ;
- le rôle de connexion actuel a l’attribut PostgreSQL `BYPASSRLS`.

**Interdiction opérationnelle :** ne jamais lancer `alembic upgrade head`, `alembic stamp` ou une migration destructive contre cette base legacy.

### Neon Auth activé sur la branche legacy

Neon Auth a créé le schéma `neon_auth` et ses tables Better Auth (`user`, `session`, `account`, `organization`, `member`, etc.). Le JWKS est accessible et publie actuellement une clé `Ed25519` avec algorithme `EdDSA`.

Cette activation ne :

- rend pas le schéma legacy compatible avec VeriClaim ;
- n’installe pas les policies RLS des tables VeriClaim ;
- ne crée pas un rôle applicatif PostgreSQL `NOBYPASSRLS` ;
- ne protège pas les routes FastAPI existantes ;
- ne fournit pas, dans l’instance contrôlée, un document OIDC Discovery compatible avec le client OIDC actuel de VeriClaim.

## 3. Contraintes d’architecture

### 3.1 Compatibilité frontend/backend

VeriClaim possède une interface Next.js et une API FastAPI distincte. Managed Better Auth privilégie les cookies HTTP-only et la documentation Neon signale que les architectures frontend/backend séparées ne sont pas directement prises en charge lorsque les cookies doivent circuler entre deux domaines.

La cible doit donc utiliser un **unique origin navigateur** :

```text
Navigateur
    │ https://app.exemple.fr
    ▼
Next.js / BFF
    ├── /api/auth/*  → proxy Neon Auth
    └── /api/v1/*    → proxy interne FastAPI
                              │
                              ▼
                        PostgreSQL Neon
```

`api.exemple.fr` séparé peut exister pour des usages serveurs, mais il ne doit pas devenir le chemin principal d’authentification navigateur sans pont JWT/BFF explicitement conçu et testé.

### 3.2 Identité versus autorisation

Neon Auth peut gérer l’identité, la session navigateur et les fournisseurs OAuth. Il ne doit pas devenir la source de vérité des permissions VeriClaim :

| Couche | Source d’autorité |
|---|---|
| Identité externe (`sub`, e-mail vérifié) | Neon Auth |
| Session navigateur | Neon Auth + proxy Next.js |
| Utilisateur applicatif | `users` VeriClaim |
| Organisation active | `auth_sessions` VeriClaim ou échange contrôlé |
| Membership / rôle | `memberships` / `roles` VeriClaim |
| Permissions fines | `permissions_json` VeriClaim |
| Isolation de données | RLS PostgreSQL VeriClaim |
| Trace | `audit_events` VeriClaim |

Le plugin Organization Neon Auth est une aide potentielle pour l’expérience utilisateur, mais son support de permissions personnalisées est limité. Il ne remplace pas le modèle `owner` / `admin` / `analyst` / `viewer` de VeriClaim.

### 3.3 Jeton court et échange de session

La stratégie préférée minimise le changement sur les routes métier existantes :

1. le navigateur se connecte via Neon Auth sous `app.exemple.fr` ;
2. le frontend obtient un JWT court Neon Auth lorsque nécessaire ;
3. il l’envoie à une route VeriClaim dédiée d’échange, jamais dans une URL ;
4. FastAPI valide strictement le JWT via le JWKS Neon Auth ;
5. FastAPI provisionne/résout l’utilisateur local via `(issuer, sub)` ;
6. FastAPI crée ou renouvelle sa session opaque existante + jeton CSRF ;
7. toutes les routes C2–C6.1 gardent leurs dépendances RBAC et CSRF actuelles.

Cette stratégie permet de conserver les garanties existantes tout en remplaçant le fournisseur d’identité. Elle évite de faire reposer la décision RBAC directement sur des claims JWT, surtout lorsque les custom claims Neon Auth ne sont pas disponibles.

## 4. Exigences de sécurité non négociables

### 4.1 Validation JWT Neon Auth

Le validateur backend devra :

- accepter explicitement `EdDSA` / `Ed25519`, et aucun algorithme implicite ;
- récupérer le JWKS via HTTPS et recharger la clé uniquement lors d’un `kid` inconnu ;
- valider `iss`, `exp`, `iat`, `nbf` si présent, `sub` et l’audience si Neon Auth l’émet ;
- borner le clock skew ;
- ne jamais accepter un JWT non signé ou un algorithme différent ;
- lier le sujet à l’utilisateur local par `(identity_provider, external_subject)` ;
- confirmer la politique d’e-mail vérifié sans supposer qu’un claim spécifique est toujours présent ;
- journaliser la création/liaison locale sans journaliser le JWT.

### 4.2 PostgreSQL et RLS

Deux rôles distincts sont nécessaires dans la nouvelle base :

| Rôle | Usage | Contraintes |
|---|---|---|
| `vericlaim_migrator` | Alembic / DDL | réservé au job de migration ; jamais à l’API |
| `vericlaim_app` | API et workers | `LOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`, non propriétaire, privilèges minimaux |

Le rôle runtime ne doit pas être `neondb_owner`, ne doit pas avoir `BYPASSRLS` et ne doit pas posséder les tables métier. Les grants et default privileges doivent être appliqués par le rôle migrateur.

Les requêtes métier continuent d’installer `app.current_organization_id` à l’intérieur de la transaction. La session de base de données est toujours commit/rollbackée avant retour au pool.

### 4.3 Méthodes de connexion

La contrainte produit historique interdit une authentification locale e-mail/mot de passe sans approbation explicite. Avant activation, sélectionner exactement une politique :

- OAuth social autorisé, sans mot de passe local ;
- e-mail OTP / magic link, sans mot de passe local ;
- SSO entreprise OIDC obligatoire.

Ne pas simplement cacher le formulaire mot de passe : vérifier que le fournisseur et ses routes n’offrent pas une voie d’inscription/connexion non approuvée.

### 4.4 Domaines et cookies

Avant production :

- ajouter exclusivement les domaines HTTPS contrôlés à la liste Neon Auth des trusted domains ;
- utiliser un cookie `Secure`, `HttpOnly`, `SameSite` approprié ;
- ne pas utiliser de wildcard de redirection ou CORS ;
- conserver le proxy `/api` Next.js afin que le navigateur n’appelle jamais `localhost` ni une API interne ;
- imposer HTTPS dans toutes les variables de production.

## 5. Plan d’exécution proposé

### Phase 0 — Décisions et gel de sécurité

- confirmer Neon Auth comme fournisseur d’identité principal ;
- choisir le mode de connexion autorisé ;
- choisir les domaines frontend/API et la topologie single-origin ;
- définir la politique MFA, vérification d’e-mail, récupération de compte et rétention ;
- conserver la base legacy intacte ;
- renouveler tous les secrets déjà exposés.

### Phase 1 — Environnement Neon propre

- créer un **nouveau projet Neon** ou une branche/base entièrement dédiée à VeriClaim ;
- ne réutiliser ni les tables, ni la version Alembic, ni les comptes legacy ;
- provisionner Neon Auth sur la nouvelle branche uniquement ;
- ajouter les trusted domains ;
- configurer uniquement les fournisseurs de connexion approuvés ;
- créer le rôle migrateur et le rôle runtime ;
- stocker les URLs/secrets dans le gestionnaire de secrets du déploiement.

### Phase 2 — Schéma et contrôle d’accès PostgreSQL

- lancer `alembic upgrade head` contre la nouvelle base ;
- contrôler `alembic current = d3c8a6e1b409` ;
- vérifier la présence des tables C1–C6.1 ;
- vérifier RLS `ENABLE` + `FORCE` et toutes les policies ;
- tester que le rôle runtime ne lit aucune ligne sans contexte tenant ;
- vérifier que le rôle migrateur, et uniquement lui, peut effectuer les DDL.

### Phase 3 — Adaptation applicative Neon Auth

- intégrer le client Neon Auth et le handler Next.js sous un préfixe réservé ;
- empêcher la réécriture Next `/api/:path*` de capturer le handler Neon Auth local ;
- remplacer l’écran de connexion OIDC par le parcours approuvé ;
- ajouter le validateur JWT EdDSA FastAPI ;
- ajouter une route d’échange Neon JWT → session opaque VeriClaim ;
- désactiver les routes/cookies OIDC précédents après migration contrôlée ;
- conserver RBAC, CSRF, audit et RLS existants ;
- ne pas migrer de comptes réels sans plan de correspondance validé.

### Phase 4 — Tests de sécurité et recette

- test de JWKS, rotation de `kid`, signature invalide, `iss` invalide, jeton expiré et audience invalide ;
- test de refus si e-mail/identité ne satisfait pas la politique retenue ;
- test de création d’utilisateur local idempotente pour le même `(issuer, sub)` ;
- test de changement de rôle et invalidation de session ;
- test RBAC/RLS inter-tenant sous rôle `vericlaim_app` ;
- test CSRF pour toutes les mutations session-cookie ;
- test de logout, révocation et session expirée ;
- test navigateur avec domaine de preview et domaine de production ;
- revue manuelle des journaux pour absence de JWT, cookie, secret ou PII excessive.

### Phase 5 — Mise en production

- déployer d’abord un environnement preview Neon Auth + base dédiée ;
- réaliser une recette de connexion complète ;
- approuver les migrations ;
- appliquer la production via job de migration unique ;
- démarrer API et workers avec le rôle runtime ;
- surveiller les erreurs de jeton, JWKS, RLS et session ;
- conserver une procédure de retour arrière qui ne détruit ni les données tenant ni les événements d’audit.

## 6. Variables de déploiement cibles

Les noms exacts seront validés avec les SDK au moment de l’implémentation. Aucun secret ne doit figurer dans Git, dans `.env.example` ou dans les logs.

```text
APP_ENV=production
DATABASE_URL=postgresql+psycopg://vericlaim_app:…@…/vericlaim?sslmode=require
DATABASE_URL_MIGRATOR=postgresql+psycopg://vericlaim_migrator:…@…/vericlaim?sslmode=require

NEON_AUTH_BASE_URL=https://…neonauth…/vericlaim/auth
NEON_AUTH_JWKS_URL=https://…neonauth…/vericlaim/auth/.well-known/jwks.json
NEON_AUTH_COOKIE_SECRET=…

FRONTEND_URL=https://app.example.fr
CORS_ORIGINS=https://app.example.fr
BACKEND_URL=http://api-internal:8000
AUTH_COOKIE_SECURE=true
```

`DATABASE_URL_MIGRATOR` n’est disponible que dans le job de migration. L’API, les workers et le frontend n’utilisent jamais cette identité.

## 7. Critères de sortie

La migration est considérée réussie uniquement si :

- la base legacy n’a subi aucune modification ;
- la nouvelle base est exactement à la tête `d3c8a6e1b409` ;
- l’API n’utilise pas de rôle `BYPASSRLS` ;
- l’intégralité de la suite backend et du build frontend est verte ;
- les tests de JWT EdDSA, RLS, RBAC, CSRF et changement de tenant passent ;
- le flux navigateur fonctionne exclusivement via les domaines approuvés ;
- aucune méthode de connexion non approuvée n’est accessible ;
- les secrets sont stockés exclusivement dans le gestionnaire de secrets du déploiement ;
- une revue humaine valide la mise en production.

## 8. Questions encore ouvertes

1. Quel mode de connexion est approuvé : OAuth, OTP/magic link, ou SSO entreprise ?
2. Faut-il impérativement conserver le SSO OIDC entreprise générique ?
3. Quel domaine unique hébergera le navigateur et le BFF Next.js ?
4. Faut-il créer un nouveau projet Neon ou une branche/base isolée du projet actuel ?
5. Quelle plateforme exécute Next.js, FastAPI et les workers ?
6. Quelle politique MFA et de récupération de compte est retenue ?
7. Neon Auth Beta est-il acceptable pour l’environnement de production prévu ?

## Sources de conception

- Neon Managed Better Auth : https://neon.com/docs/auth/overview
- JWT Neon Auth / EdDSA : https://neon.com/docs/auth/guides/plugins/jwt
- Limites de topologie : https://neon.com/docs/auth/roadmap
- Organisation Neon Auth : https://neon.com/docs/auth/guides/plugins/organization
- Trusted domains : https://neon.com/docs/auth/guides/configure-domains
