# Runbook — File de traitement bloquée

**Alertes :** `VeriClaimDocumentQueueBacklog`, `VeriClaimDocumentJobStalled`,
`VeriClaimWorkerHeartbeatStale`, `VeriClaimAnalysisQueueBacklog`

## 1. Distinguer « pas de worker » de « worker bloqué »

C'est la seule question utile, et deux signaux y répondent :

```bash
# Le worker publie un battement : âge = temps depuis la dernière itération.
cat /run/vericlaim/document-worker.json 2>/dev/null || echo "aucun battement"

# Profondeur et retard réels, lus en base.
curl -sS https://<hôte>/metrics | grep -E 'vericlaim_(document|analysis)_queue'
```

| Battement | File | Diagnostic |
|---|---|---|
| absent | ne baisse pas | **aucun worker** en vie → section 2 |
| âge > 120 s | ne baisse pas | worker **bloqué** → section 3 |
| frais | baisse | ça avance : vérifier le débit, pas l'arrêt |

## 2. Aucun worker en vie

1. Démarrer le worker : `docker compose up -d document-worker analysis-worker`.
2. Vérifier la santé au bout de 30 s :
   `docker compose exec document-worker python -m app.worker_healthcheck --file /run/vericlaim/document-worker.json`
3. Si le worker meurt au démarrage, lire son journal : configuration
   manquante, base injoignable, ou migration non appliquée.

**Rappel d'architecture** : sur un hébergement sans processus long (Vercel
serverless), **rien ne draine la file**. C'est A-06 : le worker doit tourner
sur un runtime séparé (conteneur, VM, service managé). Aucun réglage côté
Vercel ne remplace ce processus.

## 3. Worker vivant mais bloqué

1. Le battement existe et vieillit : la boucle est bloquée dans une itération.
2. Causes connues : OCR sur un PDF pathologique, base lente, stockage objet qui
   ne répond pas.
3. `docker compose restart document-worker` débloque. Le job en cours sera
   repris **par expiration de lease** — c'est prévu, pas une perte :
   `document_extraction_jobs.lease_expires_at` rend le job réclamable.
4. Vérifier qu'aucun job n'est resté `RUNNING` au-delà du lease :
   ```sql
   SELECT count(*) FROM document_extraction_jobs
   WHERE status = 'running' AND lease_expires_at < now();
   ```
   Un compte non nul qui ne diminue pas signifie que le worker ne récupère pas
   les leases expirés : c'est un défaut à consigner, pas à masquer.

## 4. Ne pas faire

- Ne pas marquer les jobs `completed` à la main pour vider la file : cela
  fabrique exactement le succès non durable que A-13 a coûté à diagnostiquer.
- Ne pas supprimer de jobs `queued`.
- Ne pas augmenter `DOCUMENT_EXTRACTION_LEASE_SECONDS` pour « éviter les
  reprises » : cela allonge le délai avant qu'un job abandonné soit repris.
