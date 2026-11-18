# Runbook — Taux de 5xx anormal

**Alerte :** `VeriClaimHigh5xxRate` (> 2 % sur 5 min)

## 1. Localiser

```bash
curl -sS https://<hôte>/metrics | grep vericlaim_http_requests_total | grep 'status="5'
```

La série porte le **gabarit de route**, ce qui suffit à identifier le point
d'entrée sans exposer d'identifiant.

## 2. Causes par code

| Code | Cause probable | Premier réflexe |
|---|---|---|
| 500 sur imports | base ou stockage | `/readyz`, puis journaux |
| 503 | dépendance indisponible | runbooks API et antivirus |
| 500 après migration | colonne manquante | vérifier `alembic current` |

## 3. Vérifier l'état de la base et des migrations

```bash
cd backend && alembic current            # doit correspondre à alembic head
```

Une révision en retard produit des 500 concentrés sur une fonctionnalité, pas
un taux diffus. C'est le signe le plus fréquent d'un déploiement incomplet.

## 4. Ce qu'il ne faut pas conclure

Un 5xx n'est pas nécessairement une panne : un refus **volontaire** (antivirus
indisponible) est un 503. Lire le corps de la réponse avant de conclure.

## 5. Clôture

Taux sous 2 % pendant 15 min, et la cause réelle consignée. Si le défaut est
applicatif, il entre au registre des anomalies avec reproduction.
