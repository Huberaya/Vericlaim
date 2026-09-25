# Landing zone Neon — VeriClaim

Ces scripts préparent la base PostgreSQL dédiée à VeriClaim. Ils sont conçus pour une **base neuve, européenne et isolée**. Ils ne doivent jamais être exécutés contre la base legacy, une base de développement partagée, ou une base contenant déjà un autre produit.

## Préconditions

- projet Neon dédié à VeriClaim ;
- région de production européenne validée ;
- base vide nommée `vericlaim` ;
- TLS obligatoire ;
- secrets dans un secret manager, jamais dans Git ni dans un fichier `.env` committé ;
- compte propriétaire utilisé seulement pour le bootstrap ;
- les mots de passe des rôles migrateur/runtime générés aléatoirement et distincts.

## Rôles prévus

| Rôle | Utilisation | Garanties |
|---|---|---|
| `vericlaim_migrator` | Job Alembic seulement | non superuser, non runtime, propriétaire des objets créés par Alembic |
| `vericlaim_app` | API et workers | non superuser, `NOBYPASSRLS`, non propriétaire des tables, privilèges minimaux |

Le compte propriétaire Neon ne doit pas être configuré comme `DATABASE_URL` de l’API ou des workers.

## Ordre strict d’exécution

1. Avec le compte propriétaire sur la nouvelle base, exécuter `01_bootstrap_roles.sql`.
2. Créer dans le secret manager :

   ```text
   DATABASE_URL_MIGRATOR=postgresql+psycopg://vericlaim_migrator:…@…/vericlaim?sslmode=require
   DATABASE_URL=postgresql+psycopg://vericlaim_app:…@…/vericlaim?sslmode=require
   ```

3. Lancer Alembic **avec `DATABASE_URL_MIGRATOR`** :

   ```bash
   cd backend
   APP_ENV=development AUTO_CREATE_SCHEMA=false DATABASE_URL="$DATABASE_URL_MIGRATOR" \
     alembic upgrade head
   ```

   Le `APP_ENV=development` du job de migration évite d’exiger des secrets applicatifs non nécessaires à Alembic. Il ne doit jamais être la configuration de l’API de production.

4. Avec `vericlaim_migrator`, appliquer les grants revus :

   ```bash
   DATABASE_URL_MIGRATOR="$DATABASE_URL_MIGRATOR" \
     python infra/neon/run_post_migration_grants.py
   ```

5. Avec `vericlaim_app`, exécuter la vérification lecture seule :

   ```bash
   DATABASE_URL_APP="$DATABASE_URL_APP" \
     python infra/neon/verify_runtime.py --expected-revision d3c8a6e1b409
   ```

6. Vérifier que la révision est `d3c8a6e1b409`, que RLS est activé/forcé et que l’API ne démarre qu’avec le rôle runtime.

## Secrets et shell

Passez les mots de passe à `psql` par variables de session ou via votre gestionnaire de secrets. Ne les mettez jamais en clair dans les scripts.

Exemple conceptuel, à exécuter depuis un environnement de déploiement sécurisé :

```bash
PGPASSWORD="$NEON_OWNER_PASSWORD" psql "$NEON_OWNER_DSN" \
  -v target_database=vericlaim \
  -v migrator_password="$VERICLAIM_MIGRATOR_PASSWORD" \
  -v app_password="$VERICLAIM_APP_PASSWORD" \
  -f infra/neon/01_bootstrap_roles.sql
```

## Posture de sécurité

- `vericlaim_app` ne reçoit pas `BYPASSRLS` ;
- les tables d’audit ne reçoivent pas `UPDATE` ou `DELETE` pour le rôle runtime ;
- les grants sont revalidés après chaque migration qui ajoute une table, séquence ou fonction ;
- toute migration est d’abord répétée sur une base staging vierge ;
- les sauvegardes/PITR et une restauration sont validées avant la première donnée client.
