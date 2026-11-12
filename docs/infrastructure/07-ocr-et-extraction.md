# 07 — OCR et extraction documentaire

**Date :** 12 novembre 2026
**SHA testé :** `fa31e0a8145fc86cda04b466989af100cf1eeeda`

## 1. Chaîne réelle d'extraction

`app/engine/document_extractor.py` — moteur `vericlaim-document-extractor-v1` :

- **PDF** : `PyMuPDF` (fitz). Texte natif page par page ; **si une page n'a pas
  de texte, elle est rendue en image puis passée à l'OCR** ;
- **images** (`.png`, `.jpg`, `.tif`, `.webp`) : OCR systématique ;
- **`.txt`** : décodage UTF-8 (BOM), UTF-8, puis Windows-1252 ;
- OCR via `pytesseract` (donc le binaire `tesseract`, installé par
  `backend/Dockerfile` : `tesseract-ocr`, `tesseract-ocr-fra`, `tesseract-ocr-eng`) ;
- bornes appliquées **avant** tout parseur : taille, nombre de pages, pixels
  d'image (protections anti « decompression bomb »), caractères extraits ;
- timeout OCR par page et échéance de lease couvrant l'ensemble du document ;
- **canonicalisation** en segments citables (`document_segments`) avec page,
  offsets, type ;
- **`requires_human_review = True` dès qu'une page a nécessité l'OCR**.

Ce dernier point est une décision produit importante : une transcription OCR n'est
jamais présentée comme une preuve fiable. C'est documenté dans le code et repris
dans la politique de revue humaine.

## 2. Ce qui est réel dans mon environnement

| Élément | Réel ? | Détail |
|---|---|---|
| `pytesseract` + binaire `tesseract` | **non** | binaire absent, `apt` indisponible, aucun paquet Python ne le fournit |
| moteur **Tesseract lui-même** | **oui** | `tesserocr` (PyPI) embarque `libtesseract 5.5.4` + `leptonica 1.87.0` |
| données d'entraînement | **oui** | `eng.traineddata` (tessdata_fast) installé dans `.recette/tessdata` |
| PyMuPDF | **oui**, version réelle | rendu des pages, lecture du texte natif |
| Pillow | **oui** | limites de pixels |
| chaîne d'orchestration (worker, segments, revue humaine) | **oui** | exécutée en E2E sur la pile réelle |

Autrement dit : **le moteur d'OCR est réel et fonctionne** (prouvé ci-dessous) ;
c'est le chemin d'appel exact (`pytesseract` → binaire système) que je n'ai pas pu
exécuter, faute du binaire.

## 3. Preuves exécutées

### 3.1 Le moteur lit réellement du texte

```
tesserocr.tesseract_version() → tesseract 5.5.4 / leptonica-1.87.0
langues installées            → ['eng']
image « RECYCLABLE » rendue   → OCR = "RECYCLABLE"
```

### 3.2 Comportement par format (tests `backend/tests/test_ocr_engine_recette.py`)

| Cas | Attendu | Observé |
|---|---|---|
| `.txt` UTF-8 | `TEXT_FILE`, pas de revue humaine | **conforme** |
| PDF texte | `PDF_TEXT`, pas de revue humaine | **conforme** |
| PDF scanné (image seule) | OCR requis → revue humaine | moteur disponible : `PDF_OCR`, `requires_human_review=True` ; **sur la pile E2E, moteur absent → échec explicite** (voir §3.3) |
| image PNG | `OCR_IMAGE` | conforme quand le moteur est joignable |
| document vide | refus | **refus explicite** |
| extension non supportée (`.exe`) | refus | **refus** |
| PDF corrompu | refus | **refus** |
| document trop volumineux | refus avant parsing | **refus** |
| PDF au-delà de `MAX_PDF_PAGES` | refus | **refus** |

### 3.3 Ce qui se passe dans le pipeline réel quand l'OCR est absent

C'est le scénario le plus instructif du chantier, parce qu'il est exactement celui
d'un déploiement mal configuré.

Un PDF scanné a été importé sur la pile réelle (201, stocké, scanné, promu), puis
le worker d'extraction a tourné. Résultat observé :

```
extraction_status        = failed
extraction_error_code    = extraction_unusable
job_status               = failed
segment_count            = 0
```

Interprétation : **aucun faux succès**. Le document n'est ni marqué lu, ni
« revue humaine », ni disponible pour une analyse. Il est en échec terminal avec
un code stable, et il reste rejouable (`POST /document-versions/{id}/extraction/retry`).

C'est exactement la propriété exigée : *ne jamais faire passer une extraction ratée
pour une extraction réussie*.

## 4. Taux d'extraction et performance

| Mesure | Valeur |
|---|---|
| PDF texte (1 page, ~150 mots) | 1 segment, `method=PDF_TEXT`, worker < 5 s |
| Segments par page | dépend de `DOCUMENT_SEGMENT_MAX_CHARS` (2000 par défaut) |
| Perte de contenu | offsets et types conservés ; contenu vide → échec explicite |
| OCR du moteur seul | < 2 s pour une page A4 à 4× |
| Latence worker visible en E2E | quelques secondes (poll 1 s) |

Je n'ai **pas** mesuré de taux d'extraction granulaire sur un corpus réel : ce
serait un chiffre inventé. Un corpus de recette représentatif doit être constitué
par l'opérateur avant d'annoncer un taux.

## 5. Ce qui n'est pas de l'authenticité documentaire

Rappel explicite : **l'OCR n'authentifie rien**. L'extraction produit du texte
citable ; elle ne prouve ni l'origine, ni l'intégrité juridique du document. C'est
pourquoi :

- une page OCR est marquée `requires_human_review` ;
- l'analyse détecte des allégations, elle ne valide pas une preuve ;
- les rapports rappellent la nécessité d'une revue humaine.

## 6. Points de vigilance

| Point | Priorité |
|---|---|
| Langues : `TESSERACT_LANGUAGES` par défaut `fra+eng` — seule `eng` est installable ici | P2 (le Dockerfile installe `fra` et `eng`) |
| Le chemin `pytesseract` (sous-processus, timeout) n'a pas été exécuté | P2 — couvert par les tests unitaires existants sur les échecs |
| Aucun corpus de recette mesuré | P2 pour un pilote (à constituer) |
| Les PDF avec polices non embarquées peuvent nécessiter l'OCR | P3 |

## 7. Verdict de la phase

- **L'extraction fonctionne réellement** pour les formats textuels : PDF natif et
  TXT extraits, segmentés, persistés et consultables par l'API.
- **Le moteur OCR est réel** (Tesseract 5.5.4) et lit effectivement du texte.
- **Le chemin d'appel production (`pytesseract`) n'a pas été exécuté ici**, faute
  du binaire ; il l'est par `backend/Dockerfile` en CI et en Compose.
- Une **indisponibilité OCR est explicitement qualifiée** et se traduit par un
  échec terminal rejouable, jamais par un faux succès — la décision de lancement
  peut donc s'appuyer sur ce comportement.
