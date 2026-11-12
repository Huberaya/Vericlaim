# Validation et reproductibilité

## Exécution locale

Depuis `backend/`, dans un environnement Python compatible :

```sh
python -m pip install --require-hashes -r requirements.lock.txt
export APP_ENV=test DATABASE_URL=sqlite:////tmp/vericlaim-test.db AUTO_CREATE_SCHEMA=true
export AUTH_SESSION_SECRET='test-only-session-secret-not-a-production-credential'
# Pour couvrir les tests PostgreSQL, fournir une base dédiée et jetable :
export VERICLAIM_TEST_POSTGRES_URL='<url-de-base-postgresql-de-test>'
pytest -q
ruff check app tests  # ruff 0.17.4 ; installé séparément
```

Ne pas pointer les tests vers des données réelles. Sans URL PostgreSQL, 27 tests d'intégration PostgreSQL sont ignorés. `AUTH_DEV_LOGIN_ENABLED` était non défini lors des suites complètes. Depuis `frontend/` : `npm ci`, `npm run typecheck`, `npm run build`, `npm audit --audit-level=moderate`. Audit Python : `pip-audit -r requirements.lock.txt --require-hashes` avec pip-audit installé séparément.

## Résultats du 11 novembre 2026

Suite complète avec PostgreSQL 16.2 : 264 passés, aucun ignoré ; Ruff ciblé propre ; build et vérification TypeScript propres. Le lock installé séparément a donné 237 passés et 27 ignorés. Audits des dépendances sans vulnérabilité connue au moment de leur exécution et `pip check` propre. Ces résultats sont locaux, pas ceux d'une CI distante sur le commit final.

Non testés dans ce sandbox : image Docker, intégration bout en bout ClamAV/Tesseract/S3, fournisseur OIDC réel, déploiement Vercel, E2E navigateur, sauvegardes/restauration, charge et sécurité indépendante. La CI ajoute un build et smoke test du conteneur ; leur succès n'est pas présumé tant que le job n'a pas tourné.

Le périmètre Ruff obligatoire est `E4,E7,E9,F,B` ; des avertissements existent dans des règles supplémentaires non activées. Les alertes de dépréciation Starlette/httpx et `HTTP_422_UNPROCESSABLE_ENTITY`, ainsi qu'un avertissement de configuration de module Tailwind, restent à traiter.


## Addendum distant du 12 novembre 2026

La qualification ne repose plus uniquement sur les résultats locaux du 11 novembre :
- [CI distante sur le SHA candidat](https://github.com/Huberaya/Vericlaim/actions/runs/43259466287) : cinq jobs réussis, dont PostgreSQL tenant isolation (RLS) sur PostgreSQL 16.
- [Workflow Neon staging](https://github.com/Huberaya/Vericlaim/actions/runs/43272249768) : précontrôle, migration Alembic, grants runtime et vérification read-only sous `vericlaim_app` réussis.
- Déploiement Vercel Preview READY au SHA candidat `115c1863ce02496180b8b31a2c3a7ff4844ef163`.

Le détail des assertions, limites et commandes de reprise est dans [09-qualification-recette-neon-preview.md](09-qualification-recette-neon-preview.md). La Preview n'a pas passé de parcours navigateur E2E : sa protection Vercel SSO n'a pas été contournée. Les services OIDC, S3/ClamAV/OCR réels, sauvegarde/restauration et revue juridique restent hors de cette qualification.
