# 02 — Authentification et identité

**Date :** 12 novembre 2026
**SHA testé :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`
**Environnement :** application réelle servie par Uvicorn + fournisseur OIDC réel local
**Responsabilité opérationnelle Clerk / Neon :** assistant ChatGPT (voir §7)

## 1. Ce qui est réellement implémenté

Le produit **n'utilise pas Clerk**. L'ADR-0001
(`docs/architecture/adr/0001-identity-and-data-platform.md`) le dit explicitement :
« Clerk n'est pas une dépendance architecturale […] Ils restent des fournisseurs
possibles, pas des sources d'autorité applicative. »

Le mécanisme réel est un **client OIDC générique** (`backend/app/identity/oidc.py`) :

- Authorization Code + **PKCE S256** ;
- `state` et `nonce` générés côté serveur (`secrets.token_urlsafe(32)`), liés par
  un cookie signé `vericlaim_oidc_transaction` (itsdangerous, TTL 600 s) ;
- découverte du fournisseur imposée sur `OIDC_ISSUER`, vérification que l'`issuer`
  annoncé correspond ;
- signature validée contre le JWKS, algorithmes autorisés limités à
  RS256/384/512 et ES256/384/512 (HS* et `none` impossibles) ;
- `audience`, `issuer`, `exp`, `iat`, `sub` requis, `leeway` de 60 s ;
- `nonce` comparé en temps constant (`hmac.compare_digest`) ;
- `azp` vérifié si l'audience est multiple ;
- e-mail **vérifié obligatoirement** (`email_verified`), sinon refus ;
- provisionnement lié au couple (`issuer`, `subject`), jamais à l'e-mail seul —
  une prise de contrôle par changement d'e-mail est refusée.

Côté session, rien n'est fait confiance au frontend : le cookie contient un jeton
opaque de 256 bits, la base stocke seulement son HMAC-SHA256
(`auth_sessions.token_hash`), et chaque mutation par cookie exige un jeton CSRF
double-submit très comparé au HMAC stocké.

## 2. Preuves : matrice exécutée

Toutes les lignes ci-dessous sont produites par `tools/recette/e2e_recette.py` et
reprises dans `.recette/last-run.json`. Le fournisseur OIDC est un vrai serveur
HTTP : `OidcClient` fait réellement la découverte, récupère le JWKS, échange le
code et vérifie la signature. **Aucun client n'est remplacé par un objet fictif.**

| # | Scénario | Résultat attendu | Observé |
|---|---|---|---|
| 1 | `/healthz` | 200 | **200** |
| 2 | `/api/v1/auth/status` | OIDC configuré, `dev-login` fermé | **`oidc_configured: true`, `dev_login_enabled: false`** |
| 3 | `POST /api/v1/auth/dev-login` | inaccessible | **404** |
| 4 | `GET /api/v1/auth/me` sans session | refus | **401** |
| 5 | `POST /api/v1/organizations` sans session | refus | **401** (et non 403, ce qui ne divulguerait rien mais tromperait) |
| 6 | `/api/v1/auth/callback` sans cookie de transaction | refus | **400** |
| 7 | `callback` avec `state` substitué | refus | **400** |
| 8 | jeton **expiré** | refus | **401** |
| 9 | **signature invalide** (clé non publiée) | refus | **401** |
| 10 | **issuer** étranger | refus | **401** |
| 11 | **audience** étrangère | refus | **401** |
| 12 | **nonce** rejoué d'une autre connexion | refus | **401** |
| 13 | e-mail **non vérifié** par l'IdP | refus | **401** |
| 14 | connexion valide | 303 vers le frontend | **303 → http://localhost:3000/** |
| 15 | session lisible après connexion | 200 | **200** |
| 16 | identité liée à (`issuer`, `subject`) | e-mail attendu | **`alice.recette@example.com`** |
| 17 | mutation **sans jeton CSRF** | refus | **403** |
| 18 | code d'autorisation rejoué | refus | **401** (test `test_oidc_identity_matrix.py`) |
| 19 | PKCE `code_verifier` falsifié | refus | refusé par l'IdP puis par le client |
| 20 | `redirect_uri` non enregistré | jamais utilisé | **400**, aucun `Location` |
| 21 | issuer découverte ≠ issuer configuré | refus | `OidcProtocolError` |
| 22 | `http://` comme issuer en staging | refus au démarrage | **RuntimeError** |
| 23 | déconnexion | session révoquée côté serveur | 204 puis `/auth/me` → 401 |
| 24 | session expirée côté serveur | refus | 401 |
| 25 | redémarrage complet du processus | session survit (elle est en base) | **200** |

Ces 25 points sont rejoués automatiquement par la CI (`backend/tests/test_oidc_identity_matrix.py`
et le job `recette-qualification`).

### Une limite rencontrée qui est en fait un garde-fou

Mon premier jeu de données utilisait `@vericlaim.test`. Le backend a refusé la
connexion : `email_validator` classe `.test` parmi les noms réservés. C'est le
comportement correct — la preuve happy-path utilise donc `@example.com`, domaine
de documentation RFC 2606, qui ne peut jamais recevoir de courrier. À noter pour
l'assistant : **un IdP configuré avec des utilisateurs en `.test` / `.invalid` /
`.localhost` ne pourra jamais se connecter à VeriClaim.**

## 3. Ce que le backend ne fait pas confiance

| Donnée | Origine | Vérification |
|---|---|---|
| identité | jeton OIDC | signature, issuer, audience, exp, nonce, e-mail vérifié |
| session | cookie | HMAC stocké en base, expiration et révocation vérifiées à chaque requête |
| organisation active | `auth_sessions.active_organization_id` **en base** | jamais lue depuis un en-tête ou un paramètre client |
| rôle | `memberships` + `roles` en base | permissions calculées côté serveur |
| jeton CSRF | en-tête + cookie | double-submit + HMAC stocké |
| identifiant de tenant | variable de session | RLS PostgreSQL en défense en profondeur |

Il n'existe **aucune** route qui accepte un en-tête `X-User-Id`,
`X-Organization-Id` ou un JWT « déjà vérifié par le frontend ». Recherche
exhaustive dans `backend/app/api`.

## 4. Ce qui n'a pas pu être testé

| Point | Raison | Conséquence |
|---|---|---|
| Tenant **Clerk** réel | `api.clerk.com` et `*.clerk.accounts.dev` non joignables | Le flux Clerk exact (claims, `azp`, rotation de clés) n'est pas certifié. Le contrat OIDC générique, lui, est prouvé. |
| Connexion depuis la Preview SSO | `*.vercel.app` non joignable | Le parcours navigateur sur la Preview n'a pas été exécuté. |
| Rotation de clé JWKS en production | nécessite un IdP contrôlé | Le code prévoit le re-fetch (`self._jwks = None`) mais le scénario n'a pas été exécuté ici. |
| Expiration réelle sur 8 h | TTL par défaut | L'expiration a été prouvée par manipulation de la ligne `auth_sessions` (test existant), pas par attente réelle. |

## 5. Points de vigilance relevés (non bloquants)

1. **`user_agent_hash` n'est ni vérifié ni mis à jour** à la résolution de session.
   Le nom suggère un binding ; il n'en existe pas. Ce n'est pas une faille (le
   jeton opaque protège la session), mais c'est un champ trompeur : je ne l'ai pas
   transformé en contrôle car un binding UA strict casse les mises à jour de
   navigateur et n'apporte rien face au vol de cookie. À documenter ou à renommer.
2. **Aucune limitation de débit** sur `/auth/login` ni `/auth/callback` (l'anti-
   rejeu repose sur `state`/`nonce` à usage unique). À traiter avant exposition
   publique large : P3.
3. **`dev-login` reste présent dans le code** mais doublement verrouillé :
   refusé si `is_production_like`, refusé si `AUTH_DEV_LOGIN_ENABLED` est faux, et
   désormais refusé sur toute plateforme managée sans base PostgreSQL. Le risque
   résiduel est une erreur de configuration — ce qui est le cas pour toute
   fonctionnalité de ce type.

## 6. Verdict de la phase

- Le backend **valide cryptographiquement** l'identité et ne se fie jamais au
  frontend. Les six scénarios négatifs obligatoires de la mission sont couverts et
  verts.
- Un utilisateur réel de recette peut s'authentifier **sur la pile démontrée**.
- L'authentification **sur la Preview Vercel elle-même** n'est pas exécutée ici :
  SSO, réseau restreint. C'est un constat, pas une dérogation.

## 7. Demandes à l'assistant (Clerk)

À confirmer par l'assistant, sans jamais transmettre de secret dans un message :

1. Existe-t-il un tenant Clerk réellement utilisé par la Preview ? Si oui :
   - type d'application (public/confidentiel) et `client_id` ;
   - `issuer` exact et URL de découverte ;
   - `redirect_uri` enregistrée (doit être exactement
     `https://<preview-host>/api/v1/auth/callback`) ;
   - algorithmes de signature activés ;
   - claims exposés (`email`, `email_verified`, `name`) et leur format ;
   - politique de session / durée de vie du jeton.
2. Si aucun tenant n'existe : la recette doit rester « OIDC non configuré », auquel
   cas `/auth/status` renvoie `oidc_configured: false` et le parcours
   d'authentification n'est **pas** démontrable sur la Preview. Merci de le
   confirmer explicitement pour que le verdict de commercialisation soit juste.
3. Vérifier que les utilisateurs de recette utilisent un domaine recevable par
   `email_validator` (voir §2) : ni `.test`, ni `.invalid`, ni `.localhost`.
4. Ne jamais activer `AUTH_DEV_LOGIN_ENABLED` sur la Preview : le garde de
   démarrage refuse déjà ce cas, mais la variable ne doit pas exister du tout.
