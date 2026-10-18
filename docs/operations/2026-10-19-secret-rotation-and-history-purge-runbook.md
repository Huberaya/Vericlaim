# Runbook — rotation des identifiants exposés et purge d'historique

- **Date :** 19 octobre 2026
- **Statut :** à exécuter impérativement après l'audit du 25 septembre 2026
  (`docs/audits/2026-09-25-origin-main-branch-audit.md`) et le nettoyage de
  l'arbre courant (commit `security(secrets)` sur la branche de travail).
- **Règle absolue :** ne jamais recopier une valeur de secret dans une issue,
  un commit, un log, une PR ou une conversation. Seuls les emplacements et les
  noms de variables sont documentés ici.

## 1. Ce qui a été purgé (arbre courant)

L'arbre courant ne contient plus aucune valeur de secret codée en dur :

| Emplacement | Nature | Traitement |
|---|---|---|
| `backend/app/core/config.py` | Secret de session de développement fixe | Remplacé par un secret aléatoire éphémère généré à chaque démarrage de processus |
| `docker-compose.yml` | Mot de passe PostgreSQL de dev par défaut | Variable `POSTGRES_PASSWORD` désormais obligatoire (`.env`), sans valeur par défaut |
| `.env.example` | Exemple de DSN avec mot de passe | Exemple sans mot de passe en ligne |
| `backend/tests/*` | Chaînes factices type identifiant | Neutralisées (valeurs explicitement non réelles, hôtes inexistants) |

Vérification : `detect-secrets scan --all-files` ne remonte plus que des faux
positifs inertes (identifiants de révision Alembic, empreintes SHA-256 de
fixtures de tests).

## 2. Ce qui reste exposé — traitement obligatoire

Des identifiants **réels** (URL PostgreSQL avec rôle propriétaire et mot de
passe, clé secrète Clerk) sont encore lisibles dans le dépôt `Huberaya/Vericlaim` :

| Emplacement | Contenu exposé |
|---|---|
| Branche `quarantine/unreviewed-main-be90de9` — `render.yaml` | DSN PostgreSQL (rôle propriétaire), `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `CORS_ORIGINS` |
| Branche `quarantine/unreviewed-main-be90de9` — `.env.example` | `DATABASE_URL`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` |
| Branche `quarantine/unreviewed-main-be90de9` — `backend/app/core/config.py` | Valeurs par défaut identiques (DSN, clé Clerk) |
| Branche `feat/compliance-engine-full` — `.env.example`, `backend/app/core/config.py` | `DATABASE_URL`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` |
| Commits orphelins accessibles par SHA (ex. `be90de97…`) | Historique complet de la lignée rejetée |

Tant que ces identifiants ne sont pas révoqués, leur présence dans Git public
constitue une exposition effective. **Supprimer un fichier ou une branche ne
révoque rien.**

## 3. Ordre d'exécution

### Étape 1 — Rotation des identifiants (avant toute purge Git)

1. **Neon PostgreSQL**
   - Changer le mot de passe du rôle `neondb_owner` (ou recréer la base sur une
     nouvelle branche Neon et basculer).
   - Recréer/rotater les rôles applicatifs avec le script audité
     `infra/neon/01_bootstrap_roles.sql` (variables `migrator_password` et
     `app_password` fournies par le secret manager, jamais en clair).
   - Mettre à jour `DATABASE_URL_MIGRATOR` et `DATABASE_URL_APP` dans les
     environnements GitHub `staging`/`production`.
2. **Clerk**
   - Révoquer la clé secrète concernée (`sk_…`) et la clé publique associée,
     ou supprimer l'instance si elle n'est plus utilisée.
   - Ne jamais réintroduire de clé Clerk dans le dépôt : l'architecture
     retenue est un contrat OIDC générique (voir
     `docs/architecture/adr/0001-identity-and-data-platform.md`).
3. **Render / Railway**
   - Révoquer tout déploiement lié à la lignée rejetée et purger les variables
     d'environnement compromises côté provider.
4. **GitHub Actions**
   - Auditer les secrets des environnements `staging`/`production`
     (`DATABASE_URL_MIGRATOR`, `DATABASE_URL_APP`, tout autre secret) et les
     faire tourner si les valeurs actuelles dérivent des identifiants exposés.

### Étape 2 — Préservation des preuves

- Conserver une copie locale de cette évaluation (les branches restent en
  place jusqu'à la fin de la rotation) ; ne pas supprimer
  `quarantine/unreviewed-main-be90de9` avant l'étape 3.

### Étape 3 — Purge de l'historique (uniquement après rotation)

1. Supprimer les branches `quarantine/unreviewed-main-be90de9` et
   `feat/compliance-engine-full` (ou les réécrire pour retirer les fichiers
   compromis, puis force-push).
2. Purger les refs de PR de la lignée rejetée.
3. Contacter le support GitHub pour le garbage collection des commits
   orphelins restants téléchargeables par SHA (jusqu'alors, traiter les
   identifiants comme compromis).
4. Vérifier avec un scan (`detect-secrets` / `gitleaks`) que plus aucune ref
   du dépôt ne remonte de valeur réelle.

### Étape 4 — Vérification finale

```bash
git fetch --all --prune
git for-each-ref --format='%(refname)' | while read -r ref; do
  git grep -lE 'npg_[A-Za-z0-9]|sk_(test|live)_[A-Za-z0-9]|postgres(ql)?://[^:]+:[^@]+@' "$ref" --
done
```

Aucune sortie attendue.

## 5. Journal d'exécution — étape 3

**Exécutée le 19 octobre 2026, après confirmation de la rotation par le
propriétaire du dépôt.**

| Action | Résultat |
|---|---|
| Suppression de `quarantine/unreviewed-main-be90de9` | ✅ supprimée (`94409fb1…`) |
| Suppression de `feat/compliance-engine-full` | ✅ supprimée (`9fb790bc…`) |
| Ancestrité vérifiée avant purge | ✅ aucun tip infecté n'était ancêtre d'une branche conservée ni d'une ref `refs/pull/*` |
| Point de fork de la lignée rejetée (`47c8e782`) | ✅ aucun marqueur d'identifiant réel |
| Tags distants | aucun (pas d'ancre alternative) |
| Scan des 9 branches restantes + 7 refs de PR | ✅ aucune valeur réelle détectée (motifs DSN à mot de passe, `sk_…`, `npg_…`) |
| Preuve d'exposition | copie locale hors dépôt : `/home/user/vericlaim-evidence/2026-10-19-branch-purge-evidence.md` (chemins, noms de variables et SHAs uniquement) |

### Point non résolu : rétention des commits par GitHub

Après suppression des branches, les commits restent **accessibles par SHA
direct** via l'API et l'interface GitHub tant que le garbage collection n'a pas
eu lieu (rétention des objets non atteignables). Test effectué le 19/10/2026 :
`GET /repos/Huberaya/Vericlaim/commits/<sha>` renvoie encore `200`.

Cela n'a plus d'impact **si et seulement si** la rotation de l'étape 1 est
effective : les valeurs exposées sont alors mortes. Pour supprimer la vue
cache, ouvrir une demande auprès du support GitHub (« remove cached views /
unreachable commits from a public repository »), en citant le dépôt et les
SHAs — **jamais** les valeurs. Modèle :

> Bonjour,
> Le dépôt public `Huberaya/Vericlaim` a exposé des identifiants dans des
> branches depuis supprimées (`quarantine/unreviewed-main-be90de9`,
> `feat/compliance-engine-full`) et dans des commits désormais non atteignables.
> Les identifiants ont été révoqués et rotés le 19/10/2026.
> Merci de purger les vues en cache et les objets non atteignables suivants :
> `be90de97…`, `94409fb1…`, `9fb790bc…` (et leurs ancêtres).
> Aucune valeur de secret n'est jointe à cette demande.

Sans cette demande (ou sans attente du cycle de GC), l'historique reste
téléchargeable par SHA. La révocation reste la seule protection réelle.
