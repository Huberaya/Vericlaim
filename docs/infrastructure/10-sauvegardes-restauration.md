# 10 — Sauvegardes et restauration

**Date :** 18 novembre 2026 (révision A-08 : octets S3, portabilité Windows)
**SHA de référence :** `8d142e6ef47572e8541b88d3730c2f41d9880f32` + corrections du
18 novembre (voir `main` après fusion de la PR #15 pour le SHA exact)
**Cible d'essai :** instances jetables isolées, jamais une base ni un bucket
persistants

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
| Portabilité des noms d'objets | **existe et testée** | noms non portables (casse, points finaux, noms réservés Windows, Unicode) encodés avec empreinte ; collision résiduelle ⇒ sauvegarde interrompue avant écrasement (§5 ter) |
| Restauration des objets | **existe et testée** | `restore-objects --destination-bucket … --prefix …` : refuse la source, ne remplace jamais un objet existant différent |
| Chiffrement des sauvegardes | **non défini** | repose sur le chiffrement du volume cible ; non implémenté ici |
| RPO / RTO | **non définis** | — |
| Responsabilité | **non attribuée** | ni opérateur, ni astreinte |

**Ce qui a changé.** Une version antérieure de ce document affirmait que le
produit ne possédait « aucune capacité de sauvegarde documentée et testée ». Ce
n'est plus vrai et la phrase est retirée : depuis A-08, base **et** objets sont
sauvegardés, vérifiés et restaurables, avec des tests exécutés.

**Ce qui reste vrai.** Aucune sauvegarde n'est **planifiée**, donc **aucun RPO
n'est effectif**, et la restauration des objets est prouvée contre un serveur
S3 de test, **pas** contre un stockage managé. A-08 reste **partielle** (P0 pour
un pilote avec données réelles).

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

## 5. Sauvegarde du stockage objet

### 5.1 Ce qui est outillé

`tools/backup/vericlaim_backup.py` archive les **octets** de chaque objet sous
`objects/<bucket>/<clé encodée>`, avec une entrée de manifeste par objet (bucket
source, clé, taille, SHA-256, métadonnées dont `content_type`). `verify`
recalcule le SHA-256 de chaque objet archivé. `restore-objects` les réécrit vers
un bucket et un préfixe **explicites**.

Règles de la restauration, toutes testées :

| Règle | Raison |
|---|---|
| destination obligatoire (bucket + préfixe) | aucune cible devinée |
| refus si destination = source (même bucket, préfixe vide) | restaurer sur soi écraserait ce qu'on sauvegarde |
| objet identique déjà présent ⇒ sauté | rejeu idempotent, aucune réécriture |
| objet **différent** ⇒ refus global **avant** écriture | on ne remplace jamais une donnée existante |
| comparaison sur les **octets relus** | la métadonnée `sha256` est déclarative : un objet corrompu la conserve |
| relecture après écriture | un `put` réussi ne prouve pas le contenu stocké |
| aucune suppression | la restauration est additive |

### 5.2 Ce qui reste une lacune

- **Cohérence base ↔ stockage.** Restaurer la base sans les objets produit des
  pointeurs morts ; restaurer les objets sans la base laisse des orphelins non
  traçables. L'ordre (base d'abord, objets ensuite, puis réconciliation) est
  écrit ; **le contrôle de réconciliation object/version n'existe pas**.
- **Durabilité du stockage managé.** Ni versioning de bucket, ni réplication,
  ni chiffrement au repos ne sont mis en place ni vérifiés par ce dépôt.

Priorité **P1**. C'est une lacune d'exploitation et de réconciliation, plus de
conception : l'outil de sauvegarde et de restauration des objets existe.

## 5 bis. Ce que A-08 prouve, et ce qu'il ne prouve pas

**Ajouté et testé** (`backend/tests/test_a08_backup_restore.py`, **36/36** au
total : 8 pour la base, 28 pour les objets) :

| Contrôle | Résultat |
|---|---|
| Manifeste décrivant exactement l'archive (taille + SHA-256) | conforme |
| Détection d'une archive **tronquée** | `verify` → sortie 2, motif explicite |
| Détection d'une archive **altérée à taille constante** | détectée par SHA-256 |
| Restauration dans une base neuve | révision préservée, compteurs identiques, source intacte |
| Cible absente | aucune restauration tentée |
| Rétention | garde les N derniers, ne vide jamais le magasin |
| `redact_dsn` | ni mot de passe ni DSN dans les rapports |

**Objets S3 — ce qui est prouvé (20 tests, serveur moto en processus) :**

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
| nommage injectif (corpus de 50+ clés) | aucune paire de clés ne vise le même chemin, même après règles Windows |
| `file` / `file.` / `file ` / `file..` | quatre chemins distincts |
| noms réservés `CON`/`NUL`/`COM1-9`/`LPT1-9`, avec extension | encodés, jamais écrits tels quels |
| variantes de casse, NFC/NFD | chemins distincts |
| parcours réel de sauvegarde avec ces clés | tous les objets conservés, chacun avec ses propres octets |
| restauration de ces clés | chaque objet rendu à sa propre clé, aucun doublon |

**Deux points vérifiés par les octets, pas par la confiance :**

| Contrôle | Pourquoi il ne peut pas reposer sur autre chose |
|---|---|
| idempotence (objet « déjà présent ») | la métadonnée `sha256` est écrite par l'appelant, pas par le stockage. Un objet corrompu après dépôt garde la métadonnée de son contenu d'origine : le déclarer identique affichait un succès sur des données fausses. Le contenu est donc **relu et haché** |
| vérification après écriture | un `put_object` qui répond 200 ne prouve rien sur ce qui est stocké. Chaque objet écrit est relu, haché et comparé |

Au-delà de `--verify-bytes-limit` (512 Mio par défaut), l'objet n'est **pas**
haché : il est signalé `unverified_too_large` et la commande sort en **2**. Non
vérifié n'est jamais assimilé à vérifié.

**Nommage de l'archive — injectif et portable.** Les clés S3 peuvent contenir
des caractères que le système de fichiers refuse. Les remplacer par `_` faisait
s'effondrer six clés distinctes (`a:b.pdf`, `a<b.pdf`, `a?b.pdf`, `a*b.pdf`,
`a|b.pdf`, `a_b.pdf`) sur un seul fichier : deux objets sauvegardés, un seul
conservé, et un manifeste déclarant le contraire. Chaque composant non portable
porte désormais l'empreinte de son nom d'origine ; une collision résiduelle fait
**échouer la sauvegarde** au lieu d'écraser un objet. Détail au §5 ter.

**Moto n'est pas un stockage de production — ce que cela change.** Les tests S3
tournent contre un serveur **moto** : un vrai processus HTTP, un vrai protocole
S3, le client `boto3` de production. Cela prouve le **contrat applicatif**
(écriture, lecture, ETag, métadonnées, absences, refus). Cela **ne prouve pas** :

| Non prouvé par moto | Pourquoi cela compte |
|---|---|
| durabilité et réplication | moto stocke en mémoire : rien ne teste une perte de disque ou de zone |
| versioning de bucket | non activé, non exercé |
| chiffrement au repos et en transit | absent du serveur de test |
| cohérence en cas d'incident fournisseur | aucun scénario de panne partielle |
| politiques IAM réelles | identifiants factices |

Une restauration vers un stockage **managé réel** reste donc **à qualifier** :
c'est un prérequis de mise en service, pas un acquis.

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

## 5 ter. Portabilité des noms d'objets (Windows, macOS, Linux)

Les clés S3 peuvent contenir des noms que Linux accepte mais que Windows
réinterprète. Une archive produite sur Linux puis analysée sous Windows peut y
faire **écraser** un objet par un autre. Le nommage encode donc dès la
sauvegarde :

| Cas | Exemple | Risque sans encodage |
|---|---|---|
| points espaces finaux | `file`, `file.`, `file ` | un seul fichier survivant (Windows retire ces caractères) |
| noms réservés | `CON`, `NUL`, `COM1`, `LPT9`, avec ou sans extension | écriture vers un périphérique, pas un fichier |
| casse | `File.TXT` / `file.txt` | un seul fichier sur NTFS et macOS |
| normalisation Unicode | `é` NFC / NFD | un seul fichier sur macOS |
| caractères interdits | `a:b`, `a<b`, `a?b` | substitution `_` → collisions multiples |

Stratégie : un nom déjà sûr **et** portable (ASCII, minuscule, sans caractère
interdit, sans point/espace final, non réservé, ≤ 100 caractères) est conservé
tel quel. Tout le reste est encodé : préfixe `_`, forme lisible tronquée, et
**empreinte SHA-256 du nom d'origine**. Deux noms différents donnent deux
empreintes différentes, donc deux chemins. Le préfixe `_` garantit par
construction qu'un nom encodé ne peut pas coïncider avec un nom conservé.

Une garde indépendante vérifie ensuite, **pendant la sauvegarde réelle**, que
deux objets ne produisent jamais le même chemin — y compris après application
des règles Windows (casse, points finaux, noms réservés) et au niveau de chaque
composant de chemin. En cas de collision résiduelle, la sauvegarde
**s'interrompt avant tout écrasement**.

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

Les étapes 4–8 ont été exercées ici, sur des instances jetables. Les étapes 1,
2, 3 (sur un cluster neuf) et 9 **n'ont pas** été exercées contre un stockage
managé réel. L'étape 9 dispose désormais d'un outil (`restore-objects`), ce qui
n'est pas la même chose qu'une exécution en conditions réelles.

## 7. Verdict

- **Une sauvegarde outillée, vérifiable et restaurable existe désormais** (A-08) :
  **36 tests**, dont 8 sur une vraie instance PostgreSQL 16 et 28 sur les objets
  (serveur S3 moto en processus).
- **Elle n'est pas planifiée** : le RPO reste non défini en pratique. La
  sauvegarde existe, la **politique** de sauvegarde n'existe pas.
- **Une restauration a réellement été exécutée** sur une cible isolée, avec
  vérification de la révision, des données, du RLS et de la chaîne d'audit.
- **La sauvegarde Neon n'est pas vérifiée** — non par absence d'accès de
  l'équipe (`03` §0), mais par absence d'accès de l'agent.
- **Le stockage objet est sauvegardé et restauré pour de vrai** (octets, pas
  inventaire), avec un nommage portable : aucune clé, y compris ambiguë sous
  Windows, ne peut en écraser une autre.
- **La restauration est prouvée contre un serveur S3 moto, pas contre un
  stockage managé.** Ni durabilité, ni versioning, ni chiffrement, ni IAM réel
  ne sont démontrés, et aucune restauration n'a été faite vers un bucket de
  production. C'est un **prérequis de mise en service**, pas un acquis.
- **Aucun RPO/RTO n'est annoncé** : sans ordonnanceur ni exercice à l'échelle,
  il n'y en a pas.
- **Aucun RTO engageable** n'est mesurable ici. Ne pas en annoncer un.
