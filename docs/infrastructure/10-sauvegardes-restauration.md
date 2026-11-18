# 10 — Sauvegardes et restauration

**Date :** 12 novembre 2026
**SHA testé :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`
**Cible d'essai :** instance jetable isolée, jamais une base persistante

## 1. Règle de ce document

> **Ne jamais affirmer qu'une sauvegarde est valide sans restauration testée.**

Conséquence immédiate : la sauvegarde **Neon** n'est **pas** validée ici, parce que
je ne peux ni l'atteindre, ni la restaurer. Ce document distingue donc trois
choses : ce que le dépôt outille réellement, la restauration **réellement
exécutée** sur une cible isolée, et les procédures qui restent à la charge de Neon.

## 2. État réel du produit (*ce qui existe dans le dépôt*)

| Élément | État | Détail |
|---|---|---|
| Sauvegarde outillée | **existe depuis A-08** | `tools/backup/vericlaim_backup.py` : `backup`, `verify`, `prune`, `restore` |
| Sauvegarde **planifiée** | **inexistante** | aucun cron, aucun workflow n'appelle le script : le RPO réel reste « depuis la dernière exécution manuelle » |
| Manifeste et checksums | **existe** | `manifest.json` : SHA-256 et taille de chaque pièce, révision Alembic, compteurs par table |
| Vérification d'intégrité | **existe et testée** | `verify` détecte troncature **et** altération à taille constante |
| Rétention | **outillée, non planifiée** | `--keep-last`, `--max-age-days` ; le dernier jeu n'est jamais supprimé |
| Restauration en cible isolée | **existe et testée** | base neuve `vericlaim_restore_<horodatage>`, révision et compteurs comparés au manifeste |
| Runbook | **existe** | `docs/runbooks/incident-sauvegarde.md` |
| Stratégie Neon (PITR / branches) | **hors de portée de l'agent** | l'équipe de pilotage a l'accès (voir `03` §0) ; aucun accès depuis ici |
| Sauvegarde du stockage objet | **octets archivés** | `objects/<bucket>/<clé>` contient le contenu réel ; entrée de manifeste par objet (bucket source, clé, taille, SHA-256, métadonnées) |
| Vérification des objets archivés | **existe et testée** | `verify` recalcule le SHA-256 de chaque objet, pas seulement celui du dump |
| Restauration des objets | **existe et testée** | `restore-objects --destination-bucket … --prefix …` : refuse la source, ne remplace jamais un objet existant différent |
| Chiffrement des sauvegardes | **non défini** | repose sur le chiffrement du volume cible ; non implémenté ici |
| RPO / RTO | **non définis** | — |
| Responsabilité | **non attribuée** | ni opérateur, ni astreinte |

Le produit ne possède donc aujourd'hui **aucune capacité de sauvegarde documentée
et testée**. C'est l'anomalie **A-08** (P0 pour un pilote avec données réelles).

## 3. Restauration réellement exécutée

Script : `tools/recette/restore.py` — reproductible, à la demande.

### 3.1 Scénario exact

1. **Base A** : PostgreSQL 16 réelle, migrée par Alembic (`a7d4c9e2b510`), rôles
   revus appliqués (`01_bootstrap_roles.sql`), grants appliqués.
2. **Données** écrites par les **services de l'application** (pas d'`INSERT`
   sauvage) : 2 organisations fictives, 2 documents, 2 versions, **6 événements
   d'audit chaînés**.
3. **Export** : `pg_dump --format=custom` (177 992 octets).
4. **Cible B** : base **distincte et vide** créée sur le même serveur jetable.
5. **Restauration** : `pg_restore --no-owner --no-privileges`, puis réattribution
   de la propriété des objets à `vericlaim_migrator` (invariant du bootstrap).
6. **Grants revus** rejoués (`02_after_migration_grants.sql`).
7. **Vérifications en tant que `vericlaim_app`** sur B.
8. L'archive est **supprimée** à la fin : aucun jeu de données exporté n'est
   conservé.

### 3.2 Résultat observé (`.recette/restore-report.json`)

```json
{
  "source_database_revision": "a7d4c9e2b510",
  "seeded_rows": {"organizations": 2, "documents": 2, "versions": 2, "audit_events": 6},
  "backup_bytes": 177992,
  "backup_format": "pg_dump --format=custom",
  "restore_seconds": 0.43,
  "target_database_revision": "a7d4c9e2b510",
  "revision_preserved": true,
  "organizations_restored": 2,
  "all_organizations_present": true,
  "rows_visible_without_tenant_context": 0,
  "rls_protected_tables": 26,
  "restored_audit_chain_valid": true,
  "restored_audit_events": 6,
  "audit_events_preserved": true,
  "data_loss": "none (writes stopped before the export)"
}
```

| Vérification | Résultat |
|---|---|
| export produit sans erreur | **oui** |
| restauration terminée | **oui** (0,43 s) |
| révision Alembic identique | **oui** (`a7d4c9e2b510`) |
| organisations restaurées | **2 / 2** |
| **chaîne d'audit recalculée et valide après restauration** | **oui** (6 événements, hachages revalidés) |
| RLS actif après restauration | **oui** (26 tables) |
| lecture sans contexte tenant | **0 ligne** |
| pertes | **aucune** |

La vérification de la chaîne d'audit n'est pas déclarative : le script recalcule
`sha256_json` de chaque événement et vérifie `previous_event_hash`, exactement
comme `GET /api/v1/audit/verify`.

### 3.3 Deux obstacles réels rencontrés (et ce qu'ils apprennent)

1. **`pg_restore --no-owner` recrée les objets au nom de l'utilisateur de
   restauration.** Les grants revus échouaient alors (`permission denied for table
   alembic_version`). Il faut **réattribuer la propriété à `vericlaim_migrator`
   avant** d'appliquer les grants. C'est un piège réel pour une restauration en
   production : le runbook doit l'inclure explicitement.
2. **La restauration ne restaure pas les rôles PostgreSQL.** `vericlaim_app` et
   `vericlaim_migrator` sont des objets de cluster, pas de base. Une restauration
   sur un cluster neuf exige de rejouer `01_bootstrap_roles.sql` (ou de recréer les
   rôles avec leurs attributs), puis les grants.

### 3.4 Ce que cette preuve est, et n'est pas

**C'est** : la démonstration que les données VeriClaim survivent à un export/import
logique ; que rôles, grants et RLS sont reproductibles ; que la chaîne d'audit reste
valide après restauration ; et que le temps de reprise est négligeable à cette
échelle.

**Ce n'est pas** : la restauration d'une sauvegarde Neon. Aucun accès Neon, aucune
branche de restauration, aucun PITR. Aucune mesure de RPO/RTO de plateforme. Le
volume est celui d'une base de recette (177 ko), pas d'un pilote réel.

## 4. Ce que Neon doit fournir (assistant)

| # | Demande | Attendu |
|---|---|---|
| B1 | Politique de sauvegarde effective du projet `VeriClaim-Recette-Isolated` | PITR ? fenêtre ? fréquence ? |
| B2 | Procédure de restauration (branche vers un point dans le temps) | procédure + durée constatée |
| B3 | Cible **isolée** autorisée pour un essai de restauration | base/instance jetable |
| B4 | Chiffrement des sauvegardes et localisation | oui/non, région |
| B5 | Rétention | durée |
| B6 | Responsable de l'exploitation | personne ou rôle |
| B7 | RPO / RTO cibles pour la recette | valeur acceptée par le métier |

Tant que B1–B7 ne sont pas confirmés **et** qu'une restauration Neon n'a pas été
exécutée sur une cible isolée, la ligne « sauvegarde et restauration vérifiées »
du verdict reste **non cochée**.

## 5. Sauvegarde du stockage objet — lacune de conception

Rien n'existe. Un document promu vit dans un bucket ; s'il est perdu, la ligne
`document_versions` devient un pointeur mort, et un rapport « probatoire » cite un
objet inexistant. Il faut :

- versioning de bucket et/ou réplication, ou sauvegarde périodique ;
- **cohérence base ↔ stockage** : restaurer la base sans les objets produit des
  pointeurs morts ; restaurer les objets sans la base laisse des orphelins non
  traçables. L'ordre (base d'abord, objets ensuite, puis réconciliation) doit être
  écrit et testé ;
- un contrôle de réconciliation object/version, qui n'existe pas aujourd'hui.

Priorité **P1**. C'est une lacune de conception, pas seulement d'exploitation.

## 5 bis. Ce que A-08 ajoute réellement (et ce qu'il n'ajoute pas)

**Ajouté et testé** (`backend/tests/test_a08_backup_restore.py`, **8/8**, sur un
vrai PostgreSQL 16 jetable) :

| Contrôle | Résultat |
|---|---|
| Manifeste décrivant exactement l'archive (taille + SHA-256) | conforme |
| Détection d'une archive **tronquée** | `verify` → sortie 2, motif explicite |
| Détection d'une archive **altérée à taille constante** | détectée par SHA-256 |
| Restauration dans une base neuve | révision préservée, compteurs identiques, source intacte |
| Cible absente | aucune restauration tentée |
| Rétention | garde les N derniers, ne vide jamais le magasin |
| `redact_dsn` | ni mot de passe ni DSN dans les rapports |

**Objets S3 — ce qui est désormais prouvé (11 tests, serveur moto en processus) :**

| Scénario | Résultat vérifié |
|---|---|
| un objet | octets archivés, SHA-256 conforme, restitution à l'identique |
| plusieurs objets, deux buckets | tous capturés, aucun mélange de bucket dans l'archive |
| objet source illisible/absent | `missing: true` au manifeste — jamais omis silencieusement |
| clé hostile (`../../etc/passwd`) | chemin neutralisé, aucun écrit hors du répertoire de sauvegarde |
| archive d'objet corrompue | `verify` → 2, motif SHA-256 |
| checksum erroné au manifeste | `verify` → 2 |
| restauration vers destination distincte | octets identiques, contrôlés après écriture |
| rejeu de la restauration | 0 réécriture, objets identiques reconnus (`already_present`) |
| collision avec un objet différent | **refus explicite**, l'objet existant est intact |
| restauration vers la source | **refusée** (même bucket + préfixe vide) ; préfixe non vide accepté |
| sources après sauvegarde et restauration | ETags identiques : ni ajout, ni suppression, ni réécriture |

Semantique de la restauration : **idempotente et non destructive**. Un objet
identique déjà présent est reconnu (aucune écriture) ; un objet différent à la
même clé provoque un refus global **avant** toute écriture — une restauration
partielle serait plus difficile à reprendre qu'un refus.

**Toujours absent, et qui bloque un pilote :**

| Manque | Conséquence |
|---|---|
| planification (cron / workflow) | **RPO réel = depuis la dernière exécution manuelle** |
| restauration vers un stockage **managé réel** | prouvée contre un serveur S3 moto (contrat HTTP), **pas** contre S3/MinIO de production |
| politique de chiffrement | dépend du volume cible |
| sauvegarde Neon (PITR) | hors de portée de l'agent (voir `03` §0) |
| mesure du RTO à l'échelle | le RTO mesuré (~3 s) porte sur un schéma vide : **il ne vaut rien** comme engagement |

Le RTO réel ne peut pas être annoncé sur cette base. Toute communication au
pilotage doit dire « restauration outillée et testée sur un schéma de test »,
jamais « RTO de 3 secondes ».

## 6. Runbook de restauration proposé

1. Identifier le point de reprise (Neon : branche PITR ; objet : version datée).
2. Créer une cible **isolée** (jamais la base de production).
3. Rejouer les rôles (`01_bootstrap_roles.sql`) si le cluster est neuf.
4. Restaurer la base.
5. **Réattribuer la propriété des objets à `vericlaim_migrator`** (§3.3).
6. Appliquer `02_after_migration_grants.sql`.
7. Vérifier : révision Alembic, `verify_runtime.py`, `rows_visible_without_tenant_context = 0`.
8. Vérifier la chaîne d'audit (`GET /api/v1/audit/verify`).
9. Restaurer les objets de stockage, puis réconcilier version ↔ objet.
10. Basculer le trafic uniquement après 7 et 8.

Les étapes 4–8 ont été exercées ici. Les étapes 1, 2, 3 (sur un cluster neuf) et 9
ne l'ont pas été.

## 7. Verdict

- **Une sauvegarde outillée, vérifiable et restaurable existe désormais** (A-08),
  avec 8 tests exécutés sur une vraie instance PostgreSQL.
- **Elle n'est pas planifiée** : le RPO reste non défini en pratique. La
  sauvegarde existe, la **politique** de sauvegarde n'existe pas.
- **Une restauration a réellement été exécutée** sur une cible isolée, avec
  vérification de la révision, des données, du RLS et de la chaîne d'audit.
- **La sauvegarde Neon n'est pas vérifiée** — non par absence d'accès de
  l'équipe (`03` §0), mais par absence d'accès de l'agent.
- **Le stockage objet est sauvegardé et restauré pour de vrai** (octets, pas
  inventaire), et la restauration est prouvée contre un serveur S3 moto. Cela
  ne démontre **ni** la durabilité, **ni** le versioning, **ni** le chiffrement
  d'un stockage managé réel, et aucune restauration n'a été faite vers un
  bucket de production.
- **Aucun RTO engageable** n'est mesurable ici. Ne pas en annoncer un.
