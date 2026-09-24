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
pytest -q
```

Résultat validé dans le workspace : **13 tests passants**.

### Développer l'interface

```bash
cd frontend
npm ci
npm run dev
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
- `DATABASE_URL`: PostgreSQL dans Compose; SQLite local par défaut.
- `MAX_UPLOAD_BYTES` et `MAX_PDF_PAGES`: bornes d'extraction OCR.

En production, définir des identifiants de base de données robustes, TLS, contrôle d'accès, politique de rétention, chiffrement, sauvegarde et journalisation d'accès. Le prototype ne fournit pas d'authentification utilisateur ni de stockage chiffré des rapports.
