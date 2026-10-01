# Sécurité — VeriClaim AI

Ce document décrit les règles de sécurité **opposables** du dépôt : ce qui ne doit
jamais atteindre un environnement partagé, comment signaler une vulnérabilité, et
les garde-fous automatiques qui empêchent une régression.

Il ne remplace pas une revue de sécurité formelle. Il existe parce qu'une faille
critique (contournement total de l'authentification) a été introduite en une seule
_merge request_ sans qu'aucun contrôle ne l'arrête.

---

## 1. Règle fondamentale

> **Aucun chemin de code ne doit créer une session, un jeton ou un accès sans
> identité vérifiée, sauf s'il est explicitement listé et justifié.**

La seule exception actuelle est `POST /api/v1/auth/dev-login` (raccourci pilote
local), qui est :

- désactivée par défaut hors de `development`/`test` ;
- **refusée au démarrage** en `staging`/`production` (`ENABLE_DEV_LOGIN=true` lève une erreur) ;
- non enregistrée dans le routeur dans ces environnements ;
- absente du bundle frontend de production (éliminée à la compilation) ;
- protégée par un test de propriété qui échoue si une nouvelle route `/api/v1/auth`
  répond à un appelant anonyme sans figurer dans une liste blanche explicite.

---

## 2. Interdits en production (échec au démarrage ou refus de requête)

Le démarrage échoue si l'une de ces conditions est violée. C'est délibéré : un
échec bruyant vaut mieux qu'une exposition silencieuse.

| Interdit | Contrôle | Conséquence |
|---|---|---|
| `APP_ENV` absent sur une plateforme partagée (`VERCEL_ENV`/`VERCEL`) | `config.py` | Refus de démarrer : le défaut `development` rouvrirait la route pilote, pointerait sur SQLite et désactiverait les cookies sécurisés |
| `APP_ENV` incohérent avec `VERCEL_ENV` (ex. `development` sur `production`) | `config.py` | Refus de démarrer |
| `ENABLE_DEV_LOGIN=true` hors `development`/`test` | `config.py` | Refus de démarrer |
| `ALLOW_LOCAL_SQLITE_FALLBACK=true` hors `development`/`test`, ou sur une plateforme | `config.py` | Refus de démarrer |
| Base de données injoignable | `database.py` + `main.py` | **HTTP 503 explicite** — jamais de base de substitution |
| `AUTO_CREATE_SCHEMA=true` hors `development`/`test` | `config.py` | Refus de démarrer |
| `DATABASE_URL` non PostgreSQL hors `development`/`test` | `config.py` | RLS PostgreSQL non applicable → refus |
| `AUTH_SESSION_SECRET` absent, < 32 caractères ou placeholder | `config.py` | Refus de démarrer |
| `AUTH_COOKIE_SECURE=false` hors `development`/`test` | `config.py` | Refus de démarrer |
| CORS `*` ou origine non HTTPS hors `development`/`test` | `config.py` | Refus de démarrer |
| OIDC incomplet | `config.py` | Refus de démarrer |
| `DOCUMENT_STORAGE_BACKEND` ≠ `s3` hors `development`/`test` | `config.py` | Refus de démarrer |
| Chiffrement serveur absent (`DOCUMENT_STORAGE_SSE_MODE=none`) hors `development`/`test` | `config.py` | Refus de démarrer |
| Buckets quarantaine et propre identiques | `config.py` | Refus de démarrer |
| `DOCUMENT_SCANNER_MODE` ≠ `clamav` hors `development`/`test` | `config.py` | Refus de démarrer |
| `DOCUMENT_SCANNER_MODE=test` hors `APP_ENV=test` | `config.py` | Refus de démarrer |
| Un secret, une clé ou un mot de passe dans le dépôt | revue + `.gitignore` | Rotation immédiate du secret exposé |

---

## 3. Invariants de sécurité à ne jamais casser

Ces propriétés sont couvertes par des tests. Une modification qui les casse doit
être traitée comme une régression bloquante.

| Invariant | Test |
|---|---|
| Isolation multi-tenant : A ne voit jamais les données de B | `test_identity_tenancy.py` |
| RLS PostgreSQL activée et forcée sur les tables tenant | migrations + `verify_runtime.py` |
| Toute mutation exige CSRF | `test_identity_tenancy.py` |
| Toute route `/api/v1/auth` non publique refuse un appelant anonyme | `test_dev_login_gate.py` |
| Un fichier n'atteint jamais un parseur avant le verdict antivirus | `test_secure_documents.py` |
| Un antivirus ou stockage indisponible échoue **fermé** (503, aucune promotion) | `test_secure_documents.py` |
| La chaîne d'audit se vérifie via le **chemin d'écriture réel** | `test_audit_chain_integrity.py` |
| L'intégrité du rapport est vérifiable côté serveur | *(C7 — à venir)* |
| Une base injoignable produit un 503, jamais une base vide | `test_runtime_environment_safety.py` |

---

## 4. Zones sensibles — revue de sécurité obligatoire

Toute modification de ces chemins doit être revue au titre de la sécurité, avec
la checklist §5 :

```
backend/app/identity/**          identité, sessions, RBAC, RLS
backend/app/api/v1/auth.py       surface d'authentification
backend/app/core/config.py       garde-fous de configuration
backend/app/core/database.py     connexion, RLS, chaîne d'audit
backend/app/documents/security.py   validation d'entrées non fiables
backend/app/documents/scanner.py    frontière antivirus
backend/app/documents/storage.py    capacités présignées
backend/alembic/versions/**      politiques RLS, contraintes
.github/workflows/**             déploiement et migrations
```

Le workflow `.github/workflows/security-sensitive-paths.yml` échoue si ces
fichiers changent sans le label `security-reviewed` sur la _pull request_.

---

## 5. Checklist de revue de sécurité

À cocher pour toute modification d'une zone sensible :

- [ ] Aucun nouveau chemin ne crée d'identité, de session ou d'accès sans identité vérifiée.
- [ ] Toute nouvelle route est authentifiée **et** autorisée (permission explicite).
- [ ] Toute mutation est protégée par CSRF.
- [ ] Toute requête sur des données tenant est scopée par `organization_id`.
- [ ] Une nouvelle table tenant reçoit une politique RLS dans la migration.
- [ ] Aucune donnée sensible dans un log, un message d'erreur ou une réponse d'API.
- [ ] Aucun secret, jeton ou identifiant en dur.
- [ ] Les entrées non fiables (fichiers, JSON client) sont validées avant usage.
- [ ] Un échec de dépendance externe (base, stockage, antivirus) échoue **fermé**.
- [ ] Un test de non-régression couvre la propriété de sécurité visée.
- [ ] Le test a été **éprouvé par mutation** : il échoue si l'on réintroduit la faille.

---

## 6. Signalement d'une vulnérabilité

**Ne pas ouvrir d'issue publique.**

Contact : `security@vericlaim.ai` *(adresse à confirmer avant l'ouverture
commerciale)*.

Merci d'inclure : description, étapes de reproduction, impact estimé, version ou
commit concerné, et toute preuve de concept. Nous accusons réception sous 72 h
ouvrées et vous tenons informé du traitement.

Délais cibles (à contractualiser avec les premiers clients) :

| Gravité | Prise en charge | Correctif |
|---|---|---|
| Critique (contournement d'authentification, fuite inter-tenant) | 24 h | 72 h |
| Élevée | 72 h | 7 jours |
| Moyenne / faible | 10 jours | prochaine version |

---

## 7. Modèle de menaces (minimal)

**Actifs à protéger**

1. Documents fournisseurs et pièces probatoires (confidentialité, intégrité).
2. Résultats d'analyse et rapports (valeur de preuve).
3. Piste d'audit (opposabilité).
4. Identités et sessions.
5. Isolation entre organisations clientes.

**Frontières de confiance**

- Navigateur (non fiable) → API : session opaque + CSRF + validation stricte.
- Fichier téléversé (non fiable) → quarantaine → antivirus → parseur : jamais de parseur avant verdict.
- Client → rapport : **le client ne doit jamais fournir un verdict** (voir C7).
- Organisation A ↔ Organisation B : RLS PostgreSQL + scoping applicatif.
- Application ↔ infrastructure : secrets par variables, jamais dans le dépôt.

**Menaces principales retenues**

| Menace | Contre-mesure |
|---|---|
| Accès non authentifié | Refus par défaut, test de propriété sur la surface `/auth` |
| Fuite inter-tenant | RLS + scoping + tests A/B |
| Falsification de rapport | Signature serveur + génération depuis l'analyse persistée (C7) |
| Altération de la piste d'audit | Chaîne SHA-256 vérifiable via le writer réel |
| Fichier malveillant | Quarantaine, antivirus fail-closed, validation extension/MIME/signature |
| Perte de données silencieuse | Refus de servir (503) plutôt que base de substitution |
| Déploiement en mode permissif | Cohérence `APP_ENV`/plateforme, échec au démarrage |

**Hors périmètre actuel** (à traiter avant montée en charge) : DLP, sandbox de
contenu actif, extraction structurée de tableaux, SCIM/SAML, WAF applicatif,
détection d'exfiltration, journalisation d'accès base de données.

---

## 8. Ce que ce document ne garantit pas

Une conformité RGPD, une certification, ni une sécurité absolue. Les affirmations
de conformité nécessitent une validation juridique externe (chantiers C12 et C19).
