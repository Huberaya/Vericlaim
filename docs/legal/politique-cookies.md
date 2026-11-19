# Politique de cookies

**Ce document décrit les cookies réellement posés par l'application**, vérifiés dans le
code (`backend/app/identity/security.py`, `backend/app/api/v1/auth.py`), et non un
inventaire générique.

## Cookies posés

| Nom | Rôle | Durée | Attributs | Consentement |
| --- | --- | --- | --- | --- |
| `vericlaim_session` | Session authentifiée (jeton opaque, stocké haché côté serveur) | durée de la session (`AUTH_SESSION_TTL_SECONDS`) | `HttpOnly`, `SameSite=Lax`, `Secure` selon `AUTH_COOKIE_SECURE` (**obligatoirement vrai en production**) | **Non** : strictement nécessaire |
| `vericlaim_csrf` | Jeton anti-CSRF, à renvoyer dans l'en-tête `X-CSRF-Token` | idem session | `HttpOnly=false` (le client doit le lire), `SameSite=Strict` | **Non** : strictement nécessaire |
| `vericlaim_oidc_*` | État de la transaction OIDC pendant un login SSO (anti-rejeu) | quelques minutes | `HttpOnly`, `SameSite=Lax` | **Non** : strictement nécessaire |

## Ce qu'il n'y a pas

- **Aucun cookie de mesure d'audience, de publicité ou de réseau social.** Aucune
  bibliothèque de ce type n'est présente dans le frontend (`frontend/package.json`) et
  aucun script tiers n'est chargé par les pages.
- **Aucun bandeau de consentement**, donc. Un bandeau qui demanderait l'accord pour des
  cookies nécessaires serait un faux choix ; en ajouter un pour des traceurs qui
  n'existent pas serait pire.

Le stockage local du navigateur (`localStorage`) est utilisé pour l'affichage
(préférences d'interface, contenu de démonstration côté client) et ne sert à aucune
identification : les identifiants de session ne sont jamais accessibles en JavaScript.

## Si un traceur est ajouté plus tard

Toute addition d'un cookie ou d'un traceur non nécessaire rend cette page fausse et
exige : un bandeau de consentement conforme (accord préalable, refus aussi simple que
l'acceptation, possibilité de retirer son choix), la mise à jour de ce document, et la
mise à jour du registre des traitements (`docs/legal/registre-des-traitements.md`).

---

*Vérification à refaire à chaque évolution du frontend : `grep -rn "document.cookie\|localStorage\|sessionStorage" frontend/src`. Dernière vérification : 30/09/2026 — aucun cookie posé par le frontend lui-même.*
