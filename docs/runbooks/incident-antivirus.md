# Runbook — Moteur antivirus indisponible

**Alerte :** `VeriClaimScannerUnreachable`

## Ce qui se passe (important)

Le service est **fail-closed** : si le scan ne peut pas avoir lieu, l'import est
**refusé**. Aucun fichier non analysé n'est promu. L'effet visible est un refus
d'import, pas une faille.

```bash
curl -sS https://<hôte>/readyz | jq '.malware_scanner, .malware_scanner_reachable, .document_import'
# attendu en incident : "configured", false, "refused"
```

## 1. Vérifier le démon

```bash
# PING sur le port clamd (3310)
docker compose exec clamav clamdscan --ping 1
```

- `PONG` → le démon répond : le problème est réseau entre API et clamd.
- Pas de réponse → section 2.

## 2. Redémarrer clamd

```bash
docker compose restart clamav
docker compose logs --tail=50 clamav
```

Au **premier** démarrage, clamd télécharge les signatures : comptez plusieurs
minutes et attendez `SelfCheck: Database status OK` dans les journaux. Un
`freshclam` en échec (réseau sortant bloqué) laisse le démon sans base de
signatures : c'est une panne, pas un réglage.

## 3. Vérifier le retour à la normale

```bash
curl -sS https://<hôte>/readyz | jq '.malware_scanner_reachable, .document_import'
# attendu : true, "enabled"
```

Puis un EICAR de bout en bout (fichier de test inerte, standard) doit être
**refusé** — un EICAR accepté signifie que le scan ne s'exécute pas.

## 4. Ne pas faire

- Ne pas passer `DOCUMENT_SCANNER_MODE=disabled` ni `test` pour rétablir les
  imports. Ce serait accepter des fichiers non analysés en production.
- Ne pas marquer les uploads bloqués comme `clean`.
