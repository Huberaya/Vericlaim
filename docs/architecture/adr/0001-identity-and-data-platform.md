# ADR-0001 — Plateforme de données et identité de VeriClaim

- **Statut :** accepté
- **Date :** 2026-09-25
- **Décideur :** direction produit et architecture VeriClaim
- **Portée :** fondation SaaS B2B, identité, isolation tenant, base de données et déploiement

## Contexte

VeriClaim est un produit RegTech B2B de pré-audit et de gestion du risque. Il conserve des documents fournisseurs, des passages citables, des analyses versionnées, des claims, des événements d’audit et, à terme, des preuves et validations humaines.

La différenciation ne doit pas reposer sur une promesse de verdict juridique automatisé. Elle repose sur :

1. l’intégrité des sources et de la preuve ;
2. l’isolation organisationnelle ;
3. la traçabilité complète ;
4. la revue humaine ;
5. l’interopérabilité avec les identités d’entreprise.

L’audit de l’environnement existant a révélé :

- une base Neon legacy, à une chaîne Alembic étrangère (`004_compliance_watcher`) ;
- des collisions de tables avec le modèle VeriClaim (`organizations`, `audit_records`) ;
- un rôle PostgreSQL actuel avec `BYPASSRLS` ;
- Neon Auth activé sur la branche legacy, avec une API Managed Better Auth et un JWKS `EdDSA` ;
- aucune OAuth Application Clerk et aucune configuration prête pour le SSO OIDC actuel.

## Décision

### 1. Neon est la plateforme PostgreSQL, pas la frontière d’identité primaire

Neon est retenu pour le PostgreSQL managé, le branching de données et les environnements isolés. Une nouvelle base/projet dédié à VeriClaim sera créé ; la base legacy n’est jamais migrée, stampée ou modifiée.

Neon Auth est **différé comme identité primaire de production**. Il peut être réévalué pour les environnements de démonstration, de preview ou un cas Data API explicitement approuvé, mais ne remplace pas l’architecture d’identité de production à ce stade.

Motifs :

- Managed Better Auth / Neon Auth est annoncé comme beta ;
- son intégration privilégie une architecture Next.js full-stack et des cookies HTTP-only ;
- la documentation signale une limitation pour les architectures frontend/backend séparées ;
- le flux OIDC Authorization Code générique existant ne peut pas être remplacé par simple configuration ;
- le support des permissions personnalisées de l’organisation Neon Auth ne couvre pas le RBAC fin VeriClaim ;
- l’entreprise doit pouvoir fédérer à terme ses propres IdP via OIDC et SAML, sans verrouillage de fournisseur.

### 2. L’identité reste fondée sur un contrat OIDC générique

VeriClaim conserve un adaptateur OIDC générique côté backend : Authorization Code + PKCE, validation stricte de l’ID token, JWKS, `iss`, `aud`, nonce et e-mail vérifié.

Aucun fournisseur ne doit être codé en dur dans les règles métier. Le fournisseur de production sera choisi après une revue sécurité, DPA, résidence des données, SAML/SCIM, MFA et SLA. Le premier candidat de marché à évaluer est un fournisseur OIDC B2B géré et compatible avec la résidence européenne ; une alternative auto-hébergée reste requise pour les clients qui l’exigent.

Clerk n’est pas une dépendance architecturale. Neon Auth non plus. Ils restent des fournisseurs possibles, pas des sources d’autorité applicative.

### 3. VeriClaim reste la source d’autorité d’autorisation

Les données suivantes restent dans le modèle VeriClaim :

- utilisateurs locaux liés à `(identity_provider, external_subject)` ;
- organisations ;
- memberships ;
- rôles `owner`, `admin`, `analyst`, `viewer` ;
- permissions explicites ;
- organisation active ;
- sessions opaques et CSRF ;
- événements d’audit append-only ;
- policies PostgreSQL RLS tenant-scoped.

Les claims du fournisseur d’identité ne portent pas les permissions métier. Toute décision d’accès est calculée à partir des memberships VeriClaim en base.

### 4. Un seul origin public est la norme navigateur

La cible de déploiement est :

```text
Navigateur
  │ https://app.<domaine>
  ▼
Next.js / BFF
  ├── /api/*        → FastAPI privé
  └── ressources UI
                         │
                         ▼
                    PostgreSQL Neon
```

L’API peut être isolée sur un réseau privé ou un domaine technique, mais le navigateur utilise l’origin applicatif unique. Cela simplifie les cookies sécurisés, le CSRF, les redirections OIDC et les politiques CORS.

### 5. Neon utilise la séparation stricte migration/runtime

La nouvelle base utilise au minimum deux rôles :

| Rôle | Usage | Interdictions |
|---|---|---|
| `vericlaim_migrator` | migrations Alembic et grants | jamais utilisé par API/workers |
| `vericlaim_app` | API et workers | `NOSUPERUSER`, `NOBYPASSRLS`, non propriétaire |

Le rôle runtime ne peut pas contourner RLS. Il reçoit seulement les droits nécessaires aux tables, séquences et fonctions applicatives. Les default privileges sont définis par le rôle migrateur.

## Conséquences

### Conséquences positives

- SSO entreprise OIDC préservé ;
- trajectoire SAML/SCIM possible pour les grands comptes ;
- RBAC et audit cohérents, même lorsqu’un client change de fournisseur d’identité ;
- RLS PostgreSQL réellement utile au runtime ;
- absence de migration risquée de la base legacy ;
- possibilité d’utiliser Neon branches pour preview, restauration et tests sans mélanger l’identité de production ;
- réduction des dépendances d’identité à un fournisseur spécifique.

### Coûts assumés

- l’intégration OIDC générique doit être exploitée et testée avec un IdP de production ;
- une revue fournisseur est requise avant le go-live ;
- les environnements de preview doivent disposer de leur propre configuration OIDC ou d’un IdP de test ;
- Neon Auth ne sera pas le raccourci d’authentification de production à court terme.

## Positionnement produit associé

VeriClaim se positionne comme un **système d’exploitation probatoire pour les équipes achats, conformité et qualité**, pas comme un moteur de verdict juridique.

Les principes produit non négociables sont :

1. preuve source avant automatisation ;
2. humain responsable de toute validation métier ;
3. citation, hash, provenance et version avant score ;
4. automatisation déterministe explicable ;
5. permissions, isolation et audit avant accélération des workflows ;
6. intégration entreprise avant dépendance à un fournisseur unique.

## Plan d’exécution

### Phase A — Landing zone sécurisée

1. créer un projet Neon dédié à VeriClaim, séparé du legacy ;
2. créer au minimum `production`, `staging` et `development` ;
3. créer les rôles migrateur/runtime ;
4. stocker les secrets uniquement dans le gestionnaire de secrets du déploiement ;
5. appliquer `alembic upgrade head` seulement dans la nouvelle base ;
6. vérifier `d3c8a6e1b409`, RLS forcé et permissions catalogue.

### Phase B — Déploiement applicatif

1. déployer Next.js comme origin public ;
2. déployer FastAPI et les workers sur un réseau privé ou contrôlé ;
3. configurer le proxy `/api/*` ;
4. imposer HTTPS, cookies Secure, CORS explicite et CSP ;
5. configurer sauvegardes, restauration, alerting et journalisation structurée.

### Phase C — Identity provider

1. sélectionner le fournisseur OIDC après revue sécurité/commerciale ;
2. créer l’application OIDC et le callback HTTPS ;
3. vérifier le login, logout, expiration, rotation JWKS, révocation et changement de tenant ;
4. préparer SAML/SCIM comme capacité entreprise ultérieure, sans la simuler.

### Phase D — Différenciation produit

1. valider C6.1 ;
2. livrer C6.2 : registre probatoire persistant et liens claim → preuve manuels ;
3. livrer C6.3 : validation humaine append-only et demandes de pièces ;
4. concevoir ensuite les intégrations achats/ERP, exports contrôlés et rapports versionnés ;
5. ne jamais introduire un LLM comme unique fondement d’une conclusion réglementaire.

## Gates de décision

Les actions irréversibles nécessitent une validation explicite :

- création/suppression de projet ou de branche Neon ;
- migration de production ;
- choix contractuel d’un fournisseur d’identité ;
- activation d’une méthode de connexion utilisateur ;
- modification de la politique MFA, récupération de compte ou rétention ;
- traitement de données réelles de clients.

## Sources à revalider avant exécution

- Neon Auth overview : https://neon.com/docs/auth/overview
- Neon Auth roadmap : https://neon.com/docs/auth/roadmap
- Neon Auth JWT : https://neon.com/docs/auth/guides/plugins/jwt
- Neon Auth Organizations : https://neon.com/docs/auth/guides/plugins/organization
- Neon branch management and recovery documentation
- sécurité et DPA du fournisseur OIDC retenu
