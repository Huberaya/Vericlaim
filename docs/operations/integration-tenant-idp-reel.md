# Intégration d'un tenant IdP réel — guide de mise en service

**Objet.** Connecter une instance déployée de VeriClaim à un **fournisseur
d'identité OIDC réel** (le tenant Clerk du pilote, ou Azure Entra ID,
Keycloak, Auth0, ou tout fournisseur OIDC Discovery). Le client applicatif
(`backend/app/identity/oidc.py`) est prêt et prouvé ; ce guide décrit la
configuration côté fournisseur et côté application, puis la checklist qui
signe la mise en service.

**Ce qui est déjà prouvé sans tenant réel** — ne pas refaire :

- la matrice protocole complète contre un fournisseur OIDC local réel
  (`backend/tests/test_oidc_identity_matrix.py`, serveur local
  `tools/recette/oidc_stub.py`) : jetons hostiles (expiration, signature,
  issuer, audience, nonce, e-mail non vérifié), anti-rejeu du code, PKCE
  vérifié côté fournisseur, redirect URI non enregistré refusé ;
- le flux complet **par les routes applicatives** (login → consentement →
  callback → session → `/me`) avec le vrai client OIDC
  (`backend/tests/test_oidc_idp_integration.py`) ;
- la **rotation de clés JWKS** : le client re-télécharge le jeu de clés
  quand il rencontre un `kid` inconnu (même fichier) ;
- les garde-fous de configuration : HTTPS obligatoire pour `OIDC_ISSUER`,
  `OIDC_DISCOVERY_URL`, `OIDC_REDIRECT_URI` en staging/production, et
  `OIDC_REDIRECT_URI` sur l'hôte du frontend (cookies same-origin).

## 1. Contrat exigé du fournisseur

| Exigence | Détail |
|---|---|
| Protocole | OIDC Discovery (`/.well-known/openid-configuration`), Authorization Code + **PKCE S256** |
| Scopes demandés | `openid email profile` |
| Jeton d'identité | signature **RS256/RS384/RS512/ES256/ES384/ES512** (jamais HS*, jamais `none`), `iss` = l'issuer épinglé, `aud` = le `client_id`, `nonce` renvoyé, `exp`/`iat`/`sub` présents |
| Claims | `sub` stable et unique ; `email` ; **`email_verified: true` obligatoire** (un e-mail non vérifié est refusé : `401`) ; `name` ou `preferred_username` en display name |
| Client | client **confidentiel** avec secret (`client_secret_basic`) recommandé ; client public + PKCE accepté sans secret |
| Rotation de clés | publication JWKS ; le client gère la rotation automatiquement |

## 2. Configuration côté fournisseur

Dans tous les cas : créer une **application/audience** dédiée, et enregistrer
le redirect URI **exactement** :

```
https://<hôte-frontend-public>/api/v1/auth/callback
```

L'API derrière le frontend (rewrites Next.js) sert la route ; le cookie de
transaction SSO est déposé sur l'hôte frontend, c'est pour cela que la
configuration refuse tout autre hôte.

### Azure Entra ID

1. *App registrations* → nouvelle registration, **Supported account types = single-tenant** (voir limites plus bas).
2. *Authentication* → *Add a platform* → *Web* → *Redirect URIs* :
   `https://<hôte-frontend>/api/v1/auth/callback`.
3. *Certificates & secrets* → nouveau client secret (le secret n'est utilisé que par l'API, jamais par le navigateur).
4. *Token configuration* → *Add optional claims* : `email`, `preferred_username` (ou `given_name`/`family_name`), et vérifier que `email_verified` est émis.
5. Variables : `OIDC_ISSUER=https://login.microsoftonline.com/<TENANT_ID>/v2.0`,
   `OIDC_CLIENT_ID=<Application (client) ID>`, `OIDC_CLIENT_SECRET=<secret>`,
   `OIDC_REDIRECT_URI=https://<hôte-frontend>/api/v1/auth/callback`.

### Keycloak

1. Realm dédié → *Clients* → *Create* : *Client type = OpenID Connect*, *Client authentication = On* (confidentiel).
2. *Valid redirect URIs* : `https://<hôte-frontend>/api/v1/auth/callback`.
3. *Client scopes* → mapper `email` + `email_verified` (par défaut *Always include in ID token* pour les deux).
4. Variables : `OIDC_ISSUER=https://<keycloak>/realms/<realm>`,
   `OIDC_CLIENT_ID=...`, `OIDC_CLIENT_SECRET=...`, `OIDC_REDIRECT_URI=...`.

### Clerk (tenant du pilote)

1. Dashboard Clerk → *API keys* : récupérer l'**Issuer** (`https://<instance>.clerk.accounts.dev` ou domaine custom) et la clé *Secret key*.
2. *Paths* : autoriser `/api/v1/auth/callback` comme callback (ou *Redirect URLs* selon la version du dashboard).
3. S'assurer que les sessions émettent `email` + `email_verified` (claims par défaut Clerk) ; `name` depuis *Name*.
4. Variables : `OIDC_ISSUER=https://<instance>.clerk.accounts.dev`,
   `OIDC_CLIENT_ID=<client id>` (si application OIDC dédiée), `OIDC_CLIENT_SECRET=<secret>`,
   `OIDC_REDIRECT_URI=https://<hôte-frontend>/api/v1/auth/callback`.
   `OIDC_DISCOVERY_URL` se déduit de `OIDC_ISSUER` (`.well-known/openid-configuration`) ; ne le surcharger que si le fournisseur l'exige.

### Auth0 / OIDC générique

1. *Applications* → *Regular Web Application* ; *Allowed Callback URLs* :
   `https://<hôte-frontend>/api/v1/auth/callback`.
2. Vérifier l'émission de `email` + `email_verified` (Actions/Auth0 ne
   garantissent pas `email_verified` sur les connexions sociales — refuser
   ces comptes ou activer une Action de vérification).
3. Variables : `OIDC_ISSUER=https://<tenant>.<region>.auth0.com`, etc.

## 3. Configuration application

```bash
OIDC_ISSUER=https://...            # issuer épinglé, vérifié tel quel (hors / final)
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...             # optionnel (client public + PKCE si absent)
OIDC_REDIRECT_URI=https://<hôte-frontend>/api/v1/auth/callback
OIDC_DISCOVERY_URL=...             # optionnel, déduit de OIDC_ISSUER
```

Côté Vercel (staging/production) : définir les variables dans les
environnements *Preview* et *Production*, avec `AUTH_SESSION_SECRET`,
`FRONTEND_URL`, `CORS_ORIGINS` cohérents (voir
`docs/infrastructure/02-authentification-et-identite.md`). En
staging/production, le démarrage **refuse** un fournisseur non HTTPS et un
redirect URI sur un autre hôte que `FRONTEND_URL`.

## 4. Modèle d'invitation et première connexion

Le SSO ne crée pas d'accès par magie : un utilisateur inconnu est créé au
statut `INVITED` sans membership ; seuls les memberships pré-invités par un
administrateur passent à `ACTIVE` au premier login réussi (`provision_oidc_user`).
Un changement d'e-mail côté IdP exige un rapprochement administrateur (jamais
de prise de possession silencieuse). Pré-inviter les utilisateurs pilotes
**avant** la mise en service.

## 5. Checklist de mise en service

À rejouer contre le **vrai tenant** (les scénarios marqués ✔ sont déjà
couverts en CI sur le fournisseur local) :

1. ✔ `/api/v1/auth/status` → `oidc_configured: true`, `dev_login_enabled: false`.
2. ✔ Login complet depuis le frontend (bouton SSO) → session ouverte, `/me` affiche le bon e-mail.
3. ✔ Refus d'un e-mail non vérifié → `401`, aucune session créée.
4. ✔ Un code d'autorisation rejoué est refusé ; PKCE vérifié côté fournisseur.
5. ✔ Rotation de clés JWKS transparente (scénario automatisé).
6. **Connexion depuis la Preview Vercel** (point ouvert de la recette) : exécuter le parcours réel sur `*.vercel.app`.
7. **Spécificités du fournisseur réel** (point ouvert « tenant Clerk ») :
   claims réellement émis (`email_verified`, `azp` si audience multiple),
   `sub` stable, et test de deux comptes distincts.
8. Journal d'audit : `membership.activated_by_sso` visible pour les
   memberships activés.
9. Entrer les identifiants tenant et la date d'exécution dans le registre de
   recette (`docs/infrastructure/12-registre-anomalies.md` pour toute
   anomalie constatée).

## 6. Dépannage

| Symptôme | Cause probable |
|---|---|
| `503` à `/login` | Discovery inaccessible ou `issuer` ≠ `OIDC_ISSUER` (fournisseur derrière un `common`/alias — voir limites) |
| `401` « e-mail vérifié » | `email_verified` absent ou `false` — configurer le claim côté fournisseur |
| `401` « nonce » | interception/rejeu de transaction — recommencer la connexion |
| `401` « clé de signature » | rotation JWKS non propagée côté fournisseur ou `kid` absent du JWKS |
| `409` au callback | e-mail déjà lié à une autre identité SSO ou changé côté IdP → rapprochement administrateur |
| `403` au callback | compte désactivé dans VeriClaim |
| Cookie absent après callback | `OIDC_REDIRECT_URI` ≠ hôte `FRONTEND_URL` (refusé au démarrage) ou navigateur en HTTP alors que `AUTH_COOKIE_SECURE=true` |

## 7. Limites assumées

- **Issuer épinglé single-tenant** : `OIDC_ISSUER` doit être l'issuer exact du
  tenant. Les autorités multi-tenant Entra (`common`, `organizations`)
  annoncent un issuer templaté `{tenantid}` : non couvertes par conception —
  dédier un tenant ou faire évoluer le client (validation `tid`/`azp`) si le
  besoin multi-tenant B2B est confirmé.
- **Pas de SLO fédéré** : la déconnexion est locale (session VeriClaim) ;
  aucune déconnexion IdP n'est propagée.
- **IdP-initiated login non supporté** : seul le parcours SP-initié
  (`/api/v1/auth/login`) est accepté.
- Le **binding User-Agent de session** (E-07) s'applique aussi aux sessions
  SSO : changer d'outil invalide le jeton, il suffit de se reconnecter.
