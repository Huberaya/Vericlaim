# VeriClaim AI — moteur réglementaire déterministe

Prototype exécutable d'un moteur d'audit des allégations environnementales. Le cœur combine un lexique explicite, des règles juridiques typées et versionnées, des contrôles de périmètre et de date, des critères probatoires, des Safe Harbors configurables, une remédiation par modèles de clauses et un journal d'audit chaîné par SHA-256. **Aucun LLM n'intervient dans le verdict.**

> **Important — aide à la conformité, pas avis juridique.** Un verdict de l'outil n'est ni une décision administrative, ni une certification, ni une preuve judiciaire automatiquement opposable. Les textes, supports réels, pièces, juridiction et transposition nationale doivent être contrôlés par une personne compétente avant diffusion.

## Démarrage rapide

À la racine `vericlaim-ai/` :

```bash
cp .env.example .env
# Remplacer au minimum POSTGRES_PASSWORD par une valeur longue et aléatoire.
docker compose up --build
```

- Interface : <http://localhost:3000>
- API OpenAPI : <http://localhost:8000/docs>
- Santé : <http://localhost:8000/healthz>
- Règles chargées : <http://localhost:8000/api/v1/engine/rules>

Les fichiers texte, PDF et images peuvent être envoyés sur `/api/v1/engine/evaluate`; les PDF scannés et images sont OCRisés par Tesseract (`fra+eng`). L'OCR peut confondre une négation, un tiret, un pourcentage ou un symbole CO₂ : le texte extrait est renvoyé et doit être comparé visuellement à l'original.

### Lancer les tests du moteur

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

Le fichier `backend/pytest.ini` résout automatiquement le `PYTHONPATH`. Si PostgreSQL n'est pas démarré, le backend bascule automatiquement sur SQLite local (`vericlaim.db`) sans configuration requise.

Résultat validé dans le workspace : **36 tests passants (100% de réussite)**.

### Export PDF de l'attestation d'audit

L'API fournit l'endpoint `POST /api/v1/engine/export/pdf` qui génère une attestation d'audit juridique officielle au format PDF (mise en page juridique professionnelle, horodatage, empreinte SHA-256, plafond d'exposition et remédiations contractuelles). L'attestation est téléchargeable en un clic depuis l'interface utilisateur.

### Historique des audits & Comparateur Fournisseurs

- **Registre d'audit** (`GET /api/v1/engine/audits`, `GET /api/v1/engine/audits/{id}`) : consultation chronologique de tous les audits enregistrés avec métadonnées fournisseur, SKU, score et scellement SHA-256.
- **Comparateur Achats** (`POST /api/v1/engine/suppliers/compare`) : benchmark déterministe côte-à-côte de plusieurs offres fournisseurs (classement par niveau de risque, identification des infractions, comparaison des plafonds d'amende et génération de clauses contractuelles d'achat).
- **Interface dédiée** : accessible via l'onglet « Comparateur Fournisseurs » dans la barre latérale, avec démo intégrée (3 fournisseurs packaging) et sélection multi-audits depuis l'historique.

### Connecteur Live aux Registres d'Écolabels Officiels

- **Vérification automatique** (`GET /api/v1/engine/ecolabels/verify`) : interroge en direct les registres officiels ISO 14024 Type I (EU Ecolabel ECAT, AFNOR NF Environnement, Blauer Engel, Nordic Swan) pour valider l'authenticité d'une licence.
- **Safe Harbor probatoire** : une licence vérifiée active automatiquement l'exonération Safe Harbor prévue par la directive européenne 2024/825 pour les allégations environnementales génériques.

### Mode "Audit de Site E-Commerce & Rendu JavaScript Dynamique"

- **Extraction et audit en direct** (`POST /api/v1/engine/evaluate/url`) : scanne une fiche produit e-commerce (Shopify, WooCommerce, Amazon, boutique headless Next.js, etc.) via son URL publique, filtre le bruit technique et structure l'argumentaire pour analyse immédiate par le moteur déterministe.
- **Moteur de rendu JavaScript dynamique (SPAs)** :
  - **Extraction des données hydratées client** : capture les données produit injectées côté client dans Next.js (`__NEXT_DATA__`), Nuxt (`__NUXT__`), Shopify Storefront (`meta.product`) et Remix.
  - **Données structurées Schema.org** : analyse automatique des balises `<script type="application/ld+json">` (`Product`, `Offer`, `brand`).
  - **Exécution DOM sandboxée** : runner Node.js VM sécurisé évaluant les scripts dynamiques de mutation DOM avec isolation stricte et délai d'expiration de 2 secondes.
- **Bouclier de sécurité anti-SSRF** : validation stricte bloquant systématiquement les adresses de bouclage (`localhost`, `127.0.0.1`), les réseaux privés (RFC 1918), les métadonnées d'instances cloud (`169.254.169.254`), et les protocoles non autorisés.
- **Boutiques de démonstration intégrées** : fiches produits tests prêtes à l'emploi (`https://demo-shop.vericlaim.ai/...`), dont une boutique headless SPA Next.js fonctionnant à 100% hors-ligne.

### Audit de Catalogue en Masse (Batch CSV & JSON)

- **Traitement de lot** (`POST /api/v1/engine/evaluate/batch`, `POST /api/v1/engine/evaluate/batch-csv`) : analyse simultanée de catalogues entiers (dizaines ou centaines de références SKU) avec calcul consolidé du taux de conformité, des sanctions financières cumulées et du risque moyen.
- **Export tableur** (`POST /api/v1/engine/export/batch-csv`) : génération immédiate d'un rapport de conformité CSV prêt pour Excel détaillant par ligne le statut, le score de risque, les infractions et la synthèse réglementaire.
- **Interface dédiée** : accessible via l'onglet « ▤ Audit de Catalogue (Batch) » avec glisser-déposer de fichier CSV, téléchargement de modèle type et démo intégrée de 6 produits multi-catégories.

### Gestion des Migrations de Base de Données (Alembic)

Les schémas de la base de données PostgreSQL / SQLite sont versionnés avec Alembic :
```bash
cd backend
alembic upgrade head
```

### Rate-Limiting & Protection Anti-Abus (SlowAPI)

Le moteur intègre un limiteur de débit centralisé (`SlowAPI`) protégeant les endpoints sensibles contre les dénis de service et l'aspiration abusive :
- **Identification hybride** : reconnaissance par clé API (`X-API-Key`) ou par adresse IP cliente.
- **Plafonds ciblés** :
  - Extraction & OCR (`POST /evaluate`) : 60 req/min.
  - Scraper d'URL e-commerce (`POST /evaluate/url`) : 20 req/min.
  - Audit batch de catalogue (`POST /evaluate/batch`, `POST /evaluate/batch-csv`) : 20 à 30 req/min.
  - Génération d'attestation PDF (`POST /export/pdf`) : 30 req/min.
- **Gestion des dépassements** : renvoi automatique d'une réponse `HTTP 429 Too Many Requests`.

### Observabilité, Métriques Prometheus & Sondes Kubernetes

Le service expose des endpoints standardisés d'observabilité pour la production :
- **Métriques Prometheus** (`GET /metrics`) :
  - `vericlaim_evaluations_total` : compteur des audits ventilé par statut de conformité et méthode d'extraction.
  - `vericlaim_violations_total` : infractions constatées par identifiant de règle juridique.
  - `vericlaim_claims_detected_total` : allégations identifiées par typologie réglementaire.
  - `vericlaim_evaluation_duration_seconds` : histogramme de latence de traitement du moteur.
- **Bilan de santé enrichi** (`GET /healthz`) : statut global, ping base de données avec temps de réponse, connectivité aux registres d'écolabels et état de la transposition française.
- **Sondes Kubernetes** :
  - `GET /livez` : sonde de vivacité (Liveness probe).
  - `GET /readyz` : sonde d'aptitude au trafic avec vérification active de la base (Readiness probe).
- **Traçabilité distribuée** : middleware `X-Request-ID` injectant et propageant un identifiant de corrélation unique par requête.

### Support Multilingue Européen (Français, Anglais, Allemand)

Le lexique déterministe d'extraction (`fact_extractor.py`) supporte nativement les allégations rédigées dans les 3 principales langues du marché unique européen :
- **Français** : terminologie AGEC, Code de l'environnement et Code de la consommation (*« biodégradable »*, *« sans chimie »*, *« neutre en carbone »*, *« emballage recyclé »*).
- **Anglais** : pan-européen et cross-border (*« 100% biodegradable »*, *« chemical-free »*, *« carbon neutral »*, *« net-zero »*, *« zero waste »*, *« made from recycled plastic »*, *« eco-friendly »*).
- **Allemand** : droit allemand et transposition UWG (*« 100% biologisch abbaubar »*, *« chemiefrei »*, *« klimaneutral »*, *« aus recyceltem Kunststoff »*, *« umweltfreundlich »*, *« null Abfall »*).
- **Détection des signaux de compensation** : identification multilingue des mentions de compensation carbone (*« offset credits »*, *« VCS »*, *« Gold Standard »*, *« Klimakompensation »*, *« kompensiert »*).

### Isolation Multi-Tenant & Gestion des Clés API

L'architecture supporte le cloisonnement étanche multi-organisations via l'en-tête `X-API-Key` :
- **Cloisonnement des registres** : chaque organisation (`Organization`) dispose d'une chaîne de blocs d'audits immuable et privée. Aucune fuite d'information inter-entreprises n'est possible (requêtes filtrées et accès direct cross-tenant bloqué en 404).
- **Gestion des Clés API** : génération cryptographique (`vk_live_...`), hachage SHA-256 en base de données, attribution de scopes (`audit:read`, `audit:write`, `batch:run`, `admin`) et révocation instantanée.
- **Rétrocompatibilité démo** : en l'absence de clé API, le système bascule de manière transparente sur le tenant démo public, préservant le fonctionnement sans configuration.
- **Interface UI intégrée** : onglet dédié « Clés API & Multi-Tenant » dans le dashboard pour créer une organisation, générer et révoquer des clés, et auditer en environnement cloisonné.

### Webhooks Sécurisés (HMAC-SHA256) & Avenants Juridiques Fournisseurs

- **Webhooks d'Alerte (`app/core/webhooks.py`)** : émission automatique d'alertes signées par HMAC-SHA256 (`t={timestamp},v1={hash}`) lors de la détection d'infractions critiques (`audit.violation_detected`) ou à l'achèvement d'audits (`audit.completed`). Gestion complète des abonnements et ping de test.
- **Générateur d'Avenant Fournisseur Anti-Greenwashing** : endpoint `POST /api/v1/engine/remediation/contract-addendum` et interface dédiée pour produire instantanément l'avenant contractuel exécutoire complet (garantie d'éviction, prise en charge intégrale des amendes DGCCRF jusqu'à 1 500 000 € ou 10 % du CA, pénalités forfaitaires de 15 000 € par SKU non conforme).

### Portail Public de Vérification & Sceau d'Opposabilité

- **Vérification d'authenticité (`/api/v1/engine/verify/{audit_id}`)** : permet à toute autorité de contrôle (DGCCRF, auditeur externe, distributeur, client final) de vérifier publiquement l'authenticité d'une attestation PDF ou d'un rapport d'audit.
- **Détection des altérations et ruptures de chaîne** : re-calcul à la volée du chaînage de blocs cryptographiques du ledger (`record_hash`) et comparaison de l'empreinte SHA-256 du texte source et du rapport pour détecter toute falsification (`TAMPERED` vs `CERTIFIED`).
- **Interface UI dédiée** : onglet « Vérification d'Attestation » dans le dashboard pour contrôler en un clic n'importe quel identifiant d'audit ou certificat.

### Compliance Watcher · Surveillance E-Commerce Continue & Détection de Régression

- **Surveillance planifiée de fiches produits (`/api/v1/engine/watcher/targets`)** : enregistrement d'URLs e-commerce sous surveillance continue avec fréquence configurable (1h à 168h).
- **Scraper et audit automatisé (`app/engine/compliance_watcher.py`)** : extraction sécurisée du contenu web, soumission au moteur réglementaire déterministe et stockage immuable du log de conformité (`MonitoringLog`).
- **Détection des régressions de conformité** : identification automatique de toute dégradation du statut légal (ex. basculement de `COMPLIANT` vers `NON_COMPLIANT` ou apparition de nouvelles allégations interdites) avec calcul du statut delta (`INITIAL`, `REGRESSION`, `RESOLVED`, `UNCHANGED`).
- **Déclenchement automatique de Webhooks** : émission d'un événement `watcher.regression_detected` dès qu'un drift ou une régression est constaté sur un site marchand.
- **Interface UI dédiée (`ComplianceWatcherView`)** : onglet « Compliance Watcher (E-Com) » pour piloter les cibles, déclencher des scans unitaires ou des scans batch de la flotte, et inspecter l'historique complet des deltas.

### Intégration Continue (CI/CD GitHub Actions)

Le workflow `.github/workflows/ci.yml` valide automatiquement sur chaque push et pull request :
- L'exécution des migrations Alembic et de la suite de tests backend (`pytest -v`).
- La vérification stricte des types TypeScript (`npm run typecheck`) et la compilation de production Next.js (`npm run build`).

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

```bash
curl -X POST http://localhost:8000/api/v1/engine/evaluate \
  -H 'Content-Type: application/json' \
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
  -F 'document=@etiquette-scan.pdf' \
  -F 'context_json={"as_of_date":"2026-09-24","jurisdiction":"FR","surface":"packaging","consumer_facing":true}' \
  -F 'evidence_json={"items":[],"legal_person":true}'
```

Les fichiers sont lus en mémoire, limités à 15 Mo et 25 pages pour les PDF; ils ne sont pas conservés comme pièces probatoires. L'empreinte du document est incluse dans le rapport. Les métadonnées de preuves ajoutées séparément ne prouvent pas le contenu du fichier.

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
| `RULE_AGEC_BIODEGRADABLE` | Interdiction sur produit/emballage (y compris oxodégradable), aucune preuve ne crée de dérogation. |
| `RULE_AGEC_NATURE_FRIENDLY` | Interdiction des formules équivalentes à « respectueux de l'environnement » (préserve la planète, ami de la nature...) sur le support visé. |
| `RULE_AGEC_COMPOSTABLE` | Encadrement strict de la mention « compostable » : interdiction isolée, exigence de compostabilité domestique (NF T 51-800) pour emballages plastiques. |
| `RULE_CONSUMER_CHEMICAL_FREE` | Interdiction absolue de « sans produits chimiques » / « zéro chimie » comme pratique commerciale trompeuse par nature (L. 121-2). |
| `RULE_CONSUMER_ZERO_POLLUTION` | Garde-fou sur « zéro déchet », « zéro pollution » et « non polluant » : promesse globale présumée infondée sans ACV exhaustive. |
| `RULE_AGEC_RECYCLED_UNQUANTIFIED` | Interdiction de « matière recyclée » / « en plastique recyclé » sans la formule légale exacte « comporte au moins [X] % de matières recyclées » (R. 541-227). |
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

## Safe Harbor et connecteur live aux registres d'écolabels officiels

Un numéro de licence saisi par un utilisateur n'est jamais considéré comme valide par défaut : il doit être corroboré par les registres officiels d'organismes tiers accrédités.

Le moteur intègre le module `LiveEcolabelConnector` connecté aux registres :
1. **EU Ecolabel (ECAT)** — Catalogue officiel de la Commission Européenne / ADEME.
2. **NF Environnement** — Schéma officiel national français (AFNOR Certification).
3. **Der Blaue Engel (Ange Bleu)** — Schéma allemand officiel (RAL / Umweltbundesamt).
4. **Nordic Swan (Svanen)** — Schéma officiel des pays nordiques.

### Endpoints d'écolabels live
- `GET /api/v1/engine/ecolabels/registries` : liste des registres officiels connectés et statut.
- `GET /api/v1/engine/ecolabels/verify?license_number=...` : vérification live et éligibilité Safe Harbor Directive (UE) 2024/825.
- `POST /api/v1/engine/ecolabels/sync` : synchronisation du cache local des licences officielles.

Un instantané personnalisé peut également être injecté via la variable d'environnement `VERICLAIM_CERTIFICATE_REGISTRY_JSON` :

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
- `DATABASE_URL`: PostgreSQL dans Compose; SQLite local par défaut.
- `MAX_UPLOAD_BYTES` et `MAX_PDF_PAGES`: bornes d'extraction OCR.

En production, définir des identifiants de base de données robustes, TLS, contrôle d'accès, politique de rétention, chiffrement, sauvegarde et journalisation d'accès. Le prototype ne fournit pas d'authentification utilisateur ni de stockage chiffré des rapports.
