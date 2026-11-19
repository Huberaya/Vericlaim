# Dossier légal — ce qui est écrit, ce qui reste à valider

**État : MODÈLES RÉDIGÉS, NON PUBLIÉS, NON VALIDÉS.** Aucun de ces documents n'a été
relu par un juriste ni par un délégué à la protection des données. Ils ne sont pas
publiés tels quels : les placeholders `[À COMPLÉTER — …]` sont des obligations légales
(identité de l'éditeur, hébergeur, assurance, médiateur de la consommation, etc.) que
seule l'entité qui commercialise peut remplir.

## Règle appliquée dans tout ce dossier

1. **Aucune valeur n'est inventée.** Un numéro d'immatriculation, une adresse, une
   durée ou une localisation qui n'est pas connue est écrite `[À COMPLÉTER — …]`.
   Un document légal faux est pire qu'un document absent : il engage.
2. **Ce qui est écrit sur le produit est vérifiable dans le code.** Chaque affirmation
   technique porte sa preuve (fichier, test) : « aucun transfert de document à un tiers
   IA » n'est pas une promesse commerciale ici, c'est une absence de dépendance que
   n'importe qui peut vérifier (`backend/requirements.txt`, `backend/app/**`).
3. **Ce que le produit ne fait pas est écrit.** Les droits non outillés, la purge non
   planifiée, l'absence de sauvegarde du stockage objet : ces limites figurent dans les
   documents, pas seulement dans les notes internes.
4. **Aucune conformité n'est déclarée.** Un audit technique ne peut pas écrire
   « conforme au RGPD ». Il peut écrire ce qui est mis en œuvre, et ce qui ne l'est pas.

## Contenu

| Fichier | Objet | État |
| --- | --- | --- |
| `mentions-legales.md` | Identification de l'éditeur et de l'hébergeur | modèle, identité à compléter |
| `politique-de-confidentialite.md` | Données traitées, finalités, durées, droits | modèle, durées réelles reprises du code |
| `cgu-cgv.md` | Conditions d'utilisation et de vente, SLA, résiliation | modèle, à valider (contrats) |
| `politique-cookies.md` | Cookies réellement posés par l'application | **vérifié sur le code** : 2 cookies, tous deux nécessaires |
| `dpa.md` | Accord de sous-traitance (art. 28) | modèle, annexes alimentées par le registre |
| `registre-des-traitements.md` | Registre art. 30 | rempli à partir du code, à valider |
| `sous-traitants-et-transferts.md` | Sous-traitants, localisation, transferts | matrice vérifiée, dépend des choix d'hébergement |
| `procedure-droits-des-personnes.md` | Procédure opérationnelle des droits | **implémenté** : routes, tests, délais calculés |

## Ce que la validation externe doit couvrir (C19 + C12)

- exactitude des durées de conservation et de leur base légale ;
- qualification des rôles : VeriClaim est **sous-traitant** de l'organisation cliente
  pour les documents qu'elle téléverse, et **responsable de traitement** pour ses propres
  données (comptes, facturation, journaux techniques). Cette double qualité doit être
  vérifiée, pas supposée ;
- rédaction des CGU/CGV, du DPA et des clauses de transfert ;
- le caractère suffisant de l'information donnée sur les ** limites du produit** (le
  rapport atteste une correspondance document↔analyse, pas la véracité juridique des
  verdicts : ce point est déjà écrit dans le produit et dans `ch10`).

## Rappel de portée

Ces documents sont ceux d'un **modèle de service**, pas d'un déploiement : l'hébergeur,
la région, le relais e-mail et le prestataire de paiement dépendent de l'exploitation et
doivent être renseignés en conséquence. Tant qu'ils ne le sont pas, la page publique
`/legal` affiche explicitement les champs manquants.
