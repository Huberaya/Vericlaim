# VeriClaim — moteur réglementaire déterministe

Prototype exécutable d'un moteur d'audit des allégations environnementales. Le cœur combine un lexique explicite, des règles juridiques typées et versionnées, des contrôles de périmètre et de date, des critères probatoires, des Safe Harbors configurables, une remédiation par modèles de clauses et un journal d'audit chaîné par SHA-256. **Aucun LLM n'intervient dans le verdict.**

> **Important — aide à la conformité, pas avis juridique.** Un verdict de l'outil n'est ni une décision administrative, ni une certification, ni une preuve judiciaire automatiquement opposable. Les textes, supports réels, pièces, juridiction et transposition nationale doivent être contrôlés par une personne compétente avant diffusion.

## Démarrage rapide

À la racine du dépôt :

```bash
cp .env.example .env
# Remplacer POSTGRES_PASSWORD, AUTH_SESSION_SECRET et MINIO_ROOT_PASSWORD
# par des valeurs longues et aléatoires.
# Configurer OIDC_* pour pouvoir se connecter et exécuter un audit.
docker compose up --build
```

- Interface : <http://localhost:3000>
- API OpenAPI : <http://localhost:8000/docs>
- Vivacité : <http://localhost:8000/healthz> (le processus répond, sans toucher aux dépendances)
- Disponibilité : <http://localhost:8000/readyz> (base, révision Alembic, stockage, antivirus ; **503** si une dépendance requise échoue)
- Métriques : <http://localhost:8000/metrics> (agrégées, sans identifiant de locataire ; à restreindre à l'ingress en production)
- État SSO : <http://localhost:8000/api/v1/auth/status>
- Règles chargées : <http://localhost:8000/api/v1/engine/rules> — membre authentifié requis
- Console MinIO locale : <http://localhost:9001> — développement uniquement; les buckets restent privés

Compose démarre PostgreSQL, MinIO (stockage objet local), un bootstrap de buckets privés, ClamAV, un `document-worker` séparé pour l’OCR/extraction et un `analysis-worker` séparé pour la détection durable des allégations. Les workers attendent l’API saine (donc les migrations appliquées), n’exposent aucun port et utilisent PostgreSQL comme file durable — aucun Redis, Celery ou RabbitMQ n’est requis. Le premier démarrage de ClamAV télécharge ses signatures et peut donc prendre plus longtemps.

Compose applique la chaîne Alembic avant de démarrer l’API. En production, exécuter les migrations dans un job de déploiement unique avant de démarrer les réplicas applicatifs ; l’API refuse de démarrer si la révision Alembic attendue n’est pas appliquée et ne crée jamais silencieusement un schéma ou une base SQLite de secours.

Après connexion SSO et sélection d’une organisation active, les fichiers texte, PDF et images peuvent être envoyés sur `/api/v1/engine/evaluate`; ils sont validés (extension/MIME/signature), limités en taille et scannés par ClamAV **avant** toute extraction. Cette route reste éphémère : elle ne conserve pas la pièce. Les PDF scannés et images sont ensuite OCRisés par Tesseract (`fra+eng`). Chaque nouvelle trace d’analyse est rattachée à l’organisation active. L'OCR peut confondre une négation, un tiret, un pourcentage ou un symbole CO₂ : le texte extrait est renvoyé et doit être comparé visuellement à l'original.

### Lancer les tests du moteur

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

Le fichier `backend/pytest.ini` résout automatiquement le `PYTHONPATH`. Pour les tests isolés, utilisez explicitement `DATABASE_URL=sqlite:///:memory:` et `AUTO_CREATE_SCHEMA=true`. Une indisponibilité PostgreSQL ne déclenche jamais de bascule silencieuse vers SQLite : l'API répond **503** (`database_unavailable`). Le repli local n'existe que sous `ALLOW_LOCAL_SQLITE_FALLBACK=true`, jamais en staging/production.

Résultat mesuré : **202 tests passants** (`cd backend && python -m pytest -q`). Chaque garde de sécurité de la vague 1 est **éprouvée par mutation** : le test échoue si l'on réintroduit la faille qu'il protège. Un compteur de tests n'est pas une preuve de bout en bout ; le parcours réel est décrit dans [`docs/operations/release-checklist.md`](docs/operations/release-checklist.md) §6. La suite couvre le parcours de quarantaine/promotion, les rejets antivirus, l’expiration, l’isolation tenant/RBAC/CSRF, l’anti-TOCTOU par ETag, les policies S3 présignées, le multipart historique scanné, les queues PostgreSQL, les leases/retries, les workers, les hashes, les segments citables, les analyses versionnées déterministes, l’intégrité probante des rapports (signature HMAC, vérification publique) et le catalogue fournisseurs/produits idempotent. Un avertissement de dépréciation provenant de `starlette.testclient`/httpx reste à traiter lors d’une prochaine mise à niveau compatible.

### Migrations de base de données

```bash
cd backend
# DATABASE_URL doit pointer vers la base cible ; jamais vers une base client non sauvegardée.
alembic upgrade head
alembic current
```

`AUTO_CREATE_SCHEMA` est une aide locale/test uniquement. Les migrations Alembic sont la source de vérité pour les environnements partagés. Le modèle SaaS initial est documenté dans [`docs/architecture/ch01-foundation.md`](docs/architecture/ch01-foundation.md), l’identité/RBAC/RLS dans [`docs/architecture/ch02-identity-tenancy.md`](docs/architecture/ch02-identity-tenancy.md), la quarantaine documentaire dans [`docs/architecture/ch03-secure-document-ingestion.md`](docs/architecture/ch03-secure-document-ingestion.md), l’extraction durable dans [`docs/architecture/ch04-durable-document-extraction.md`](docs/architecture/ch04-durable-document-extraction.md), la détection d’allégations versionnée dans [`docs/architecture/ch05-persistent-claim-detection.md`](docs/architecture/ch05-persistent-claim-detection.md) et le catalogue achats tenant-scoped dans [`docs/architecture/ch06-catalog.md`](docs/architecture/ch06-catalog.md).

### SSO, organisation et accès au moteur

Le moteur n’est plus public : `POST /api/v1/engine/evaluate` exige une session SSO, une organisation active, la permission `audit:run` et un en-tête CSRF. `GET /api/v1/engine/rules` exige la permission `rules:read`.

En staging/production, définir obligatoirement un `AUTH_SESSION_SECRET` aléatoire non-placeholder (au moins 32 caractères), `AUTH_COOKIE_SECURE=true`, `FRONTEND_URL`, `CORS_ORIGINS`, `OIDC_ISSUER`, `OIDC_CLIENT_ID` et `OIDC_REDIRECT_URI`. Les URLs doivent être HTTPS, le callback OIDC doit utiliser l’hôte frontend et les wildcards CORS sont refusés.

Aucun mot de passe local n’est fourni. Un raccourci pilote sans identifiant existe pour le développement local (`POST /api/v1/auth/dev-login`, bouton « espace pilote ») : il est **désactivé par défaut hors de `development`/`test`**, activable localement par `ENABLE_DEV_LOGIN=false`, et **refusé au démarrage** en staging/production. Le routeur qui l’expose n’est pas enregistré dans ces environnements et le bouton est éliminé du bundle de production à la compilation. Un test de non-régression vérifie qu’aucune route `/api/v1/auth` ne répond à un appelant anonyme hors liste publique explicite.

Le flux et les routes sont détaillés dans [`docs/architecture/ch02-identity-tenancy.md`](docs/architecture/ch02-identity-tenancy.md). Une invitation crée un membership `invited`; l’envoi d’e-mail reste à connecter à un service externe et n’est pas prétendu comme effectué.

### Parcours documentaire persistant sécurisé

Le parcours `/api/v1/documents` est distinct de l’analyse éphémère. Il conserve des pièces dans deux buckets privés et ne les transforme en version exploitable qu’après validation cohérente de l’extension/MIME/signature, calcul SHA-256 et verdict propre ClamAV. Les URLs d’upload et de téléchargement sont présignées, courtes et tenant-scoped; les réponses de document/version n’exposent aucune clé objet persistante. Un formulaire S3 POST présigné comporte nécessairement une clé opaque générée serveur parmi ses champs, valable uniquement avec sa policy courte.

1. créer le document logique avec `POST /api/v1/documents` (`documents:manage`);
2. demander une version avec `POST /api/v1/documents/{id}/versions` et téléverser le formulaire retourné vers MinIO/S3;
3. appeler `POST /api/v1/document-uploads/{id}/complete` pour lancer les contrôles et la promotion;
4. le worker PostgreSQL réclame ensuite le job, vérifie à nouveau le hash de l’objet propre et extrait TXT/PDF/image hors API ; le statut passe par `pending` / `running` puis `completed`, `review_required` (OCR à relire) ou `failed` ;
5. consulter le document, les passages citables (`GET /api/v1/document-versions/{version_id}/segments`) ou obtenir une URL temporaire de téléchargement de l’original propre ; un gestionnaire peut relancer explicitement un échec via `POST /api/v1/document-versions/{version_id}/extraction/retry`.

`owner`, `admin` et `analyst` gèrent les documents; `viewer` les consulte. Toute mutation exige une session SSO, une organisation active et le CSRF. Un scanner ou stockage indisponible retourne `503` sans promouvoir le fichier; un fichier rejeté n’est jamais livré au parseur/OCR ni placé dans le bucket propre. Après promotion, le worker ne traite que l’objet propre dont le SHA-256 correspond encore à la version immuable.

Les variables `DOCUMENT_STORAGE_*`, `DOCUMENT_SCANNER_*`, `DOCUMENT_EXTRACTION_*` et `DOCUMENT_OCR_*` sont décrites dans [`.env.example`](.env.example). En staging/production, l’infrastructure retenue est **MinIO S3-compatible + ClamAV + PostgreSQL/RLS**, avec chiffrement serveur et endpoints TLS imposés au démarrage. Le diagramme de flux, les permissions et les limites sont documentés dans [`docs/architecture/ch03-secure-document-ingestion.md`](docs/architecture/ch03-secure-document-ingestion.md) et [`docs/architecture/ch04-durable-document-extraction.md`](docs/architecture/ch04-durable-document-extraction.md).

### Analyses persistantes et détection déterministe des allégations

Le parcours C5 transforme des **versions documentaires déjà extraites** (`completed` ou `review_required`) en dossier d’analyse versionné. Il ne relit jamais le binaire MinIO et ne passe jamais par le prototype éphémère `/api/v1/engine/evaluate` : il traite uniquement les `DocumentSegment` persistés par le worker C4.

1. envoyer `POST /api/v1/analyses` avec `document_version_ids` et un en-tête obligatoire `Idempotency-Key` ; l’API crée transactionnellement `Analysis`, `AnalysisVersion`, manifeste d’entrée SHA-256 et job PostgreSQL tenant-scoped, puis retourne **`202 Accepted`** ;
2. `analysis-worker` réclame un lease, réutilise `FactExtractor` segment par segment et publie les claims dans une transaction atomique ;
3. consulter `GET /api/v1/analyses/{analysisId}`, le snapshot `GET /api/v1/analyses/{analysisId}/versions/{version}` ou un passage `GET /api/v1/claims/{claimId}` ;
4. `POST /api/v1/analyses/{analysisId}/retry` crée toujours une **nouvelle** `AnalysisVersion`, sans écraser les claims, le manifeste ni le hash de résultat de la version précédente.

Chaque claim conserve le segment, la page, les offsets du transcript, le trigger lexical et les SHA-256 source/segment. Une détection issue d’un segment `image_ocr` est systématiquement `review_required`; aucune confidence probabiliste n’est fabriquée. `owner`, `admin` et `analyst` peuvent créer/relancer (`audit:run` + CSRF) ; les membres ayant `audit:read` peuvent consulter. L’interface de conservation documentaire propose ce parcours dès que l’extraction est terminée et affiche les passages persistés.

Le pipeline persistant (`vericlaim-analysis-engine-v2`, scope `claim_detection_and_regulatory_verdicts`) détecte les allégations citables, puis exécute le **moteur de règles déterministe sur ces allégations persistées** et stocke une ligne `analysis_verdicts` par couple (allégation, règle) : règle, référence légale, force normative, sévérité, Safe Harbor, contrôles probatoires, étapes de raisonnement, remédiation et sanction. Le contexte d'évaluation (date, support, juridiction) et l'empreinte du Rule Book sont figés à la création, ce qui rend une version rejouable et vérifiable. Le registre de preuves persisté (`evidence`) alimente le calcul : une pièce expirée ou rejetée est écartée et listée dans `evidence_manifest.excluded`, jamais ignorée en silence. Restent hors périmètre : LLM, RAG, déduplication sémantique, extraction de tableaux. Un verdict est un élément de pré-audit, jamais un avis juridique ni une certification. Voir [`docs/architecture/ch05-persistent-claim-detection.md`](docs/architecture/ch05-persistent-claim-detection.md).

### Lexique de détection mesuré (C9)

La détection est **lexicale et déterministe**, et elle est désormais **mesurée**. Le lexique vit dans `app/engine/lexicon.py` (`LEXICON_VERSION = lexicon-2026-09-30.1`), et `backend/tests/corpus/claim_detection_corpus.json` sert de référence versionnée : **60 cas positifs** (les 15 cas de l’audit + 45 formulations du marché) et **27 cas négatifs** dont 11 d’ambiguïté à tolérance nulle (couleur, descripteur alimentaire, durabilité au sens de longévité, marquage de conformité, raison sociale). Mesure publiée : **100 % de détection, 0 % de faux positifs**, avec un **cliquet** qui interdit toute régression sous la mesure de référence (un seuil à 90 % seul absorberait six points de régression : c’est mesuré, pas supposé).

Ce que l’audit avait trouvé et qui est corrigé : « 100 % de matière recyclée » n’était pas détecté ; les allégations de certification (`ECOLABEL`, `FSC`, `PEFC`, `ISO 14xxx`, `Cradle to Cradle`, `biosourcé`) n’existaient pas comme concept, donc la chaîne allégation → preuve restait vide. Le type `certification` a été ajouté, avec extraction **verbatim** du schéma, du numéro de certificat et de l’organisme, et une règle `RULE_EVIDENCE_SUSTAINABILITY_LABEL` fondée sur l’annexe I points 2 bis et 4 bis de la directive (UE) 2024/825 (texte officiel vérifié ; transposition française **non constatée**, donc aucune violation nationale n’est prononcée).

Limites, publiées dans la réponse d’API et détaillées dans `audit/C9_evidence.md` : un corpus écrit par l’auteur du lexique mesure la non-régression, pas la couverture du marché ; aucune détection des allégations implicites, des images ou des pictogrammes ; le certificat n’est jamais vérifié ; « Nous agissons pour le climat » (engagement d’entreprise) déclenche la prohibition AGEC lorsque le support est un emballage — effet de bord mesuré et épinglé par un test, **décision ouverte**.

### Score de confiance : une rubrique, jamais une probabilité (C10)

`confidence_score` était `NULL` partout et `review_required` ne se levait que sur les segments OCR : le client ne pouvait pas distinguer une détection solide d'une détection fragile, alors que l’écran affichait une liste d’allégations sans nuance. Le score est désormais une **rubrique déterministe et publiée** (`app/engine/detection_confidence.py`, `confidence-rubric-v1`) : une somme de facteurs nommés, chacun justifié par un élément lisible dans le texte — formulation ancrée de plusieurs mots, allégation chiffrée, qualificatif, numéro de certification, polarité, qualité de l’extraction. Chaque charge utile porte la liste des facteurs et la phrase qui dit ce que le nombre **n’est pas** : ni une probabilité, ni un score statistique, ni une appréciation du risque juridique. Preuve que ce n’est pas un modèle : les 80 scores du corpus de référence tombent tous sur une grille de 0,05, et la borne haute est 0,95 — le moteur ne prétend jamais tout savoir.

Les **quatre niveaux** (`high`, `medium`, `low`, `human_review_required`) sont tous atteints par des entrées réelles : 26 / 51 / 3 sur le corpus, plus les motifs de revue. Ce point a coûté une correction : dans la première version, le niveau bas était **inatteignable** (toute pénalité sous le seuil moyen imposait aussi une revue, qui écrasait le niveau) — quatre niveaux annoncés, trois affichables. La base dépend maintenant du déclencheur : un mot seul vaut moins qu’une formulation ancrée.

Une allégation qui demande un arbitrage est marquée `review_required` pour l’une de quatre raisons déterministes : segment OCR, extraction à revoir, **conflit de polarité** (le même type d’allégation asserté puis nié dans la même phrase, cas mesuré sur le corpus), ou **verdict réglementaire `REVIEW_REQUIRED`** — ce dernier corrige une contradiction observée où l’allégation était publiée « détectée » pendant que son verdict disait « à revoir ». Voir `audit/C10_evidence.md` (campagne de 10 mutations, 10/10 détectées) et `audit/produce_c10_evidence.py`. Limites publiées : les poids sont une convention documentée, non validée par un juriste ni corrélée à un taux d’erreur réel ; le niveau et les motifs **n’apparaissent pas encore dans le PDF** ; le rendu de l’interface est vérifié par compilation, pas par un test navigateur.

### Identité self-service : un inconnu peut entrer sans nous (C14)

L’audit avait relevé le défaut le plus bloquant pour un lancement : **l’invitation créait un membre `invited` sans envoyer d’e-mail**, et il n’existait ni inscription, ni mot de passe, ni réinitialisation. Un client ne pouvait donc pas ouvrir un compte ni inviter son équipe sans l’éditeur.

Le parcours complet existe désormais et il est **exécuté** : inscription (`POST /api/v1/auth/signup`), confirmation d’adresse, connexion par mot de passe, réinitialisation, changement de mot de passe, acceptation d’invitation. Les règles de mot de passe sont **publiées** (`GET /api/v1/auth/password-policy` : longueur, jeu de caractères, nombre d’échecs avant verrouillage, durée du verrouillage, schéma de hachage) — l’interface ne recopie pas ces valeurs, elle les lit, parce qu’une copie finit toujours par mentir.

Ce qui est vrai sur les secrets, et vérifié en base : les mots de passe sont hachés en **scrypt** (`n=2¹⁴`, `r=8`, `p=1`, paramètres stockés dans l’empreinte pour pouvoir les durcir plus tard), comparés en temps constant, jamais journalisés ; les jetons à usage unique (vérification 24 h, réinitialisation 1 h, invitation 7 j) ne sont stockés qu’en **SHA-256** — un dump de base ne contient aucun lien utilisable.

Ce qui est vrai sur les réponses, et vérifié par test : l’inscription et la demande de réinitialisation répondent **exactement la même chose** que l’adresse existe ou non (comparaison du corps complet, pas seulement du code HTTP), sinon le formulaire deviendrait un oracle d’existence de comptes ; le verrouillage après 8 échecs s’applique **aussi au bon mot de passe** ; une réinitialisation **révoque toutes les sessions** ; le changement de mot de passe exige le mot de passe courant **et** un jeton CSRF.

Ce qui est vrai sur les e-mails, sans embellissement : il n’existe **aucun relais SMTP dans cet environnement**. Chaque message est écrit dans la table `email_messages` avant toute tentative d’envoi, et son statut dit la vérité (`not_configured` = enregistré, non remis ; `failed` avec le code d’erreur ; `sent` seulement après remise réelle). L’inviteur reçoit ce statut dans la réponse et dans la chaîne d’audit : l’interface ne peut pas afficher une coche verte pour un message resté dans une file. Le transport SMTP lui-même **n’est pas éprouvé ici**, et c’est écrit tel quel dans `audit/C14_evidence.md`.

Preuves : `backend/tests/test_self_service_identity.py` (**20 tests**, dont le parcours d’acceptation complet) ; `audit/c14_mutations.py` (**16/16 mutations détectées**, chacune par le test attendu) ; `audit/produce_c14_evidence.py` rejoue le parcours en **HTTP réel à travers le proxy du frontend** (`curl → :3000 → uvicorn :8000`) et conserve la transcription dans `audit/C14_evidence_transcript.json`. La garde C1 (`tests/test_dev_login_gate.py`) liste désormais explicitement les routes d’authentification publiques et vérifie que chacune **refuse un appelant anonyme sans secret valide** — sans quoi il aurait suffi d’ajouter une route à la liste blanche pour ouvrir l’accès.

Décisions ouvertes : **2FA non implémentée** (le plan la mentionnait comme optionnelle) ; **aucune limitation de débit HTTP** sur les routes d’authentification publiques, le verrouillage étant par adresse et non par IP — à traiter avant exposition publique ; aucune purge planifiée des jetons et des messages.

### Politique de rétention : déclarée, jamais inventée

`GET /api/v1/pilot/retention-policy` renvoyait les mêmes littéraux à toutes les organisations : `dpo@vericlaim.ai`, `AES-256 / TLS 1.3`, `EU (Paris / Frankfurt)`, 5/10/5 ans et une date de revue fixe. **Ces valeurs étaient fausses pour tout client** et présentées comme un artefact de conformité ; une région d’hébergement publiée par l’éditeur est un engagement contractuel que personne n’a pris.

La table `organization_retention_policies` (RLS + FORCE, une ligne par organisation) contient désormais les valeurs **déclarées**. Aucune ligne ne veut dire « non déclaré » : l’API répond `null`, `configured=false`, et nomme les champs manquants dans `undeclared_fields`. La déclaration s’écrit par `PUT /api/v1/pilot/retention-policy` (permission `organization:manage` + CSRF) et conserve son auteur et son horodatage. La région d’hébergement, le standard de chiffrement et le contact DPO ne sont affichés que s’ils ont été déclarés : le schéma ne porte plus aucune valeur par défaut pour ces champs. Enfin, la réponse indique `automatic_deletion_implemented: false` — **aucune purge ni expiration automatique n’existe dans ce code**, et l’affirmer serait le même défaut, déplacé.

### Rapport probant signé et vérifiable

Un rapport n’est pas un fichier : c’est une **pièce rattachée à une analyse persistée**. À chaque génération (`POST /api/v1/reports/pdf`, `POST /api/v1/reports/dossier`), le service émet une référence publique (`vc-…`), signe le matériau (`schema_version`, identifiant de version d’analyse, `result_sha256`, empreinte de Rule Book, version de moteur, horodatage UTC canonique, référence) en **HMAC-SHA-256** sur `canonical_json`, et persiste la ligne `reports` correspondante (référence, signature, identifiant de clé, empreintes signées, `sha256` du fichier livré). La référence et la signature sont imprimées dans le document.

`GET /api/v1/reports/verify/{reference}` est **public** — un tiers qui contrôle un rapport n’a pas de compte VeriClaim — et répond à trois questions distinctes, sans jamais les confondre : la référence est-elle connue et la signature valide (`known`, `signature_valid`), l’analyse a-t-elle changé depuis l’émission (`analysis_unchanged`), le fichier fourni est-il intact (`file_matches`, seulement si l’empreinte est transmise). Une référence inconnue retourne `known=false` et **ne vaut jamais validation**. Un rapport rendu sous un Rule Book ou un moteur déprécié porte une bannière explicite citant l’empreinte d’origine et la version courante. Une revue humaine validée, contestée ou outrepassée (`validations`) est imprimée dans la carte d’évaluation concernée.

Limites assumées, publiées dans la réponse de vérification et dans `audit/C7_evidence.md` : la clé appartient à l’éditeur (origine serveur et intégrité du lien, **pas** une non-répudiation par un tiers ni un horodatage qualifié RFC 3161) ; `analysis_unchanged` ne compare que trois champs de la version, pas le contenu des verdicts ; il n’existe ni rotation ni révocation de clé (changer `REPORT_SIGNING_KEY` invalide la vérification des rapports antérieurs) ; la signature ne certifie pas la véracité juridique des verdicts — un verdict faux, signé, reste signé. `REPORT_SIGNING_KEY` est **obligatoire** en staging/production et refusée si elle est faible ou de gabarit ; elle n’apparaît jamais dans `describe_runtime()`.

### Catalogue fournisseurs et produits (C6.1)

Le catalogue achats active les ressources persistantes `Supplier` et `Product` déjà présentes dans le modèle SaaS. Chaque création exige `Idempotency-Key`, est isolée par organisation, journalisée et protégée par les permissions `catalog:read` / `catalog:manage` ; owner, admin et analyst gèrent le catalogue, viewer le consulte.

- `POST /api/v1/suppliers` et `POST /api/v1/products` retournent `201`, ou `200` lors d’un replay idempotent ;
- les listes sont cursor-based (`GET /api/v1/suppliers`, `GET /api/v1/products`) ;
- un produit doit appartenir à un fournisseur actif du même tenant et ne peut pas être réaffecté ensuite ;
- les archivages sont des soft deletes : les documents et analyses historiques gardent leur contexte ; un fournisseur avec produits actifs ne peut pas être archivé ;
- le coffre documentaire peut maintenant rattacher facultativement une nouvelle pièce à un fournisseur et à l’un de ses produits ; le lancement C5 depuis cette pièce conserve ce contexte.

Ce catalogue ne vérifie ni l’identité ni la conformité d’un fournisseur et ne génère aucun score, verdict ou enrichissement externe. La gestion de preuves persistantes et les liens claim → preuve restent hors de C6.1. Voir [`docs/architecture/ch06-catalog.md`](docs/architecture/ch06-catalog.md).

### Développer l'interface

```bash
cd frontend
npm ci
npm run dev
# Ou build de production :
npm run build && npm run start
```

`NEXT_PUBLIC_API_URL` cible l'API locale (`http://localhost:8000` par défaut). Le client utilise une URL relative sur les previews non locales, relayée par la réécriture Next vers `BACKEND_URL`; cela évite qu'un navigateur distant appelle son propre `localhost`. Vérification de types et build : `npm run typecheck && npm run build`.

Le dashboard propose deux démos cliquables. La démo « conforme » est un jeu de données déclaré, pas un constat de conformité : sans registre de certificats corroboré côté serveur et sans métadonnées d'ACV suffisantes, l'API peut légitimement demander une revue ou des preuves supplémentaires.

## Exemple API — texte JSON

> Cette route nécessite une session opaque obtenue par le callback OIDC et le cookie CSRF associé. Le frontend gère les cookies `credentials: include` et l’en-tête `X-CSRF-Token`; ne fabriquez pas de token à la main.

```bash
curl -X POST http://localhost:8000/api/v1/engine/evaluate \
  -H 'Content-Type: application/json' \
  -H 'Cookie: vericlaim_session=<session-obtenue-via-oidc>; vericlaim_csrf=<csrf>' \
  -H 'X-CSRF-Token: <csrf>' \
  -d '{
    "source_text": "Bouteille 100% biodégradable.",
    "context": {
      "as_of_date": "2026-09-24",
      "jurisdiction": "FR",
      "surface": "packaging",
      "consumer_facing": true,
      "product_identifier": "SKU-001"
    },
    "evidence": {"items": [], "legal_person": true}
  }'
```

Le rapport renvoie notamment `overall_compliance`, `risk_score`, `violations_count`, chaque décision par couple allégation/règle, les étapes d'inférence, les vérifications probatoires, une clause fournisseur, une exposition connue non cumulative et un manifeste d'audit. `extracted_source_text` permet à l'interface de mettre en évidence les segments OCRisés.

### Exemple API — document ou scan

```bash
curl -X POST http://localhost:8000/api/v1/engine/evaluate \
  -H 'Cookie: vericlaim_session=<session-obtenue-via-oidc>; vericlaim_csrf=<csrf>' \
  -H 'X-CSRF-Token: <csrf>' \
  -F 'document=@etiquette-scan.pdf' \
  -F 'context_json={"as_of_date":"2026-09-24","jurisdiction":"FR","surface":"packaging","consumer_facing":true}' \
  -F 'evidence_json={"items":[],"legal_person":true}'
```

Cette route éphémère valide extension/MIME/signature et soumet le fichier à ClamAV avant tout parseur/OCR; une panne antivirus échoue fermé. Les fichiers sont lus en mémoire, limités à 15 Mo et 25 pages pour les PDF; ils ne sont pas conservés comme pièces probatoires. L'empreinte du document est incluse dans le rapport. Les métadonnées de preuves ajoutées séparément ne prouvent pas le contenu du fichier. Pour conserver une pièce et son historique, utiliser le parcours documentaire présigné décrit ci-dessus.

## Conception du moteur

### Pipeline

1. **Extraction déterministe** — `fact_extractor.py` repère des segments lexicaux, conserve leurs offsets, leur type, la polarité négative, les signaux de compensation et certains faits chiffrés. Chaque détection reste un fait candidat, non une conclusion juridique.
2. **Qualification** — `rule_book.py` associe l'occurrence à des `RegulatoryRule` immuables : identifiant stable, force normative, article, juridiction, date d'application, périmètre, sévérité, preuves attendues, Safe Harbors et sanction le cas échéant.
3. **Arbre d'inférence** — `inference_evaluator.py` consigne détection → périmètre → date → règle → preuves → Safe Harbor → verdict. Les règles absolues AGEC passent avant l'évaluation de tout dossier probatoire.
4. **Validation probatoire** — `proof_validator.py` vérifie les champs déclarés d'ACV, de certificat, de filière et de neutralité; il ne certifie pas les analyses, chiffres, méthodes ou documents sous-jacents.
5. **Remédiation** — `legal_remediation.py` produit des explications, des réécritures à trous (sans inventer de pourcentage) et des clauses fournisseurs déterministes.
6. **Piste d'audit** — la base enregistre hashes de la source, du manifeste des preuves et du rapport, plus un hash chaîné au précédent. Les enregistrements restent consultables par le DBA et cette chaîne n'est pas un horodatage qualifié ni une signature électronique.

Le score est un **indice de priorité de revue**, fondé sur des poids de sévérité fixes et le nombre de règles actionnables. Ce n'est pas une probabilité d'infraction. Les amendes non cumulatives ne sont pas additionnées; les dommages civils ne sont pas chiffrés.

## Règles livrées

| Identifiant | Nature du contrôle |
|---|---|
| `RULE_AGEC_BIODEGRADABLE` | Interdiction sur produit/emballage, aucune preuve ne crée de dérogation. |
| `RULE_AGEC_NATURE_FRIENDLY` | Interdiction des formules équivalentes à « respectueux de l'environnement » sur le support visé. |
| `RULE_EU_GENERIC_CLAIM` | Allégations génériques, à date-gater depuis le 27 septembre 2026; Safe Harbor uniquement si la licence est corroborée par un registre serveur et couvre le produit et la claim. |
| `RULE_EU_CARBON_NEUTRAL_COMPENSATION` | Interdiction date-gatée de la claim produit fondée sur une compensation hors chaîne de valeur; pas de Safe Harbor par crédits carbone. |
| `RULE_FR_CARBON_NEUTRAL_DISCLOSURE` | Règle française actuelle de publicité de neutralité, article L. 229-68; bilan, trajectoire et transparence sont contrôlés séparément de la future règle UE. |
| `RULE_EU_COMPARATIVE_LCA` | Garde-fou comparatif d'ACV multicritère ISO 14040/14044, **advisory only** et sans amende propre. |
| `RULE_ISO_RECYCLABLE_PERCENTAGE` | Garde-fou de filière de collecte, tri et traitement disponible; norme volontaire, à mettre à jour pour l'édition courante. |
| `RULE_EVIDENCE_QUANTIFIED_CLAIM` | Contrôle interne de preuve pour les pourcentages climatiques; absence d'ACV = rejet conditionnel de publication, pas infraction automatique. |

## Corrections juridiques explicites par rapport au brief initial

Le brief contient des chiffres/références qu'il ne faut pas inscrire comme droit positif sans vérification. Le Rule Book évite volontairement ces erreurs :

1. **AGEC — montant.** Le plafond actuellement codé pour un manquement aux obligations de l'article L. 541-9-1 est de **3 000 € pour une personne physique et 15 000 € pour une personne morale**, au titre de l'article L. 541-9-4-1 (version modifiée en juillet 2026). Le montant de 100 000 € n'est pas le plafond AGEC de cette règle : il relève notamment du régime de publicité de neutralité carbone de l'article L. 229-69. Cette sanction peut elle-même être portée jusqu'aux dépenses de l'opération illégale, selon la procédure et les faits. Sources : [L. 541-9-4-1, Légifrance](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043959912/2026-09-24), [L. 229-69, Légifrance](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043960258).
2. **AGEC — référence réglementaire.** L'interdiction législative figure à L. 541-9-1 et l'article réglementaire en vigueur exprimant la règle sur les produits neufs destinés aux consommateurs est **R. 541-230**. `R. 541-221` n'est pas utilisé ici comme référence actuelle de l'interdiction. Source : [L. 541-9-1](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041555718), [R. 541-230](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000049389990).
3. **Directive (UE) 2024/825.** Le moteur connaît la date d'application prévue au **27 septembre 2026**, mais ne traite pas automatiquement la directive comme une règle nationale française déjà transposée. Par défaut, `EU_2024_825_FR_TRANSPOSITION_STATUS=unknown`; après contrôle d'une source officielle, un administrateur peut configurer `implemented` ou `not_implemented`. Lorsque le statut est inconnu/non confirmé, le résultat UE passe en `REVIEW_REQUIRED`, pas en constat automatique de violation française. Source : [texte français EUR-Lex, directive 2024/825](https://eur-lex.europa.eu/eli/dir/2024/825/oj/fra).
4. **Green Claims.** COM(2023) 166 est une **proposition**, pas la directive 2024/825 et pas un fondement adopté pour une sanction. Les négociations et le statut de retrait ont été incertains; le moteur marque donc la règle comparative `PROPOSAL_ONLY`, sans sanction ni décompte d'infraction. Il conserve le seuil ACV comparatif comme politique de publication prudente. Source : [COM(2023) 166, EUR-Lex](https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=COM%3A2023%3A166%3AFIN).
5. **ISO 14021.** L'édition ISO 14021:2016 a été retirée et une édition 2026 publiée. Le contrôle demandé sur l'édition 2016 est conservé uniquement comme garde-fou de norme volontaire et doit être rapproché de l'édition 2026 et des règles françaises de catégorie avant déploiement. Sources : [ISO 14021:2016 — statut retiré](https://www.iso.org/standard/66652.html), [ISO 14021:2026](https://www.iso.org/standard/14021).
6. **ACV et conclusion légale.** Une ACV ISO 14044 est un seuil probatoire interne choisi pour les affirmations chiffrées/comparatives du prototype; l'absence de ce fichier ne signifie pas automatiquement qu'une infraction a été commise. L'exactitude, la méthodologie, les données, la représentativité et la présentation réclament une revue humaine.

## Safe Harbor et registre de certificats

Un numéro de licence fourni dans le corps de la requête n'est jamais considéré comme vérifié. Le registre doit être contrôlé et configuré **côté serveur**. Format possible pour `VERICLAIM_CERTIFICATE_REGISTRY_JSON` :

```json
[
  {
    "scheme": "EU_ECOLABEL",
    "license_number": "NUMERO_VERIFIE_DANS_LE_REGISTRE_OFFICIEL",
    "valid_from": "2025-01-01",
    "valid_until": "2027-01-01",
    "product_identifiers": ["SKU-001"],
    "product_categories": ["packaging"],
    "relevant_claim_types": ["generic_environmental"],
    "issuer": "organisme compétent",
    "registry_name": "instantané vérifié le AAAA-MM-JJ",
    "source_url": "URL du registre",
    "officially_recognised": true
  }
]
```

Ne mettez dans cette configuration que des données vérifiées par un processus interne fiable. La preuve doit couvrir le SKU/catégorie, être valide à la date d'évaluation et démontrer une performance pertinente pour le sens exact de la claim. Même un certificat valide ne contourne pas l'interdiction AGEC de la mention « biodégradable ».

## Configuration sensible

- `EU_2024_825_FR_TRANSPOSITION_STATUS`: `unknown` par défaut; configurer seulement après validation juridique de la mesure française en vigueur.
- `VERICLAIM_CERTIFICATE_REGISTRY_JSON`: registre de confiance serveur; par défaut vide, donc aucun Safe Harbor certificate n'est accordé.
- `DATABASE_URL`: PostgreSQL dans Compose et obligatoire en staging/production afin d’appliquer la RLS; SQLite est réservé au développement/test explicite.
- `AUTO_CREATE_SCHEMA`: `true` uniquement en développement/test (refusé hors de ces environnements) ; `false` pour toute base partagée, mise à jour par Alembic.
- `MAX_UPLOAD_BYTES` et `MAX_PDF_PAGES`: bornes d'extraction OCR et de l’ingestion documentaire; la taille est aussi imposée par la policy d’upload présigné puis recontrôlée côté serveur.
- `DOCUMENT_STORAGE_BACKEND`, `DOCUMENT_STORAGE_ENDPOINT`, `DOCUMENT_STORAGE_PUBLIC_ENDPOINT`, `DOCUMENT_QUARANTINE_BUCKET`, `DOCUMENT_CLEAN_BUCKET`: frontière S3/MinIO. Les buckets de quarantaine et propre doivent être différents et privés. L’endpoint public est celui présent dans l’URL présignée du navigateur, sans obliger l’API à exposer son endpoint interne.
- `DOCUMENT_STORAGE_SSE_MODE` (`aes256` ou `aws:kms`) et `DOCUMENT_STORAGE_SSE_KMS_KEY_ID`: chiffrement serveur requis hors développement/test; `aws:kms` exige une clé.
- `DOCUMENT_SCANNER_MODE=clamav`, `DOCUMENT_CLAMAV_HOST`, `DOCUMENT_CLAMAV_PORT`, `DOCUMENT_CLAMAV_TIMEOUT_SECONDS`: contrôle antivirus requis en staging/production; `test` est réservé à `APP_ENV=test`.
- `DOCUMENT_UPLOAD_TTL_SECONDS` et `DOCUMENT_DOWNLOAD_TTL_SECONDS`: durées de capacités temporaires, à conserver courtes (plafonds applicatifs : 60 min / 15 min).
- `DOCUMENT_EXTRACTION_MAX_ATTEMPTS`, `DOCUMENT_EXTRACTION_RETRY_BASE_SECONDS`, `DOCUMENT_EXTRACTION_LEASE_SECONDS`, `DOCUMENT_EXTRACTION_POLL_SECONDS`: budget d’essais, backoff, reprise après crash et cadence du worker PostgreSQL. Le lease doit couvrir le pire OCR configuré.
- `ANALYSIS_DETECTION_MAX_ATTEMPTS`, `ANALYSIS_DETECTION_RETRY_BASE_SECONDS`, `ANALYSIS_DETECTION_LEASE_SECONDS`, `ANALYSIS_DETECTION_POLL_SECONDS`: budget d’essais, backoff, lease et cadence du worker de détection déterministe; ce worker ne lit que les segments C4 persistés.
- `DOCUMENT_OCR_TIMEOUT_SECONDS`, `DOCUMENT_OCR_RENDER_SCALE`, `DOCUMENT_MAX_IMAGE_PIXELS`, `DOCUMENT_SEGMENT_MAX_CHARS`: bornes de coût, de mémoire et de granularité; elles ne rendent pas l’OCR juridiquement fiable.
- `AUTH_SESSION_SECRET`, `AUTH_COOKIE_SECURE`, `FRONTEND_URL`, `CORS_ORIGINS`, `OIDC_*`: session OIDC, cookies, callback et origine frontend ; voir le Chantier 2.

En production, définir des identifiants de base de données robustes, TLS, contrôle d'accès, politique de rétention, chiffrement, sauvegarde et journalisation d'accès. Le prototype fournit maintenant une authentification OIDC configurable, une isolation tenant applicative/RLS PostgreSQL, un stockage d’objets documentaire mis en quarantaine et une orchestration OCR/extraction durable à base PostgreSQL. Il ne fournit pas encore de DLP, sandbox de contenu actif, extraction structurée de tableaux, rapprochement automatique des claims/preuves, SCIM/SAML ou fournisseur OIDC préconfiguré.
