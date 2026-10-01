# Runbook — sauvegarde, restauration et retour arrière

Ce document décrit la seule procédure de sauvegarde testée sur ce schéma. Il ne remplace pas la
sauvegarde managée de Neon (PITR) : il la complète, parce qu'un PITR fourni par un prestataire
n'a jamais été **exercé** ici, et que « nous avons des sauvegardes » sans exercice est une
croyance, pas un contrôle.

La répétition est outillée : `scripts/ci/rehearse_restore.py`. Elle a été exécutée sur PostgreSQL
17 le 30/09/2026, puis intégrée à la CI, sur une base jetable, sans aucun secret.

> Aucun DSN, mot de passe ou clé ne doit figurer dans ce document, dans une issue ou dans un
> journal de workflow.

## Identités

| Rôle | Ce qu'il peut faire | Ce qu'il ne peut pas faire |
|---|---|---|
| `vericlaim_app` | servir les requêtes, sous RLS | sauvegarder, migrer, écrire du DDL |
| `vericlaim_migrator` | appliquer la chaîne Alembic, accorder les droits runtime, **restaurer** | lire les données hors RLS, sauvegarder |
| `vericlaim_backup` | **sauvegarder** (`BYPASSRLS`, lecture seule) | écrire, migrer, servir |

Trois rôles, trois métiers. La restauration appartient au rôle propriétaire de la base cible, parce
que depuis PostgreSQL 15, écrire dans le schéma `public` d'une base qu'on ne possède pas est refusé
(`permission denied for schema public`) — refus rencontré puis corrigé pendant la répétition.

## Droits du rôle de sauvegarde

Le rôle de sauvegarde doit lire **les tables et les séquences** : un `pg_dump` échoue sinon sur
`permission denied for sequence audit_records_sequence_seq`, après avoir déjà produit un fichier
partiel. Les droits sont accordés une fois par l'exploitant, puis vérifiés par la répétition :

```sql
GRANT USAGE ON SCHEMA public TO vericlaim_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO vericlaim_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO vericlaim_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE vericlaim_migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO vericlaim_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE vericlaim_migrator IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO vericlaim_backup;
```

Le contrôle préalable fait partie de la répétition : elle refuse de commencer si le rôle de
sauvegarde n'a pas `BYPASSRLS` ou ne lit pas toutes les tables et séquences, et le dit en clair.

## Procédure

1. **Contrôler le rôle de sauvegarde** (étape 0 de la répétition) : `BYPASSRLS` actif, lecture de
   toutes les tables et de toutes les séquences.
2. **Sauvegarder** depuis un hôte disposant de `pg_dump` de la même version majeure que le serveur :

   ```bash
   pg_dump "$DATABASE_URL_BACKUP" --format=custom --no-owner --no-privileges \
     --file=vericlaim-$(date -u +%Y%m%dT%H%M%SZ).dump
   ```

   `--no-owner --no-privileges` est délibéré : le dump ne transporte pas d'identités ni de
   privilèges, donc il ne peut pas en ressusciter de mauvais. En contrepartie, **les grants
   doivent être rejoués après restauration** (étape 4).
3. **Restaurer** dans une base **éphémère**, jamais directement sur une base servant du trafic :

   ```bash
   createdb -O vericlaim_migrator vericlaim_restore_rehearsal
   pg_restore --dbname "$DATABASE_URL_MIGRATOR_REHEARSAL" --no-owner --no-privileges \
     --exit-on-error vericlaim-....dump
   ```
4. **Rejouer les grants runtime** : `python infra/neon/run_post_migration_grants.py`
   (avec `DATABASE_URL_MIGRATOR` pointant sur la base restaurée).
5. **Vérifier** : `python infra/neon/verify_runtime.py --expected-revision <tête>` — rôle runtime,
   RLS activée et forcée, moindre privilège, tête Alembic.
6. **Test de fumée du produit** : lire une organisation et vérifier la chaîne d'audit restaurée
   avec le code du produit (`app.audit.service.verify_audit_chain`). Une base restaurée dont la
   chaîne ne vérifie plus n'est pas une sauvegarde, c'est un dommage.

En une commande, exactement ce que la CI exécute :

```bash
python scripts/ci/rehearse_restore.py \
  --source-url "$DATABASE_URL_MIGRATOR" \
  --backup-url "$DATABASE_URL_BACKUP" \
  --admin-url "$DATABASE_URL_MIGRATOR" \
  --target-db vericlaim_restore_rehearsal \
  --app-password-env VERICLAIM_APP_PASSWORD \
  --json /tmp/vericlaim-rehearsal.json
```

## Retour arrière

Le retour arrière de ce produit **n'est pas** un `alembic downgrade`. Un downgrade rejoue du DDL à
l'envers sur des données vivantes : les migrations de ce dépôt ne sont pas toutes réversibles, et
certaines portent des données de référence. La procédure retenue est la seule qui ait été répétée :

1. **Geler** les déploiements applicatifs et prévenir les utilisateurs concernés.
2. **Identifier** l'état réel : `python -m alembic current` avec le rôle de migration.
3. **Détruire et reconstruire** la base cible depuis la dernière sauvegarde vérifiée :

   ```bash
   dropdb --if-exists --force vericlaim
   createdb -O vericlaim_migrator vericlaim
   pg_restore --dbname "$DATABASE_URL_MIGRATOR" --no-owner --no-privileges \
     --exit-on-error vericlaim-....dump
   ```

4. **Rejouer les grants** (étape 4 ci-dessus), puis **vérifier** (étape 5) et **fumer** (étape 6).
5. **Consigner** : horodatage UTC, sauvegarde utilisée, tête obtenue, durée, incident déclencheur.

La perte acceptée est celle des écritures postérieures à la sauvegarde, et elle doit être **dite**
aux utilisateurs, pas découverte par eux.

## Ce que la répétition a mesuré (30/09/2026, PostgreSQL 17.11, schéma réellement migré)

Transcript brut : `audit/C24_evidence_transcript.json` (12 étapes, reproductible par
`scripts/ci/rehearse_restore.py`).

| Étape | Résultat mesuré |
|---|---|
| migration de la source | tête `f63c9a1b7d20`, 1,17 s |
| contrôle du rôle de sauvegarde | `BYPASSRLS` actif, **38/38** tables et **1/1** séquence lisibles |
| données écrites par le produit | 1 document, 3 événements d'audit, chaîne **valide avant** sauvegarde |
| sauvegarde avec le rôle de migration | **échec**, refus par la politique RLS (`analyses`) |
| sauvegarde avec `--enable-row-security` | sauvegarde **réussie**, restauration **réussie**, **0 document, 0 version, 0 segment, 0 événement d'audit** restaurés ; seules les organisations survivent |
| sauvegarde avec le rôle dédié | 205 781 octets, 0,1 s |
| base éphémère et restauration | 0,5 s, aucune erreur |
| grants runtime rejoués | appliqués |
| `verify_runtime.py` | **réussi** : rôle runtime, RLS activée et forcée, moindre privilège, tête attendue |
| test de fumée du produit | organisation lisible, 1 document, **chaîne d'audit valide**, 3 événements |
| comparaison des volumes | identiques sur les 6 tables métier, tête identique |
| retour arrière à blanc | base détruite puis reconstruite depuis la sauvegarde, tête `f63c9a1b7d20` en 0,5 s |

Deux pièges sont donc écrits ici parce qu'ils ont été **mesurés**, pas supposés : une sauvegarde
qui échoue avec un rôle existant, et une sauvegarde qui réussit en perdant les données. Un
opérateur qui ne connaît que le premier croira que le second est un succès.

## Ce que cette procédure ne couvre pas

1. **Le PITR Neon n'a pas été exercé** : aucune infrastructure Neon n'est accessible depuis
   l'environnement d'audit. La procédure managée doit être répétée et consignée de la même façon
   avant la première donnée client — sur une branche Neon isolée, pas en production.
2. **La restauration de MinIO/S3 n'est pas couverte** : les binaires des documents vivent dans le
   stockage objet, pas dans PostgreSQL. Une base restaurée sans ses objets rend des documents
   illisibles. La procédure de sauvegarde objet reste à écrire et à répéter.
3. **Aucun test de restauration partielle** (une table, un locataire) : la restauration est totale.
4. **La répétition tourne sur des données fabriquées** par le script : les volumes réels et les
   temps réels restent à mesurer sur la base de production.
