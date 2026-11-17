# Runbook — API indisponible ou dégradée

**Alertes :** `VeriClaimApiDown`, `VeriClaimDatabaseDown`, `VeriClaimApiLatencyHigh`

## 1. Confirmer (30 s)

```bash
curl -sS -m 5 https://<hôte>/healthz   # le processus répond-il ?
curl -sS -m 5 https://<hôte>/readyz    # la base répond-elle ?
curl -sS -m 5 https://<hôte>/metrics | grep -E 'vericlaim_(database_up|uptime)'
```

- `/healthz` échoue → section 2 (processus).
- `/healthz` ok, `/readyz` en 503 avec `database: unavailable` → section 3 (base).
- Les deux ok mais p95 > 2,5 s → section 4 (saturation).

## 2. Le processus ne répond pas

1. Vérifier que le conteneur/instance tourne : `docker compose ps backend`.
2. Lire les 100 dernières lignes de journal. Une erreur au démarrage est
   presque toujours explicite (`DatabaseUnavailableError`, migration manquante,
   configuration refusée).
3. Redémarrer **une fois** : `docker compose restart backend`.
4. Si le redémarrage échoue de la même façon, la cause est au démarrage, pas
   dans la charge. Ne pas boucler sur les redémarrages.

**Ne pas** passer `AUTO_CREATE_SCHEMA=true` pour « débloquer » : c'est un
contournement de migration, pas un correctif.

## 3. La base est injoignable

```bash
psql "$DATABASE_URL" -c 'select 1'          # depuis l'hôte de l'API
```

- Refus de connexion → réseau, pare-feu, ou base arrêtée. Vérifier d'abord le
  réseau, ensuite l'état du serveur PostgreSQL.
- Trop de connexions → vérifier le pool. Un pool épuisé se voit dans les
  journaux PostgreSQL (`too many clients`).
- TLS refusé → la production exige `sslmode=require` ; ne jamais rétrograder en
  `sslmode=disable` pour faire passer une connexion.

## 4. Lenteur sans panne

1. `vericlaim_document_queue_queued` élevé ? Le problème est la file, pas l'API
   → voir `incident-file-bloquee.md`.
2. Sinon, vérifier la latence base et la charge du serveur.
3. Rappel d'architecture : l'OCR tourne dans un **worker séparé**. Si l'OCR
   apparaît dans les traces de l'API, une route exécute du travail long et doit
   être déplacée.

## 5. Clôture

L'incident est clos quand `vericlaim_database_up == 1`, `/readyz` en 200 et le
taux de 5xx revenu sous 2 % pendant 15 minutes. Consigner la cause réelle dans
`docs/infrastructure/12-registre-anomalies.md` si elle révèle un défaut.
