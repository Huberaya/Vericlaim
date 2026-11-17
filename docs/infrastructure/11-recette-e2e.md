# 11 — Recette de bout en bout (exécution réelle)

**Date :** 12 novembre 2026
**SHA testé :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`
**Résultat :** **54 / 54 PASS**, 0 échec — `.recette/last-run.json`,
`generated_at = 2026-11-12T18:57:42Z`

## 1. Ce que ce parcours est

Un script (`tools/recette/e2e_recette.py`) qui pilote **l'application réelle** par
HTTP, comme le ferait un navigateur — pas un test unitaire, pas un mock :

- vrai serveur `uvicorn` avec l'application `app.main:app` non modifiée ;
- vrai **PostgreSQL 16** avec les rôles revus et RLS actif ;
- vrai **serveur S3** appelé par le client boto3 de production ;
- vrai **écouteur du protocole clamd** ;
- vrai fournisseur OIDC local (découverte, JWKS, jeton signé RS256, `/userinfo`) ;
- **vrais workers** lancés comme processus séparés ;
- vrai serveur **Next.js** pour les contrôles de proxy (voir `04` §5).

Reproduction : `.venv/bin/python -m tools.recette.run` (voir `README.md`).

## 2. Parcours complet, étape par étape

| # | Étape | Résultat |
|---|---|---|
| 1 | `healthz` répond | PASS — processus vivant |
| 2 | `readyz` expose la base et les deux dépendances documentaires | PASS — `database: ok`, `document_storage: configured`, `malware_scanner: configured` |
| 3 | `auth/status` annonce OIDC configuré et dev-login fermé | PASS — `oidc_configured: true`, `dev_login_enabled: false` |
| 4 | dev-login est impossible | PASS — **404** |
| 5 | route protégée sans session | PASS — **401** |
| 6 | mutation sans session | PASS — **401** (et non un 403 trompeur) |
| 7 | callback sans cookie de transaction | PASS — **400** |
| 8 | callback avec `state` substitué | PASS — **400** |
| 9 | jeton OIDC **expiré** | PASS — **401** |
| 10 | jeton OIDC **signature invalide** | PASS — **401** |
| 11 | jeton OIDC **issuer étranger** | PASS — **401** |
| 12 | jeton OIDC **audience étrangère** | PASS — **401** |
| 13 | jeton OIDC **nonce rejoué** | PASS — **401** |
| 14 | jeton OIDC **email non vérifié** | PASS — **401** |
| 15 | connexion SSO valide d'Alice | PASS — **303** vers le frontend |
| 16 | session Alice lisible | PASS — **200** |
| 17 | provisionnement : identité liée à l'issuer **et** au subject | PASS — `alice.recette@example.com` |
| 18 | mutation sans jeton CSRF | PASS — **403** |
| 19 | création organisation A | PASS — **201** |
| 20 | création fournisseur fictif A | PASS — **201** |
| 21 | création produit fictif A | PASS — **201** |
| 22 | création fiche documentaire A | PASS — **201** |
| 23 | **import PDF réel** : quarantaine → scan → promotion → persistance | PASS — **201**, `sha256` et `size_bytes` enregistrés |
| 24 | import refusé : `.exe` | PASS — **422** |
| 25 | import refusé : MIME déclaré incohérent | PASS — **422** |
| 26 | import refusé : exécutable déguisé en PDF | PASS — **422** |
| 27 | import refusé : au-delà de `MAX_UPLOAD_BYTES` | PASS — **422** |
| 28 | **EICAR refusé, jamais promu** | PASS — **422**, upload `rejected` |
| 29 | worker d'extraction : job réclamé, PDF extrait, segments persistés | PASS — `segments=1`, `job=completed` |
| 30 | segments consultables via l'API | PASS — **200**, 1 segment |
| 31 | **document scanné sans moteur OCR** → échec explicite, sans faux succès | PASS — `statut=failed`, `code=extraction_unusable`, `job=failed` |
| 32 | URL signée de téléchargement émise | PASS — **200** |
| 33 | téléchargement via URL signée | PASS — **200**, 1011 octets |
| 34 | objet refusé sans signature | PASS — **403** |
| 35 | analyse mise en file | PASS — **202** |
| 36 | worker de détection : claims détectés et persistés | PASS — `job=completed`, `claims=3` |
| 37 | résultat d'analyse consultable | PASS — **200**, `completed` |
| 38 | **rapport PDF** généré et récupéré | PASS — **200**, 7 943 octets |
| 39 | **dossier probatoire ZIP** généré et récupéré | PASS — **200**, 6 717 octets |
| 40 | chaîne d'audit vérifiée intacte | PASS — **200**, 23 événements |
| 41 | création organisation B | PASS — **201** |
| 42 | B ne voit pas le document de A | PASS — **404** |
| 43 | B ne peut pas télécharger la version de A | PASS — aucune URL inter-tenant |
| 44 | B ne voit pas le fournisseur de A | PASS — **404** |
| 45 | B ne voit pas l'analyse de A | PASS — **404** |
| 46 | B ne peut pas basculer sur l'organisation de A | PASS — **404** |
| 47 | liste documentaire de B vide malgré l'activité de A | PASS — **200**, 0 élément |
| 48–49 | fournisseur et produit fictifs B | PASS — **201** |
| 50 | import documentaire indépendant pour B | PASS — **201** |
| 51 | extraction du document de B | PASS — `segments=1` |
| 52 | **après redémarrage complet** : session, document et isolation conservés | PASS — **200** |
| 53 | second import mis en file, **aucun worker lancé** | PASS — version en file |
| 54 | **worker arrêté : le job reste en file**, aucun succès fabriqué | PASS — `pending` / `queued` |

## 3. Les trois propriétés que ce parcours prouve vraiment

### 3.1 Identité

Un jeton n'est accepté que si **toutes** les conditions tiennent : signature,
issuer, audience, expiration, nonce à usage unique, email vérifié. Six variantes
dégradées sont refusées (étapes 9–14), et le dev-login est fermé (étape 4). Le
rattachement d'identité se fait sur `(issuer, subject)` — pas sur l'email seul
(étape 17).

### 3.2 Isolation

B, authentifié et actif, ne voit **rien** de A : document (404), version (pas d'URL),
fournisseur (404), analyse (404), bascule d'organisation (404), liste vide. Et
l'isolation survit à un redémarrage complet (étape 52). Ce n'est pas un filtre
applicatif : sans contexte tenant, PostgreSQL ne renvoie **aucune** ligne (RLS forcé
sur 26 tables).

### 3.3 Aucun succès fabriqué

Deux étapes portent tout le sérieux du parcours :

- **31** : un PDF scanné, sans moteur OCR disponible, produit `failed` +
  `extraction_unusable` — pas « lu », pas de segments, pas d'analyse possible ;
- **54** : un import sans worker reste `pending` / `queued` — aucune promotion.

C'est exactement la classe de défaut qui aurait rendu une démo convaincante et un
pilote dangereux.

## 4. Ce que ce parcours ne couvre pas

| Non couvert | Pourquoi | Où c'est traité |
|---|---|---|
| Navigateur réel (clics, cookies tiers, redirections SSO externes) | pas de navigateur, Preview sous SSO | `04` §3, `13` |
| Vrai fournisseur OIDC externe | fournisseur local conforme OIDC | `02` |
| Vrai `clamd` | non installable | `06` |
| Chemin `pytesseract` (binaire) | absent | `07` |
| Chevauchement multi-workers concurrents | un worker par file | `08` §5 |
| Charge / performance / latence | hors périmètre | `13` |
| Neon et Vercel réels | réseau bloqué | `03`, `04` |

## 4 bis. Exécution en intégration continue (et son défaut)

Ce parcours ne tourne pas seulement sur une machine de développement : le job
`recette-qualification` l'exécute à chaque push (workflow
`.github/workflows/continuous-integration.yml`).

| Fait observé | Valeur |
|---|---|
| Runs verts de bout en bout | `43325846167`, `43326500112`, `43327153607`, `43327851678`, `43328688825` |
| Runs en échec à l'étape 6 | `43326090494` (9 s), `43326928526` (7 s) |
| Durée d'un parcours réussi | 17–19 s |

Les deux échecs s'arrêtent **trop tôt** pour être des expirations de délai : ce
sont des exceptions levées hors étape. Non expliqués à ce jour, ils sont suivis
comme **A-13** dans le registre. Le parcours local, lui, donne 54/54 de façon
reproductible (dont deux exécutions sous charge).

**Ne pas lire « CI verte » comme « parcours fiable à 100 % ».** Le harnais
publie désormais, en cas d'échec, le type d'exception et la dernière progression,
ce qui rendra le prochain incident diagnosticable sans accès aux logs.

## 5. Reproducibilité

Le parcours crée une base et des buckets **neufs à chaque exécution** dans
`.recette/` (git-ignoré), supprime les objets à la fin, et refuse toute cible hors
de ce répertoire. Deux exécutions successives donnent le même résultat ; les
identifiants diffèrent, ce qui est attendu.

## 6. Verdict

**54/54, dont les contrôles négatifs.** Le parcours couvre l'identité, l'import
sécurisé, l'extraction, la détection, les livrables probatoires, l'audit,
l'isolation inter-tenant, la reprise après redémarrage et l'absence de succès
fabriqué. Ce qui reste hors de portée est listé au §4, sans être maquillé en
succès.
