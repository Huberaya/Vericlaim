# Checklist de release — VeriClaim AI

À dérouler **avant chaque mise en production**. Chaque case doit être cochée avec
une preuve, pas de mémoire. Une case non cochée bloque la release.

Référence : [`SECURITY.md`](../../SECURITY.md) · [`production-migration-runbook.md`](./production-migration-runbook.md)

---

## 0. Prérequis bloquants

- [ ] La branche de release est `main` et le commit a été revu.
- [ ] La CI est verte : `pytest`, `tsc --noEmit`, `next build`, `alembic heads` (tête unique).
- [ ] Aucune vulnérabilité critique ou élevée ouverte sur le périmètre livré.
- [ ] Les modifications des zones sensibles (voir `SECURITY.md` §4) portent le label `security-reviewed`.

---

## 1. Environnement — la cause racine de l'incident le plus grave

> Sans `APP_ENV`, l'application démarre en mode `development` : route pilote
> ouverte, SQLite, cookies non sécurisés, CORS localhost. L'application refuse
> désormais ce cas au démarrage, mais la variable doit être posée côté plateforme.

- [ ] **`APP_ENV` est défini explicitement** sur la plateforme de déploiement (`production`).
- [ ] `VERCEL_ENV` (ou l'indicateur équivalent) est cohérent avec `APP_ENV`.
- [ ] `ALLOW_LOCAL_SQLITE_FALLBACK` est **absent ou `false`**.
- [ ] `ENABLE_DEV_LOGIN` est **absent ou `false`**.
- [ ] `APP_ENV` n'est **pas** codé en dur dans `vercel.json` (il doit refléter l'environnement réel, pas une constante figée).
- [ ] Le log de démarrage confirme l'environnement résolu : `event=startup_runtime`.

**Preuve attendue** — extrait de log de démarrage. Format réel, mesuré :

```json
{"event": "startup_runtime", "environment": "staging", "database_backend": "postgresql+psycopg",
 "database_host": "h", "auto_create_schema": false, "dev_login_enabled": false,
 "document_storage_backend": "s3", "document_scanner_mode": "clamav",
 "oidc_configured": true, "secure_cookies": true}
```

En production, `environment` doit valoir `production`. `database_host` est volontairement
un hôte masqué (jamais l'URL complète, qui contient le mot de passe).

- [ ] Aucune ligne `event=dev_login_enabled` ni `event=local_sqlite_fallback_enabled` dans les logs de production.

---

## 2. Secrets et configuration

- [ ] `AUTH_SESSION_SECRET` : valeur aléatoire, ≥ 48 caractères, non-placeholder, distincte par environnement.
- [ ] `DATABASE_URL` : PostgreSQL, identifiant applicatif à moindre privilège (jamais le compte de migration).
- [ ] `DOCUMENT_STORAGE_SSE_MODE` : `aes256` ou `aws:kms` (jamais `none`).
- [ ] `AUTH_COOKIE_SECURE=true`, `FRONTEND_URL` et `CORS_ORIGINS` en HTTPS, origines explicites (pas de `*`).
- [ ] `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_REDIRECT_URI` cohérents, callback sur l'hôte frontend.
- [ ] `DOCUMENT_SCANNER_MODE=clamav` et `DOCUMENT_CLAMAV_HOST` joignables.
- [ ] Aucun secret dans le dépôt, les logs, ou un message d'erreur retourné au client.
- [ ] Secrets tournants planifiés (dernière rotation : \_\_\_\_\_\_\_\_).

**Commande de contrôle recommandée**

```bash
python -c "from app.core.config import get_settings, describe_runtime; print(get_settings().describe_runtime())"
```

---

## 3. Base de données et migrations

- [ ] La migration a été validée sur **staging** d'abord.
- [ ] `alembic heads` renvoie **une seule** tête.
- [ ] La migration est appliquée par le job dédié, jamais par le démarrage de l'API.
- [ ] Les droits runtime à moindre privilège sont appliqués après migration.
- [ ] Les politiques RLS sont présentes sur toute nouvelle table tenant.
- [ ] `/readyz` confirme la révision attendue.

```bash
python -m alembic current
python -m alembic heads
```

- [ ] **Sauvegarde récente vérifiée** et **restauration testée** sur base éphémère.

---

## 4. Contrôles de sécurité fonctionnels

- [ ] `POST /api/v1/auth/dev-login` renvoie **404** en production.
- [ ] Aucune route `/api/v1/auth` ne répond à un appelant anonyme hors liste blanche.
- [ ] Une requête inter-tenant renvoie 404/403 (test A/B exécuté sur l'environnement déployé).
- [ ] Une mutation sans jeton CSRF renvoie 403.
- [ ] Un accès sans session renvoie 401.
- [ ] `GET /api/v1/audit/verify` renvoie `is_valid: true` sur une organisation ayant de l'activité.
- [ ] Le certificat d'intégrité s'émet (200) et se refuse après altération (409).

---

## 5. Disponibilité et reprise

- [ ] Abaisser la base → `/healthz` répond, `/readyz` signale non-prêt, l'API renvoie **503** (jamais de base vide).
- [ ] Le repli SQLite est **inactif** : aucune écriture dans `/tmp`.
- [ ] Redémarrage d'un worker en cours d'analyse → reprise automatique par lease.
- [ ] Antivirus ou stockage indisponible → 503 et **aucune promotion** de fichier.

---

## 6. Parcours de bout en bout (le test qui compte vraiment)

> La leçon de l'audit : 89 tests passaient pendant que la chaîne d'audit était
> cassée. La suite verte ne prouve rien sur le parcours réel.

- [ ] Création de compte / connexion SSO.
- [ ] Création d'organisation, invitation d'un collègue.
- [ ] Création fournisseur + produit.
- [ ] Import d'un document (TXT, PDF texte, PDF scanné).
- [ ] Analyse de bout en bout jusqu'au statut `completed`.
- [ ] **Le rapport téléchargé contient exactement les données de l'application** (allégations, verdicts, score, versions).
- [ ] **Un rapport altéré ou un JSON client falsifié est refusé.**
- [ ] Historique et versioning cohérents.
- [ ] Export de dossier (ZIP) lisible.

---

## 7. Observabilité

- [ ] Logs structurés émis avec `organization_id` et `request_id`.
- [ ] Alertes actives : échec de job répété, `/readyz` en échec, chaîne d'audit invalide, file saturée.
- [ ] Remontée d'erreurs frontend active.
- [ ] Durées mesurées : extraction, OCR, analyse, génération de rapport.

---

## 8. Documentation et support

- [ ] `README` exact (pas d'affirmation non vérifiée).
- [ ] Mentions légales, politique de confidentialité, CGU à jour.
- [ ] Canal de support joignable et testé.
- [ ] Page de statut à jour.

---

## 9. Décision finale

| Champ | Valeur |
|---|---|
| Version / commit | |
| Environnement | |
| Date et heure | |
| Déroulé par | |
| Approuvé par | |
| Incidents connus au lancement | |

- [ ] **GO** — toutes les cases obligatoires cochées.
- [ ] **NO-GO** — motif : \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

---

## 10. Après la release

- [ ] Vérification post-déploiement : `/healthz`, `/readyz`, connexion, une analyse.
- [ ] Surveillance renforcée pendant 24 h.
- [ ] Retour d'incident documenté si la release a été annulée.
- [ ] Procédure de rollback connue et applicable (révision Alembic précédente identifiée).
- [ ] **Répétition de restauration exécutée et consignée** avant toute migration de schéma
      (`python scripts/ci/rehearse_restore.py`, mode opératoire :
      [`restore-runbook.md`](restore-runbook.md)). Sans cette case, une migration part sans
      chemin de retour vérifié.
