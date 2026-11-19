## Objet

Récupérer l'ensemble des fonctionnalités de la PR #7 sur `main` actuel (`6f2cade`),
corriger les incompatibilités, et livrer **une seule branche fusionnable**.

La PR #7 n'est pas fusionnée : ses fichiers ont été réintégrés un par un, les
corrections des PR #14, #15 et #16 sont conservées, et aucun conflit n'a été
arbitré « au plus récent » sans lecture.

## Ce que fait la branche

Moteur de détection et couverture probatoire · verdicts réglementaires et
traçabilité des règles · coffre documentaire (extraction, antivirus, rétention) ·
analyses persistées immuables · rapports signés et file de génération asynchrone ·
droits RGPD et effacement · identité self-service · support et observabilité ·
abonnements, plans, quotas et journal d'usage · revue humaine, matrice de preuves,
pages publiques.

## Défauts réels trouvés et corrigés

Ce sont des défauts présents dans la PR #7 telle qu'elle était, pas des
préférences de style.

**1. Trou d'isolation entre organisations.** Cinq tables portant `organization_id`
n'avaient **aucune** politique RLS : `identity_tokens`, `email_messages`,
`billing_subscriptions`, `billing_provider_events`, `billing_checkout_sessions`.
Ce sont des jetons d'authentification, une boîte d'envoi et l'état commercial.
`infra/neon/verify_runtime.py` le signalait déjà ; la vérification exécutée contre
un PostgreSQL jetable listait exactement ces cinq noms. Migration `a1b9c7e34d52`.
Les trois tables dont la colonne est nullable conservent une politique permissive
sur les lignes **sans** organisation (un jeton émis avant rattachement doit rester
utilisable) et interdisent la lecture des lignes d'une autre organisation.

**2. Garde d'abonnement inerte.** `documents.py` montait
`Depends(require_active_subscription)` — la fabrique, pas la dépendance. La garde
n'était donc jamais évaluée sur l'import de documents ni la création de versions,
alors que les tests la croyaient active. `route_has_billing_gate` a été écrit pour
détecter exactement ce cas et le test de facturation l'a confirmé.

**3. Deux drapeaux pour une seule route pilote.** `ENABLE_DEV_LOGIN` conditionnait
l'enregistrement, `AUTH_DEV_LOGIN_ENABLED` le gestionnaire. Quand ils divergeaient,
la route disparaissait alors que le gestionnaire l'autorisait encore — un écart de
configuration invisible. Le drapeau unique de `main` est retenu, avec un opt-in
explicite même en développement (fail closed).

**4. Code mort et imports manquants dans `reports.py`.** Quatre symboles de
signature (`new_verification_reference`, `key_id`, `file_sha256`,
`SIGNATURE_SCHEMA_VERSION`) étaient utilisés sans import, et des blocs de rendu
synchrone — remplacés par la file C22 — traînaient au milieu de docstrings.

**5. Doublons de fusion.** Deux blocs entiers dupliqués dans `core/metrics.py`
(la seconde copie écrasait la première, et supprimer la mauvaise faisait
disparaître les jauges métier), `get_db` importé deux fois dans `reports.py`,
`read_bytes` déclaré deux fois dans le protocole de stockage.

**6. Contacts RGPD et support lus puis jetés.** Les variables étaient lues dans
`get_settings()` mais jamais transmises au `Settings` : les endpoints publiaient
une adresse vide, et une demande de droits devenait impossible à adresser.

**7. Tests qui mesuraient autre chose que le comportement.**
`test_runtime_environment_safety.py` exigeait le repli SQLite que `main` a supprimé
(ADR-002 : il contourne la RLS) — il verrouille désormais l'invariant inverse.
`test_schema_parity.py` rechargeait le module de configuration **dans le
processus**, laissant deux configurations coexister, d'où des 401 sur des requêtes
pourtant authentifiées ; les migrations s'exécutent maintenant dans un
sous-processus, sans état partagé. La CI comparait la tête Alembic à un identifiant
écrit en dur.

## Preuves exécutées

Valeurs sorties de commandes rejouables. Commit de référence : `ba933b3`.

| Vérification | Résultat |
|---|---|
| Suite backend, PostgreSQL 16.2 jetable | **737 passed, 9 skipped, 0 failed** |
| Suite backend, environnement de la CI (SQLite) | **706 passed, 40 skipped, 0 failed** |
| Preuves RLS inter-organisations (rôle applicatif réel) | **15/15**, dont 4 nouvelles |
| Typecheck frontend (`tsc --noEmit`) | vert |
| Build frontend (`npm run build`) | vert, 12 pages |
| Ruff (`F811,F841,F821,E9`) | **aucune erreur** — mesuré, pas supposé |
| Recette de qualification (plan de données réel) | **54/54** |
| Répétition de restauration PostgreSQL | **`FAILURES: none`** (38 tables RLS, `rows_visible_without_tenant_context: 0`) |
| Surface sans identité (113 chemins interrogés) | 20 routes justifiées, 0 ouverte par inadvertance |
| Discipline de migration | tête unique `c5f1a8e37b910`, 21 révisions linéaires |
| Jobs CI | 5/5 verts : backend, frontend, RLS, recette, restauration |

Les 9 tests ignorés sous PostgreSQL (40 sous SQLite) exigent des services
externes — S3 réel, ClamAV, base Neon — et ne sont pas présentés comme exécutés.

**Aucune migration n'a été appliquée à une base réelle.** `alembic_version` n'a
pas été modifié, l'historique n'a pas été réécrit.

Deux corrections de cette passe méritent d'être signalées, parce qu'elles portent
sur des affirmations fausses de la description précédente :

- elle annonçait « Ruff — aucun » alors qu'il restait 42 variables mortes. Le
  chiffre a été mesuré, puis corrigé (42 retraits, aucun changement de
  comportement) ;
- elle annonçait la recette à 54/54 et la restauration à `FAILURES: none` sans
  que la restauration soit effectivement verte à ce moment-là : le vérificateur
  de chaîne d'audit ne reconnaissait qu'un des trois schémas de hachage acceptés
  par l'application, et déclarait falsifiée une chaîne saine.

## Revue de sécurité

`docs/audits/2026-11-19-security-review-pr7-integration.md` déroule la liste de
contrôle de `SECURITY.md` §5 point par point, en distinguant **vérifié**,
**non couvert** et **limite connue**. Le label `security-reviewed` n'est pas
apposé : cette revue fournit les constats pour qu'un tiers puisse la faire, elle
ne s'auto-certifie pas.

Le constat le plus important de cette revue concerne les trois tables dont
`organization_id` est nullable. Leur politique vaut
`organization_id IS NULL OR organization_id = contexte`. Mesuré avec le rôle
applicatif :

- contexte posé → une organisation ne voit que ses lignes et les orphelines,
  jamais celles d'une autre : **la lecture croisée entre organisations est
  refusée** ;
- aucun contexte → les orphelines et les lignes déjà rattachées sont visibles.
  C'est ce que fait l'application pour consommer un jeton ou appliquer un
  webhook.

Ce second point n'est **pas** une protection, et il est désormais écrit comme tel
dans la migration et fixé par un test. Il est inatteignable par l'API — aucune
route ne lit ces trois tables — et il n'a pas été « corrigé » à la hâte : une
politique par utilisateur toucherait le verrouillage des tentatives de connexion,
sans preuve de non-régression disponible à cette échelle.

## Réserves — à ne pas confondre avec des bloqueurs de fusion

Aucun de ces points ne bloque la fusion ; aucun ne doit être lu comme résolu.

1. **Planification des sauvegardes non prouvée.** La restauration est prouvée
   contre moto, pas contre un stockage S3 réel. RPO/RTO restent non mesurés. Les
   crons d'exécution des workers sont désormais déclarés en cadence quotidienne
   (seule acceptée par Hobby) et ne valent que comme rattrapage : un cycle ne
   dure que le temps de la requête.
2. **Preuve TLS non exécutée.** La vérification runtime contrôle les rôles et la
   RLS, pas la terminaison TLS du fournisseur.
3. **Validation juridique externe non réalisée.** Les verdicts réglementaires
   s'appuient sur un référentiel interne ; aucune revue par un juriste n'a eu lieu.
4. **Paiement non encaissé.** Aucun encaissement réel n'a été simulé ni effectué ;
   l'adaptateur « local » refuse de démarrer en production.
5. **Revue de sécurité à faire.** Cette PR ne porte pas l'étiquette
   `security-reviewed`. Les preuves ci-dessus sont fournies pour qu'une revue
   réelle puisse être conduite ; elle n'a pas été faite.

## Hors périmètre volontaire

La PR #15 n'est pas rouverte. Aucune intervention sur Neon, Clerk, Vercel, les
secrets, les variables d'environnement ou les paramètres de production.

## Mission 10 — blocages de sécurité levés (19 novembre 2026)

**Dernier commit de code : `d148be7`** (rapport et corrections éditoriales après, sans changement de code) · rapport complet : `docs/audits/2026-11-19-mission-10-rapport.md`

### RLS d'identité : deux défauts mesurés, corrigés

La suite PostgreSQL tournait avec le rôle `postgres`, qui **contourne la RLS** :
elle validait l'application sur une base qui ne se comportait pas comme la
production. Sous les rôles réels, deux défauts apparaissaient, et un reproche
supposé n'apparaissait pas :

- l'**inscription échouait** (`new row violates row-level security policy`) ;
- une organisation pouvait lire le **jeton orphelin** d'un utilisateur d'une
  autre organisation ;
- le constat « sans contexte, toutes les lignes sont visibles » est en revanche
  **faux** : vérifié, non reproduit.

Correction par **séparation d'identité**, pas par politique permissive : rôle
`vericlaim_system` provisionné hors migration (`infra/neon/04_system_role.sql`),
droits accordés **par colonne**, aveugle aux corps d'e-mail et aux tables métier,
incapable de supprimer un jeton ou de réécrire l'audit. Côté applicatif, une
politique unique `FOR ALL` avec `USING` **et** `WITH CHECK` stricts : plus aucune
ligne sans organisation n'est visible d'une organisation.

Preuves : **9 tests PostgreSQL** sur les rôles réels (dont invitation, écriture
croisée refusée, audit en ajout seul) et **15 preuves d'isolation**. Éprouvées
par mutation : 6 échecs si les parcours repassent sur le rôle applicatif, 1 si la
politique redevient permissive, 1 si le rôle système reçoit un `SELECT` global.

### Vercel : le blocage était une cadence de cron

Trois `crons` en `* * * * *`, introduits par l'intégration de la PR #7. Hobby
refuse plus d'une exécution par jour, **au déploiement**, avant compilation :
chaque Preview échouait en 0 s. Cadence passée au quotidien ; déploiement
`success` au SHA final.

**Preview :** https://vericlaim-9ij6zkgcy-baya-ibraimmayoanouedu-8164s-projects.vercel.app
— état Vercel `success`. Elle n'a **pas été ouverte** : `curl` vers
`*.vercel.app` échoue à la poignée de main TLS depuis cet environnement, et le
connecteur Vercel répond « Access denied ». Le statut prouve que le build a
abouti, pas qu'un parcours fonctionne.

### CI au SHA final

Backend, Frontend, RLS, Recette, Répétition de restauration, Vercel : **verts**.
« Chemins sensibles modifiés » : **rouge volontairement** — le label
`security-reviewed` exige une revue humaine qui n'a pas eu lieu.

### Verdict : NO-GO pour la fusion

Les trois points techniques sont traités et prouvés. Ce qui manque n'est pas un
correctif : c'est une **revue humaine réelle** et une **ouverture de la Preview**
par quelqu'un qui y a accès. Poser le label sans revue reviendrait à simuler une
approbation.
