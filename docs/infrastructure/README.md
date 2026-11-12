# Chantier 2 — Environnement de recette VeriClaim

> **Périmètre : recette / Preview uniquement.** Aucune modification de production,
> aucune fusion, aucune migration destructive.
> **Dernière mise à jour :** 12 novembre 2026.

Ce dossier remplace l'affirmation « c'est déployé » par une série d'observations
reproductibles. Chaque document indique le SHA testé, la procédure exacte, ce qui
a réellement été observé, et — c'est le plus important — **ce qui n'a pas pu être
observé**.

## Lire dans quel ordre

| # | Document | Question à laquelle il répond |
|---|---|---|
| 00 | [État initial](00-etat-initial-recette.md) | D'où part-on, avec quelles preuves, et par quel SHA ? |
| 01 | [Architecture et configuration](01-architecture-et-configuration.md) | Quels composants réels existent vraiment ? |
| 02 | [Authentification et identité](02-authentification-et-identite.md) | Le backend valide-t-il l'identité cryptographiquement ? |
| 03 | [Neon et runtime](03-neon-et-runtime.md) | Le rôle runtime respecte-t-il les droits et le RLS ? |
| 04 | [Vercel Preview](04-vercel-preview.md) | La Preview correspond-elle au SHA testé ? |
| 05 | [Stockage documentaire](05-stockage-documentaire.md) | Les documents sont-ils réellement stockés et privés ? |
| 06 | [Antivirus et fichiers](06-antivirus-et-fichiers.md) | Un fichier dangereux peut-il entrer ? |
| 07 | [OCR et extraction](07-ocr-et-extraction.md) | L'extraction fonctionne-t-elle vraiment ? |
| 08 | [Workers et traitements](08-workers-et-traitements.md) | Les files asynchrones avancent-elles et sont-elles isolées ? |
| 09 | [Monitoring et incidents](09-monitoring-et-incidents.md) | Un incident est-il détectable et traçable ? |
| 10 | [Sauvegardes et restauration](10-sauvegardes-restauration.md) | Une restauration a-t-elle été testée ? |
| 11 | [Tests E2E](11-recette-e2e.md) | Le parcours complet fonctionne-t-il de bout en bout ? |
| 12 | [Registre d'anomalies](12-registre-anomalies.md) | Qu'est-ce qui est cassé, corrigé, ou bloquant ? |
| 13 | [Verdict](13-verdict-recette-exploitable.md) | Peut-on lancer un pilote ? À quelles conditions ? |

## Le harnais

Toutes les preuves de ce dossier proviennent de `tools/recette`, exécutable par
n'importe qui :

```bash
pip install -r backend/requirements.txt "pgserver>=0.1.4" "moto[server]>=5.0"
python -m tools.recette.run          # parcours complet  -> .recette/last-run.json
python tools/recette/restore.py      # restauration      -> .recette/restore-report.json
```

Il construit une pile jetable (PostgreSQL 16 réel avec les scripts de rôles
revus, une API S3 réelle utilisée par l'adaptateur boto3 de l'application, les
vrais workers comme processus séparés, un fournisseur OIDC réel) et rejoue le
parcours complet. Le compte rendu machine est écrit dans
`.recette/last-run.json` ; c'est la source de toutes les valeurs citées ici.

## Résultats en un coup d'œil

| Mesure | Valeur | Document |
|---|---|---|
| Parcours E2E | **54 / 54 PASS**, 0 échec | `11` |
| Suite backend (avec PostgreSQL) | **267 passed, 2 skipped** | `01` |
| Tables protégées par RLS (forcé) | **26** sur 26 | `03` |
| Politiques RLS | **26** | `03` |
| Restauration sur cible isolée | **réussie**, chaîne d'audit revalidée | `10` |
| Défauts réels trouvés / corrigés | **6 / 6** | `12` |
| Intégration continue | **4 / 4 jobs `success`** (run `43325846167`) | `12` |
| Conditions bloquantes restantes | **6** | `13` |

> **Statut proposé : QUALIFIÉ POUR LA CI ET LA RECETTE TECHNIQUE — NON QUALIFIÉ POUR
> UN PILOTE AVEC DONNÉES RÉELLES.** Motif principal : pas de worker sur Vercel, pas
> d'antivirus réel exécuté, pas de sauvegarde, et quatre preuves inaccessibles
> depuis cet environnement (Neon, Vercel à l'exécution, navigateur, ClamAV).

## Trois règles de lecture

1. **Rien n'est déclaré fonctionnel sans observation enregistrée.** Quand une
   preuve manque, le document le dit et nomme la conséquence.
2. **Un service simulé est nommé comme tel.** Le fournisseur OIDC, le serveur S3
   local et l'écouteur clamd ne sont pas Clerk, MinIO ni ClamAV. Chacun est
   marqué dans `01-architecture-et-configuration.md` et dans le document dédié.
3. **Aucun secret ne figure ici.** Aucune URL de connexion, aucune clé, aucun
   jeton. Les preuves citées sont des résultats, des codes HTTP et des requêtes
   SQL sans identifiants.
