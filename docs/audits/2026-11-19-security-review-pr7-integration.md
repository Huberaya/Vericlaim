# Revue de sécurité de la récupération de la PR #7 — 2026-11-19

Cette revue a été conduite par l'agent qui a intégré la PR #7, en suivant la
liste de contrôle de `SECURITY.md` §5. Elle **ne remplace pas** une revue par un
tiers : elle fournit les constats, les preuves et les angles morts pour que cette
revue puisse être faite. Le label `security-reviewed` reste volontairement absent
tant qu'un humain ne l'a pas posée.

Ce qui suit distingue trois choses qui ne doivent pas être confondues :
**vérifié** (une commande l'a montré), **non couvert** (personne ne l'a montré),
et **limite connue** (le comportement est mesuré, mais il est moins fort qu'il
n'y paraît).

---

## 1. La liste de contrôle, point par point

### « Aucun nouveau chemin ne crée d'identité, de session ou d'accès sans identité vérifiée »

**Vérifié.** Les 113 chemins documentés ont été interrogés sans session, sans
en-tête, avec un corps vide. 20 répondent sans identité, et chacune est déclarée
avec sa raison dans `scripts/ci/check_public_surface.py` : sondes de santé,
catalogue commercial, page RGPD, canal de support, vérification publique d'un
rapport signé. Aucune ne lit de donnée appartenant à une organisation.

Les cinq routes d'inscription et de réinitialisation refusent un corps vide par
validation (422) avant toute lecture ; le webhook de paiement refuse faute de
prestataire actif (404) et vérifie la signature avant tout traitement ; le
déclencheur de travaux est fermé par défaut (503 sans `CRON_SECRET`), et 401
avec un secret configuré.

**Mutation.** Six tests figent cette propriété, dont trois qui rouvrent
délibérément une route, font répondre 200 au webhook et retirent le fail-closed :
chacune doit être signalée. Un contrôle qui ne peut pas virer au rouge ne prouve
rien.

**Angle mort.** Ce contrôle mesure ce qui répond *sans identité*. Il ne juge pas
la qualité de l'autorisation d'une route authentifiée : une route qui exige une
session mais pas la permission adéquate resterait verte ici.

### « Aucune donnée sensible dans un log, un message d'erreur ou une réponse d'API »

**Vérifié partiellement.** Le démarrage publie un état de configuration expurgé :
`secure_cookies` apparaît comme `<masqué>` et la trace de connexion ne contient ni
DSN complet ni identifiant. Les réponses des routes publiques inspectées ne
contiennent aucune donnée client.

**Non couvert.** Aucun balayage systématique de l'ensemble des messages d'erreur
n'a été fait. Les tests existants couvrent des cas nommés
(`test_a10_runtime_identity.py`, `test_privacy_lifecycle.py`), pas l'exhaustivité.

### « Aucun secret, jeton ou identifiant en dur »

**Vérifié.** La CI exécute une étape « Scan tracked source for credential
signatures », verte. Les seules valeurs sensibles du dépôt sont des mots de passe
de rôles de test jetables (`rehearsal-only`, `integration-app-password`), créés et
détruits par les workflows eux-mêmes, et jamais utilisés hors de la CI.

### « Toute requête sur des données tenant est scopée par organization_id »

**Limite connue, mesurée.** Voir §2 : la politique RLS protège la lecture croisée
entre organisations authentifiées, mais pas une requête qui n'installe aucun
contexte.

### « Une nouvelle table tenant reçoit une politique RLS dans la migration »

**Vérifié.** Les cinq tables apportées par la PR #7 sans politique en ont une
maintenant (`a1b9c7e34d52`), avec `ENABLE` **et** `FORCE ROW LEVEL SECURITY`.
`infra/neon/verify_runtime.py` le contrôle en continu, et
`test_verifier_detects_a_new_unprotected_tenant_table` prouve que ce vérificateur
sait détecter une table oubliée.

### « Un échec de dépendance externe échoue fermé »

**Vérifié.** Trois cas exercés : sans `CRON_SECRET`, le déclencheur refuse de
travailler (503) plutôt que de drainer la file ; sans antivirus joignable, les
sondes de disponibilité passent en échec et la publication est refusée ; sans
prestataire de paiement actif, le webhook ne traite rien.

### « Un test de non-régression couvre la propriété visée »

**Vérifié pour les quatre défauts trouvés**, chacun avec un test qui échoue si la
faille est réintroduite :

| Défaut | Test de non-régression |
| --- | --- |
| Cinq tables sans RLS | `test_postgres_tenant_isolation.py` (4 preuves) |
| Garde d'abonnement inerte (`Depends` sur la fabrique) | `test_billing_plans_and_quotas.py` via `route_has_billing_gate` |
| Deux drapeaux pour la route pilote | `test_a10_runtime_identity.py` |
| Surface sans identité non vérifiée | `test_public_surface_is_closed.py` (6 tests, dont 3 mutations) |

---

## 2. Lignes sans organisation : corrigé le 19 novembre 2026

**État antérieur (mesuré, et défaut réel).** Trois tables (`identity_tokens`,
`email_messages`, `billing_provider_events`) portent un `organization_id`
nullable, et leur politique valait :

```
organization_id IS NULL OR organization_id = vericlaim_current_organization_id()
```

Conséquences mesurées avec le rôle applicatif (`vericlaim_app`, `NOBYPASSRLS`)
sur PostgreSQL 16.2 :

* contexte posé sur l'organisation A → A voyait ses lignes **et les orphelines**,
  donc le jeton de réinitialisation d'un utilisateur rattaché à B s'il avait été
  émis avant le rattachement ;
* aucun contexte → les orphelines étaient visibles, ce qui est voulu pour le
  service, mais aussi les lignes des organisations déjà rattachées ;
* l'inscription **échouait** : écrire une ligne rattachée sans contexte est
  refusé par `WITH CHECK` (« new row violates row-level security policy »).

Le troisième point n'avait pas été remarqué lors de la revue : la suite
PostgreSQL tournait avec le rôle `postgres`, qui contourne la RLS. Elle validait
donc l'application sur une base qui ne se comportait pas comme la production.

**Correction.** La séparation n'est plus faite par un prédicat permissif mais par
**identité** :

* un rôle dédié `vericlaim_system`, provisionné hors migration par
  `infra/neon/04_system_role.sql`, porte les parcours qui n'ont pas
  d'organisation par construction (inscription, vérification d'adresse,
  réinitialisation, invitation, webhook) ;
* ses droits sont accordés **par colonne** par la migration
  `c5f1a8e37b910_tenant_and_system_access_separation` : il ne lit ni le corps,
  ni le sujet, ni le destinataire d'un e-mail, et aucune table métier ; il ne
  peut pas supprimer un jeton ni réécrire l'audit ;
* le rôle applicatif ne voit **plus aucune** ligne sans organisation, et la
  politique devient une politique unique `FOR ALL` avec `USING` **et**
  `WITH CHECK` stricts, ce que `verify_runtime.py` exige.

**Preuves exécutées** (PostgreSQL 16.2, rôles réels, chaîne Alembic réelle) :

| Preuve | Fichier | Résultat |
| --- | --- | --- |
| Inscription sous le rôle applicatif | `test_postgres_identity_flows.py` | vert |
| Vérification d'adresse | idem | vert |
| Réinitialisation + révocation des sessions | idem | vert |
| Acceptation d'invitation (adhésion + session) | idem | vert |
| Jeton orphelin illisible par une organisation étrangère, lisible par le système | idem | vert |
| `WITH CHECK` refuse l'écriture croisée | idem | vert |
| Rôle système aveugle aux corps d'e-mail et aux tables métier | idem | vert |
| Audit en ajout seul (ni UPDATE ni DELETE) | idem | vert |
| 4 inscriptions concurrentes → 4 organisations distinctes | idem | vert |
| Aucune organisation ne lit une ligne sans organisation | `test_postgres_tenant_isolation.py` | vert |

**Validation par mutation**, pour que ces tests ne soient pas décoratifs :
repasser les parcours sur le rôle applicatif produit **6 échecs** ; rétablir la
politique permissive en produit **1** ; accorder `SELECT` global au rôle système
en produit **1**.

**Capacités résiduelles du rôle système, à connaître.** Il peut lire le nombre de
lignes d'`audit_events` (nécessaire au chaînage de hachage) et créer une
organisation (l'inscription la crée). Aucune des deux n'ouvre la lecture des
données d'un autre locataire, mais elles dépassent le strict périmètre
« identité » ; elles sont consignées plutôt que passées sous silence.

**Ce qui reste hors de portée de cette preuve.** La RLS est vérifiée sur une base
construite localement à partir de la chaîne Alembic, pas sur Neon. Un écart entre
les deux (paramètre de connexion, rôle propriétaire des tables) ne serait pas vu
ici.

---

## 3. Isolation : ce qui a été vérifié et comment

Exécuté contre un PostgreSQL 16.2 jetable, base construite par la chaîne Alembic
réelle, rôles `vericlaim_migrator` et `vericlaim_app` recréés à chaque exécution
avec leurs attributs de moindre privilège réaffirmés :

| Preuve | Résultat |
| --- | --- |
| Tête de migration atteinte | `a1b9c7e34d52` lors de cette revue ; `c5f1a8e37b910` après la mission 10 |
| Vérificateur `verify_runtime` sur base saine | aucun échec |
| Aucune ligne visible sans contexte | vert |
| Lecture limitée à sa propre organisation | vert |
| Cinq tables à RLS forcée, une politique chacune | vert |
| Jetons, messages, abonnement, sessions : lecture cloisonnée | vert |
| Écriture pour une autre organisation refusée (`WITH CHECK`) | vert |
| Insertion, mise à jour, suppression croisées sans effet | vert |
| Moindre privilège du rôle applicatif | vert |
| Tables d'audit en ajout seul pour le rôle applicatif | vert |
| Le vérificateur détecte RLS désactivée, prédicat permissif, table oubliée | vert |

Total : **15 preuves, 0 échec**.

---

## 4. Sauvegardes et workers

* **Restauration** : `tools/recette/restore.py` restaure une base PostgreSQL
  réelle depuis une sauvegarde et vérifie la chaîne d'audit restaurée — trois
  schémas de hachage reconnus, comme le vérificateur applicatif. Résultat :
  `FAILURES: none`, 38 tables RLS, `rows_visible_without_tenant_context: 0`.
* **Restauration en CI** : la répétition démarre un PostgreSQL jetable avec TLS,
  échoue nommément si TLS est inactif, et passe sur `225f1a8` puis `b138b2a`.
* **Planification** : aucun cron n'est déclaré. Le plan Vercel Hobby refuse les
  tâches planifiées ; l'affirmer serait faux. **La planification n'est donc pas
  prouvée**, et RPO/RTO ne sont pas mesurés. C'est une réserve de mise en
  service, pas un bloqueur de fusion.
* **Storage réel** : la restauration est prouvée contre moto, pas contre un
  stockage S3 réel. Non couvert.

---

## 5. Facturation

* Aucun encaissement réel ni simulé. L'adaptateur « local » refuse de démarrer en
  production et `collects_money` vaut `false` dans le catalogue public.
* Idempotence des événements prestataires : contrainte d'unicité sur
  (`provider`, `provider_event_id`) et test correspondant.
* Les prix sont marqués `provisoires — valeurs de catalogue non validées par la
  direction commerciale` : la réponse publique le dit, plutôt que de laisser
  croire à une grille validée.

---

## 6. Réglementaire

Les verdicts s'appuient sur un référentiel interne et sont traçables jusqu'à la
règle appliquée (version de référentiel et de moteur figées dans le rapport).
**Aucune validation juridique externe n'a eu lieu.** Les documents de
`docs/legal/` sont des modèles à faire relire, pas des documents opposables.

---

## 7. Ce qui n'a pas été fait, et qu'il ne faut pas lire comme fait

1. Revue par un tiers : non faite. Le label `security-reviewed` reste absent.
2. Balayage exhaustif des logs et messages d'erreur : non fait.
3. Autorisation fine des routes authentifiées (au-delà de l'authentification) :
   non mesurée globalement ; les tests nommés couvrent des cas.
4. Validation juridique externe : non faite.
5. Test contre un stockage S3 réel et contre la base Neon : non fait (services
   externes indisponibles depuis cet environnement).
6. Déploiement Vercel : échoue en 0 s, avant compilation. Le connecteur Vercel
   refuse l'accès (« Access denied »), ce qui oriente vers un jeton ou une portée
   d'équipe côté fournisseur. Rien n'indique un défaut du code déployé, mais rien
   ne le prouve non plus.
