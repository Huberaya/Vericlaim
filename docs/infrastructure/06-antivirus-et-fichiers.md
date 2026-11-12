# 06 — Antivirus et sécurité des fichiers

**Date :** 12 novembre 2026
**SHA testé :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`

> ## Avertissement en tête de document
>
> **Je n'ai pas pu exécuter ClamAV.** `libclamav`, le démon `clamd`, `freshclam` et
> la base de signatures ne sont pas installables dans mon environnement (ni par
> `apt`, root indisponible, ni depuis un dépôt joignable).
>
> **« Antivirus opérationnel » n'est PAS démontré par ce chantier, et ne doit pas
> être annoncé comme tel commercialement.** Ce qui est démontré est plus étroit et
> doit être lu exactement comme tel : le *client* clamd de VeriClaim est correct,
> et le produit **refuse de traiter un document non vérifié**.

## 1. Mécanisme réellement implémenté

`app/documents/scanner.py` implémente un client **clamd INSTREAM** minimal :

- envoi par blocs de 64 kio encadrés de leur longueur, terminés par un bloc nul ;
- réponse unique terminée par `\0`, taille bornée à 16 kio ;
- **échéance globale** (`DOCUMENT_CLAMAV_TIMEOUT_SECONDS`) appliquée à chaque
  opération socket, pas seulement à la connexion ;
- `stream: OK` → propre ; `stream: <signature> FOUND` → infecté **sans jamais
  exposer la signature** à l'appelant ; toute autre réponse (erreur clamd, trame
  ambiguë, encodage invalide, caractères de contrôle, EOF sans verdict, réponse
  supplémentaire) → **erreur**, jamais « propre » ;
- client sans hôte ou délai non positif → refus de construction ;
- `DOCUMENT_SCANNER_MODE=disabled` → **fail-closed** : toute analyse lève.

Trois scanners existent : `ClamAvMalwareScanner` (production),
`TestCleanMalwareScanner` (réservé à `APP_ENV=test`, refuse toujours EICAR),
`DisabledMalwareScanner` (refuse tout).

## 2. Ce que j'ai réellement exécuté

J'ai écrit un **écouteur du protocole clamd** (`tools/recette/clamd_stub.py`) qui
parle la même langue que le démon. Le client de production s'y connecte sans être
modifié. Cela prouve la robustesse du client, pas l'efficacité d'un antivirus.

| # | Test | Attendu | Observé |
|---|---|---|---|
| 1 | document sain | propre | `MalwareScanResult(is_clean=True, engine="clamav")` |
| 2 | **fichier EICAR** (isolé) | détecté | `is_clean=False` — et en E2E, **HTTP 422**, `upload.status=rejected`, aucune version promue |
| 3 | trame d'erreur clamd (`… ERROR`) | refus, pas « propre » | `MalwareScanError` |
| 4 | réponse **ambiguë** (deux trames) | refus | `MalwareScanError` |
| 5 | délai dépassé (réponse lente) | indisponible | `MalwareScannerUnavailableError` |
| 6 | connexion fermée sans verdict | indisponible | `MalwareScannerUnavailableError` |
| 7 | scanner désactivé | tout refusé | `MalwareScannerUnavailableError` |
| 8 | fichier corrompu (PDF tronqué) | refusé avant tout parseur | 422 sur signature |
| 9 | fichier trop volumineux | refusé | 422 |
| 10 | téléchargement d'un document rejeté | impossible | aucune version n'existe, donc aucune URL ne peut être émise |
| 11 | reprise après indisponibilité | la complétion renvoie 503, l'upload reste `scanning`, un nouvel appel re-tente | couvert par `test_clamav_indeterminate_verdicts.py` (vert) |

Le fichier EICAR utilisé est la chaîne de test canonique, générée localement, sans
code exécutable. Elle n'a jamais quitté le répertoire jetable `.recette/`.

## 3. La règle qui compte : rien n'entre sans verdict

C'est le contrôle de sécurité réel et il est prouvé :

1. les octets arrivent dans le bucket **quarantaine** ;
2. `complete_document_upload` fait un `HEAD`, vérifie la taille et le type, puis
   appelle le scanner ;
3. **si le scan échoue ou est indisponible, aucune copie n'est faite vers le
   bucket propre, aucune version n'est créée, aucun parseur n'est invoqué** ;
4. un document rejeté ne peut donc pas être téléchargé : la route de téléchargement
   exige une `DocumentVersion`.

Vérifié en E2E : après un EICAR en 422, aucune `document_versions` n'existe pour
cet upload.

## 4. Écarts connus, à ne pas minimiser

| Écart | Impact | Priorité |
|---|---|---|
| Aucun moteur ClamAV n'a été exécuté | **on ne sait pas si un vrai fichier malveillant serait détecté** | **P0 pour un pilote qui ingère des pièces tierces** |
| Base de signatures et fraîcheur non vérifiées | une base périmée détecte moins | P0 |
| Quarantaine : pas de politique de conservation/analyse manuelle outillée | un fichier infecté reste dans le bucket quarantaine | P2 (l'expiration 1 j de Compose atténue) |
| `scan_signature_version` jamais renseigné par le client | impossible de prouver avec quelle base un scan a eu lieu | P2 |
| Aucun test de reprise avec un vrai `clamd` redémarré | scénario couvert par le stub seulement | P2 |

## 5. Ce qu'il faut faire avant un pilote

1. Déployer un vrai `clamd` (ou un service d'analyse managé) sur l'environnement de
   recette, avec `DOCUMENT_SCANNER_MODE=clamav` et `DOCUMENT_CLAMAV_HOST` joignables.
2. Vérifier `/readyz` → `malware_scanner: configured`.
3. Exécuter le scénario EICAR **sur la Preview**, avec un accès autorisé, et
   constater le refus.
4. Renseigner la version de signatures dans `scan_signature_version` pour rendre la
   preuve d'analyse auditable.
5. Documenter la politique de quarantaine (conservation, accès, purge).

Tant que 1 à 3 ne sont pas faits par un opérateur disposant d'un accès réel,
la phrase « analyse antivirus opérationnelle » **ne doit pas être utilisée**.

## 6. Verdict de la phase

- **Le contrôle « aucun fichier non analysé dans le circuit normal » est prouvé et
  robuste** : refus fail-closed, EICAR bloqué, aucune promotion, erreurs clamd
  traitées comme des erreurs et non comme des « propres ».
- **L'antivirus lui-même n'est pas qualifié** : aucun moteur réel exécuté. C'est
  une **limite explicite** de ce chantier, pas un résultat.
