# Capacités et limites produit

| Fonction | Ce qui existe dans le dépôt | Limite commerciale |
|---|---|---|
| Évaluation éphémère | Moteur déterministe, règles et rapport sur entrée fournie | Non conservée comme résultat réglementaire persistant ; revue juridique nécessaire. |
| Documents | Quarantaine, scan avant promotion, extraction durable et segments citables | Les intégrations S3/ClamAV/OCR réelles n'ont pas été testées ici ; OCR à confronter à l'original. |
| Analyses | Versions immuables, jobs et détection de claims avec provenance | Détection lexicale, pas de verdict juridique persistant ni taux d'exhaustivité validé ; PDF/dossier persistants refusés (409). |
| Catalogue et revue | Fournisseurs, produits, preuves liées, décisions humaines, requêtes | Identité et qualification des fournisseurs/réviseurs non certifiées ; un lien ne prouve pas sa suffisance. |
| Pilote | Inventaire JSON, totaux et priorité de revue selon liens manquants | Versions historiques comptées séparément ; pas un score de conformité ou de risque légal. |
| Journal | Chaînage SHA-256 des événements présents | Pas d'ancrage externe ni signature ; suppression ou réécriture globale non exclues. |
| Identité | Sessions, rôles et contexte tenant ; RLS PostgreSQL | Configuration et isolation réelles à démontrer dans l'environnement déployé. Les clés API ne sont pas un moyen d'authentification démontré. |
| Rétention | Statut `not_configured` et inventaire JSON | Aucune durée, localisation ou conformité RGPD garantie ; export sans binaires. |

Les règles sont identifiées par une empreinte technique du contenu, pas par une validation juridique signée. Les interfaces et API ont des champs historiques conservés pour compatibilité ; leur nom ne constitue pas une promesse.
