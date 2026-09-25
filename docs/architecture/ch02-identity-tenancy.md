# Chantier 2 — Identité, organisations, RBAC et isolation multi-tenant

**Statut :** livré techniquement.
**Choix validé :** SSO OIDC générique et accès au moteur réservé aux membres authentifiés d’une organisation active.

> VeriClaim AI ne stocke ni mot de passe utilisateur, ni token d’accès/refresh token du fournisseur d’identité. Il établit une session opaque locale après validation cryptographique d’un ID token OIDC.

## 1. Décisions d’architecture

### ADR-007 — OIDC Authorization Code + PKCE, fournisseur agnostique

Le backend implémente le flux OIDC Authorization Code avec PKCE :

1. `GET /api/v1/auth/login` crée `state`, `nonce` et `code_verifier` aléatoires ;
2. une transaction signée, limitée à 10 minutes, est déposée dans un cookie HttpOnly `SameSite=Lax` ;
3. le callback échange le code auprès du `token_endpoint` ;
4. l’ID token est vérifié avec le JWKS du fournisseur : signature asymétrique (`RS*` ou `ES*` seulement), `iss`, `aud`, `exp`, `iat`, `nonce`, `sub` et e-mail vérifié ;
5. une session opaque est créée dans `auth_sessions`, puis le navigateur reçoit seulement un token aléatoire HttpOnly.

Les algorithmes `none` et HMAC sont refusés. Les tokens amont ne sont pas conservés en base. La configuration est compatible avec un fournisseur respectant OIDC Discovery (Azure Entra ID, Auth0, Keycloak, etc.) ; l’intégration avec un tenant réel reste une étape de déploiement.

### ADR-008 — Sessions serveur opaques et protection CSRF

- `vericlaim_session` : cookie HttpOnly, `SameSite=Lax`, `Secure` obligatoire hors développement/test ; son HMAC SHA-256 est stocké, jamais le token brut.
- `vericlaim_csrf` : cookie non HttpOnly `SameSite=Strict`, comparé à l’en-tête `X-CSRF-Token` et à un HMAC serveur.
- Toute mutation cookie-authentifiée (`POST`, `PATCH`, `DELETE` métier) exige le jeton CSRF correspondant.
- `POST /api/v1/auth/logout` révoque la session côté serveur et supprime les deux cookies.
- Les sessions expirent après `AUTH_SESSION_TTL_SECONDS` (8 heures par défaut) et les comptes désactivés sont refusés.

### ADR-009 — Organisation active détenue par la session

L’organisation active est stockée dans `auth_sessions.active_organization_id`, après vérification d’un `Membership` actif. Le client ne peut donc pas imposer un `organization_id` arbitraire au serveur via un body ou un header.

Un utilisateur connecté sans membership passe par l’onboarding de création d’organisation. Un utilisateur avec plusieurs memberships choisit l’organisation active avec `POST /api/v1/auth/active-organization`; le serveur revalide le membership à chaque bascule.

### ADR-010 — RBAC explicite et minimal

Les rôles système sont initialisés de manière idempotente au démarrage pour les environnements `create_all` et existent sous forme de données dans les environnements migrés.

| Rôle | Permissions principales |
|---|---|
| `owner` | paramètres organisation, membres, rôles, analyses, consultation des règles |
| `admin` | membres opérationnels, analyses, consultation des règles |
| `analyst` | lancer et consulter les analyses, consulter les règles |
| `viewer` | consulter les analyses/règles autorisées ; ne peut pas lancer d’analyse |

Contraintes de gouvernance appliquées :

- un `admin` ne peut ni attribuer ni modifier le rôle `owner` ;
- une organisation conserve toujours au moins un `owner` actif ;
- un invite crée un `User` sans mot de passe et un `Membership` `invited` ;
- ce membership devient `active` seulement lorsque cette même adresse e-mail est confirmée par le SSO.

L’envoi d’e-mail d’invitation n’est pas simulé : l’API répond explicitement `delivery_status: not_sent`. Un service d’e-mail/audit de délivrabilité est nécessaire avant d’en faire un parcours autonome.

### ADR-011 — RLS PostgreSQL en défense supplémentaire

La migration `7b3b4c985738_identity_and_tenant_security.py` ajoute la fonction PostgreSQL `vericlaim_current_organization_id()` et active/force RLS sur les tables métier tenant-owned, dont :

- documents, versions, segments, preuves, certificats ;
- analyses, versions, claims, risques, recommandations, validations, rapports ;
- fournisseurs, produits, demandes de preuves ;
- `audit_events` et les nouveaux `audit_records` rattachés à une organisation.

Les politiques imposent :

```sql
organization_id = vericlaim_current_organization_id()
```

`rules` et `rule_versions` autorisent en lecture les règles globales (`organization_id IS NULL`) ainsi que les politiques de l’organisation active ; les écritures via le rôle applicatif ne peuvent créer que des lignes de l’organisation active.

Avant chaque accès HTTP aux données métier tenant-scoped, le backend positionne `app.current_organization_id` avec `set_config(..., true)`. Cette valeur est locale à la transaction et le backend commit/rollback à la fin de la requête, ce qui évite une fuite de contexte entre connexions poolées.

Les tables d’identité (`users`, `organizations`, `memberships`, `roles`, `auth_sessions`) ne reçoivent pas la même policy générique : elles sont nécessaires avant la résolution du tenant. Elles ne sont accessibles qu’à travers les services d’identité et les routes RBAC explicites. Une RLS relationnelle plus fine sur ces tables, avec fonctions `SECURITY DEFINER` auditées, reste une amélioration ultérieure.

SQLite ne fournit pas RLS : il est utilisé uniquement en développement/test explicite. PostgreSQL reste la base partagée cible.

## 2. API livrée

### Publique

| Méthode | Route | Usage |
|---|---|---|
| `GET` | `/api/v1/auth/status` | Savoir si le SSO est configuré, sans révéler de secret. |
| `GET` | `/api/v1/auth/login` | Démarrer la redirection OIDC. |
| `GET` | `/api/v1/auth/callback` | Callback enregistré chez le fournisseur OIDC. |
| `GET` | `/healthz` | Supervision technique. |

### Session / organisation

| Méthode | Route | Autorisation |
|---|---|---|
| `GET` | `/api/v1/auth/me` | Session valide. |
| `POST` | `/api/v1/auth/logout` | Session valide + CSRF. |
| `POST` | `/api/v1/auth/active-organization` | Session valide + CSRF + membership actif. |
| `GET` | `/api/v1/organizations` | Session valide. |
| `POST` | `/api/v1/organizations` | Session valide + CSRF ; crée un membership `owner`. |
| `GET/PATCH` | `/api/v1/organizations/current` | `organization:read` / `organization:manage` (+ CSRF pour PATCH). |
| `GET` | `/api/v1/organizations/current/roles` | `members:read` ; liste des rôles système disponibles. |
| `GET` | `/api/v1/organizations/current/members` | `members:read`. |
| `POST` | `/api/v1/organizations/current/members/invitations` | `members:manage` + CSRF. |
| `PATCH` | `/api/v1/organizations/current/members/{userId}/role` | `members:manage` + CSRF. |
| `DELETE` | `/api/v1/organizations/current/members/{userId}` | `members:manage` + CSRF, avec garde-fous `owner`. |

### Moteur historique désormais protégé

| Route | Permission |
|---|---|
| `POST /api/v1/engine/evaluate` | `audit:run` + CSRF |
| `GET /api/v1/engine/rules` | `rules:read` |

Chaque appel d’analyse crée désormais un `AuditRecord` tenant-scoped et un `AuditEvent` `regulatory_audit.completed`. Le moteur déterministe et ses verdicts ne sont pas modifiés.

## 3. Configuration de déploiement

Les variables suivantes sont obligatoires en staging/production :

```dotenv
DATABASE_URL=postgresql+psycopg://…
AUTH_SESSION_SECRET=<secret-aleatoire-non-placeholder-au-moins-32-caracteres>
AUTH_COOKIE_SECURE=true
FRONTEND_URL=https://app.exemple.eu
CORS_ORIGINS=https://app.exemple.eu
OIDC_ISSUER=https://idp.exemple.eu/realms/vericlaim
OIDC_CLIENT_ID=vericlaim-web
OIDC_REDIRECT_URI=https://app.exemple.eu/api/v1/auth/callback
# OIDC_CLIENT_SECRET=…          # seulement pour un client confidentiel
# OIDC_DISCOVERY_URL=…          # dérivé de OIDC_ISSUER par défaut
```

- `APP_ENV` est limité à `development`, `test`, `staging` ou `production`; une valeur inconnue échoue au démarrage.
- `DATABASE_URL` doit être PostgreSQL en staging/production afin que la RLS soit effectivement disponible; SQLite est refusé dans ces environnements.
- Les URLs doivent être absolues et HTTPS hors développement/test.
- `AUTO_CREATE_SCHEMA=true` est refusé hors développement/test : les environnements partagés passent uniquement par Alembic.
- Les wildcards CORS sont refusés car la session utilise des cookies.
- En développement, OIDC peut être absent : l’UI signale alors explicitement que le SSO n’est pas configuré. Il n’existe aucun bypass local par mot de passe ou header.
- Enregistrer exactement `OIDC_REDIRECT_URI` chez le fournisseur, avec le même schéma, hôte et chemin.
- Avec la réécriture Next `/api`, utiliser le même hôte frontend pour le callback (`https://app…/api/v1/auth/callback`) afin que les cookies de session et CSRF restent same-origin. Un déploiement sans proxy doit concevoir explicitement son domaine de cookies et son mécanisme CSRF ; ne pas simplement basculer le navigateur vers une API cross-site.
- Utiliser un rôle DB applicatif sans `BYPASSRLS`; le rôle de migration doit être séparé et contrôlé.

## 4. Migration et exploitation

```bash
cd backend
APP_ENV=production \
DATABASE_URL='postgresql+psycopg://…' \
AUTH_SESSION_SECRET='…' \
AUTH_COOKIE_SECURE=true \
FRONTEND_URL='https://app.example.eu' \
CORS_ORIGINS='https://app.example.eu' \
OIDC_ISSUER='https://idp.example.eu' \
OIDC_CLIENT_ID='vericlaim-web' \
OIDC_REDIRECT_URI='https://app.example.eu/api/v1/auth/callback' \
AUTO_CREATE_SCHEMA=false \
alembic upgrade head
```

La chaîne devient :

```text
f3efc9c81c8d  architecture foundation
       ↓
7b3b4c985738  identity and tenant security
       ↓
a4f6b2d8e901  seed system roles
```

Avant migration d’une instance ayant déjà des `audit_records`, sauvegarder la base. La nouvelle colonne `organization_id` est nullable afin de préserver les enregistrements historiques ; ces lignes sans organisation sont invisibles sous la policy RLS. Toute nouvelle analyse authentifiée reçoit une organisation non nulle.

## 5. Frontend livré

- garde de session au chargement ;
- écran de connexion SSO ou message explicite de configuration manquante ;
- création de première organisation ;
- sélection d’organisation active ;
- rôle et organisation visibles dans la barre supérieure ;
- déconnexion ;
- cookies envoyés avec les appels moteur et en-tête CSRF ajouté aux mutations.

## 6. Limites assumées

- Aucun fournisseur OIDC réel n’a été configuré dans ce workspace ; le callback et les vérifications sont testés avec un client simulé et des garde-fous cryptographiques unitaires.
- La migration RLS est rendue et contrôlée en SQL PostgreSQL offline, mais aucun serveur PostgreSQL/Docker réel n’est disponible dans cet environnement.
- Pas de SCIM, SAML, MFA géré par VeriClaim, API keys de service, récupération de compte, ni délivrabilité e-mail d’invitation. Les politiques MFA, verrouillage et limitation de tentatives restent déléguées au fournisseur OIDC et au reverse proxy.
- Les nouvelles ressources métier du catalogue/documents/analyse n’ont pas encore leurs routes CRUD ; leurs tables sont néanmoins déjà couvertes par RLS PostgreSQL.
- La sérialisation forte de chaînes d’audit concurrentes est encore un chantier dédié ; l’implémentation prend un verrou sur le dernier événement quand il existe mais ne prétend pas être une notarisation.
