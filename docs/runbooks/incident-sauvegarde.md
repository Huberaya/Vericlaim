# Runbook — Sauvegarde et restauration

**Déclenché par :** sauvegarde absente, `verify` en échec, ou besoin de
restauration.

## 1. Produire une sauvegarde

```bash
export DATABASE_ADMIN_URL='postgresql://…/vericlaim?sslmode=require'
python tools/backup/vericlaim_backup.py backup --directory /var/backups/vericlaim
```

Produit `backup-<horodatage>/` : `database.dump`, les **octets** des objets
sous `objects/<bucket>/<clé>`, `objects.json` (index lisible) et `manifest.json`
(taille + SHA-256 de chaque pièce, révision Alembic, compteurs par table, et une
entrée par objet avec bucket source, clé, taille, SHA-256, métadonnées).

## 2. Vérifier avant de croire

```bash
python tools/backup/vericlaim_backup.py verify /var/backups/vericlaim/backup-<horodatage>
```

Sortie 0 = intégrité confirmée. **2 = ne pas restaurer** : l'archive est
tronquée ou altérée.

Un contrôle mensuel doit inclure une **restauration réelle**, pas seulement une
vérification de checksums : une archive intègre peut ne pas se restaurer.

## 3. Restaurer dans une cible isolée

```bash
python tools/backup/vericlaim_backup.py restore /var/backups/vericlaim/backup-<horodatage>
```

Le script crée une base **neuve** (`vericlaim_restore_<horodatage>`) et refuse
de restaurer dans une base existante. Il compare révision et compteurs au
manifeste.

Après restauration, réappliquer les grants revus :

```bash
python infra/neon/run_post_migration_grants.py
python infra/neon/verify_runtime.py
```

## 3 bis. Restaurer les objets

```bash
python tools/backup/vericlaim_backup.py restore-objects \
  /var/backups/vericlaim/backup-<horodatage> \
  --destination-bucket <bucket-de-restauration> \
  --prefix restauration-<horodatage>/
```

Règles appliquées par la commande, et pourquoi :

| Règle | Raison |
|---|---|
| destination obligatoire (bucket **et** préfixe) | aucune cible n'est devinée |
| refus si destination = source (même bucket, préfixe vide) | restaurer sur soi écraserait ce qu'on sauvegarde |
| objet identique déjà présent ⇒ sauté | un rejeu ne réécrit rien (idempotent) |
| objet **différent** déjà présent ⇒ refus global, avant écriture | on ne remplace jamais une donnée existante sur la foi d'une archive plus ancienne |
| relit chaque objet écrit et compare le SHA-256 | un `put` réussi n'est pas une preuve de contenu |
| ne supprime jamais rien | la restauration est additive |

Ordre recommandé : **base d'abord**, objets ensuite, puis réconciliation. La
commande ne modifie ni ne supprime aucun objet source ; vérifier les ETags
source avant/après si le doute subsiste.

Reprise après un refus : un refus n'écrit **rien**. Corriger la cause (préfixe
différent, bucket neuf) puis relancer — c'est le comportement voulu.

## 4. Rétention

```bash
python tools/backup/vericlaim_backup.py prune --keep-last 7 --max-age-days 30
```

Le dernier jeu est toujours conservé, même trop ancien : une rétention qui vide
le magasin est une perte de données.

## 5. Ce que ce runbook ne couvre pas

Les sauvegardes **Neon** (PITR, rétention côté fournisseur, chiffrement au
repos) et la réplication du stockage objet managé relèvent de l'équipe de
pilotage. Les objets S3 ne sont **pas** rechargés par le script : seul leur
inventaire est enregistré, ce qui permet de constater une disparition, pas de
la réparer.
