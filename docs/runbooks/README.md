# Runbooks d'incident — VeriClaim

Chaque alerte de `infra/monitoring/alerts.yml` renvoie ici. Un runbook dit quoi
faire **dans l'ordre**, comment savoir si l'action a marché, et ce qu'il ne faut
pas faire.

| Runbook | Déclenché par |
|---|---|
| [API indisponible](incident-api-indisponible.md) | `VeriClaimApiDown`, `VeriClaimDatabaseDown`, `VeriClaimApiLatencyHigh` |
| [File bloquée](incident-file-bloquee.md) | `VeriClaimDocumentQueueBacklog`, `VeriClaimDocumentJobStalled`, `VeriClaimWorkerHeartbeatStale` |
| [Antivirus](incident-antivirus.md) | `VeriClaimScannerUnreachable` |
| [Codes HTTP](incident-codes-http.md) | `VeriClaimHigh5xxRate` |
| [Sauvegarde et restauration](incident-sauvegarde.md) | sauvegarde manquante, vérification en échec, restauration nécessaire |

**Règle générale** : ne jamais désactiver un contrôle pour faire passer un
indicateur au vert. Si le remède est « désactiver l'alerte », la cause n'a pas
été trouvée.
