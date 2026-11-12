# Sécurité et exploitation : état et preuves à obtenir

## Présent dans le code

Sessions opaques, RBAC, CSRF pour mutations, contexte tenant et RLS PostgreSQL fail-closed ; uploads en quarantaine et scan avant promotion ; jobs durables pour extraction et détection ; endpoints de santé et disponibilité. Voir [architecture identité](../architecture/ch02-identity-tenancy.md), [ingestion](../architecture/ch03-secure-document-ingestion.md) et [migrations](../operations/production-migration-runbook.md).

## Avant mise en service

- Définir les propriétaires d'exploitation, l'inventaire des accès, secrets, rotation, gestion des incidents et délais d'escalade. Ne jamais stocker les secrets dans le dépôt.
- Appliquer les migrations via un job unique avec sauvegarde préalable ; utiliser des privilèges runtime minimaux et vérifier RLS avec comptes réels et tests inter-tenants.
- Configurer TLS, OIDC et domaines/CORS, stockage privé, antivirus, OCR, deux workers indépendants et supervision des files, erreurs, capacité et alertes. Tester les échecs fermés.
- Sauvegarder base et objets, définir RPO/RTO et rétention, puis effectuer une restauration de bout en bout documentée. Aucun RPO, RTO ou SLA n'est validé ici.
- Réaliser revue de sécurité indépendante, dépendances, tests d'intrusion et charges adaptés ; suivre les correctifs.

`/healthz` est un contrôle du processus, non un engagement de disponibilité ; `/readyz` vérifie des dépendances configurées. Les métriques non instrumentées de l'interface ne sont pas une supervision. Le chaînage local n'empêche pas une réécriture par un acteur privilégié.

Vercel : l'inventaire des projets a été refusé par les permissions ; configuration, déploiement, exécution durable des workers et opérations hébergées non vérifiés. Il ne faut pas déduire une architecture de production effective du seul `vercel.json`.
