# Livraison opérationnelle — 20 octobre 2026

## Base et provenance

- Départ local confirmé : `36cd8313434a3b51a2428e676d5e5d8a851ee8e2`, branche imposée `arena/b48349e4-vericlaim`, 35 entrées non commitées préservées.
- Référence distante / PR #10 : `274c6eedab482e0b77e6ed3d44267e0edf0a622d`. Main distant toujours `36cd831`. Aucune branche distante `arena/2672a0ba-vericlaim` annoncée par `ls-remote`.
- Sauvegarde G1 accessible, sommes de contrôle et 35 fichiers vérifiés. Nouveau commit de provenance `3eecfff`, sans prétendre récupérer les anciens commits B/F. Historique de #10 intégré par merge `4a1d707`, aucun historique réécrit. Seul conflit : commande CI PostgreSQL, résolue en conservant les tests documentaires G1.
- Les anciens rapports importés sont historiques ; seules les validations rejouées dans cette livraison font preuve du candidat actuel. PR #7 non intégrée, production et bases partagées non touchées.

## Corrections et preuves rouges → vertes

| Défaut reproduit | Correction et test |
|---|---|
| API d'import annonçait 4 octets comme plafond pour un fichier de 4 octets | Réponse `max_size_bytes=settings.max_upload_bytes` ; signature S3 conserve le plafond plus strict correspondant au fichier déclaré ; dépassement serveur refusé 422 |
| Ancienne tentative d'analyse acceptée après reprise sous le même worker_id | Fencing par attempt_count aux étapes traitement/publication/erreur, relecture ORM fraîche au verrouillage ; entrelacement déterministe durant la détection, tentative courante réussie une seule fois |
| Événements documentaires rejetés par le vérificateur d'audit | Écriture commune au format canonique ; lecture compatible des deux formats historiques sans réécriture ; test ancien format + nouveau format, horodatage monotone et métadonnées altérées rejetées |
| SET LOCAL perdu après commit/rollback PostgreSQL | Restauration du contexte de la Session à chaque transaction ; test runtime réel, nouvelle Session non contaminée |
| `FOR UPDATE` sur audit_events/audit_records refusé par les grants append-only | Verrou transactionnel PostgreSQL par tenant et type de chaîne, sans droit UPDATE ajouté ; tests rôle runtime, deux écritures concurrentes de genesis sérialisées et enveloppes après commit |
| Documents supprimés bloquant la file, archives toujours traitées | Jobs inactifs terminés proprement, aucune publication après désactivation pendant extraction, file saine du même tenant toujours utilisable ; 4 cas rouges puis 6 cas verts |

Commits de corrections : `d74b6a8` (upload/analyse), `4ac6555` (audit/transactions), `96f3afc` (reprise documents). Le dernier commit complète les preuves HTTP et ce relevé. SHA complet livré et résultat CI final consignés dans le message de livraison/PR pour éviter une auto-référence impossible du commit contenant ce rapport.

Les correctifs scanner et extraction présents dans G1 sont conservés : réponses clamd bornées/terminées/strictes, échéance transmission/réception, GET/COPY conditionnels sur ETag et fencing documentaire. Leurs tests ont été rejoués, pas seulement repris des rapports anciens.

## Contrôles exécutés

Depuis la racine :

```sh
APP_ENV=test VERICLAIM_TEST_POSTGRES_URL='postgresql://postgres@/postgres?host=/home/user/Vericlaim/.cache/pgdata' .venv/bin/python -m pytest -q backend/tests
```

Résultat avant dernier commit : **147 passed, zéro skip, code 0**, dont 18 tests PostgreSQL/RLS sur PostgreSQL 16.2 jetable, migrations Alembic sur bases vierges et rôle `vericlaim_app` non propriétaire. Commande rejouée sur le SHA final avant push.

Depuis frontend : `npm ci && npm run typecheck && npm run build && npm audit` : codes 0, Next 16.4.2, **0 vulnérabilité**. Typecheck/build/audit rejoués sur le SHA final. Pas de script lint ou test frontend dédié disponible.

Depuis backend, contre la paire réelle Next/Uvicorn locale, base SQLite jetable et secret local éphémère ignoré par Git :

```sh
APP_ENV=development AUTH_DEV_LOGIN_ENABLED=false \
DATABASE_URL=sqlite:////home/user/Vericlaim/.cache/preview.db \
AUTH_SESSION_SECRET="$(cat ../.cache/preview-session-secret)" \
VERICLAIM_ALLOW_LOCAL_HTTP_SEED=true ../.venv/bin/python -m tests.http_preview_smoke
```

Code 0 : HTML/proxy, refus anonyme 401, dev-login 404, OIDC absent 503, session valide, CSRF 403, création documentaire 201, autre tenant 404, stockage absent 503, révocation et expiration 401. Les identités sont provisionnées directement dans la base jetable : aucune revendication de login OIDC réel. Script complété par vérification de la chaîne d'audit documentaire via HTTP.

Diff final examiné, `git diff --check` et scan des signatures de credentials identique à CI. Deux avertissements de dépréciation Starlette/httpx et HTTP_422 non bloquants. Les assertions de régression ont été observées échouées avant correction ; aucun test pertinent supprimé.

## Livraison et limites

Push uniquement vers la branche de session ; PR #10 mise à jour après tests, jamais de push sur main ni de force push. La CI du SHA poussé doit être réellement observée, pas déduite du vert de l'ancien SHA.

**Jalon : correctifs ciblés prêts pour revue/fusion si CI finale verte. Produit/staging complets non déclarés validés.**

Blocages opérationnels : Docker, MinIO/S3, ClamAV et OCR/Tesseract non disponibles ici ; tests du protocole clamd avec pair TCP simulé, pas un antivirus réel. Aucun accès Neon autorisé fourni, aucune opération Neon. OIDC réel, navigateur E2E et parcours upload → antivirus → OCR → analyse → rapport avec ces services restent à exécuter dans un staging isolé configuré.

Aucune purge physique automatique n'est ajoutée : sans contrôle de rétention/legal hold et stockage réel, supprimer des objets serait dangereux. Les objets privés dérivés dont le commit échoue peuvent encore nécessiter une réconciliation de stockage ; ce point n'est pas déclaré résolu. Les anciens formats d'audit sont vérifiés selon les champs qu'ils signaient historiquement ; leurs métadonnées non signées ne sont pas rétroactivement protégées.
