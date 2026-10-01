# Conditions générales d'utilisation et de vente

> **MODÈLE NON PUBLIÉ.** Document contractuel : il doit être rédigé ou validé par un
> avocat avant toute mise en ligne. Il est fourni ici comme base de travail, avec les
> points sur lesquels **le produit impose une contrainte de fait** (marqués 🔒) : les
> ignorer produirait des conditions inexécutables.

## 1. Objet et définitions

**[À COMPLÉTER — rédaction standard : parties, définitions, documents contractuels
(CCU, DPA, annexe SLA), ordre de priorité.]**

## 2. Ce que le service fait — et ce qu'il ne fait pas 🔒

Le service produit une **analyse pré-réglementaire assistée par ordinateur** :

- il détecte des allégations dans vos documents, les confronte aux preuves que vous
  fournissez, et produit des verdicts **explicables**, rattachés à des règles publiées
  et versionnées ;
- il génère un rapport **scellé** (signature HMAC-SHA-256) et une référence de
  vérification publique.

Il **ne** fournit **pas** :

- un conseil juridique, ni une décision d'autorité ;
- une garantie de conformité : le rapport atteste la correspondance entre le document
  et une analyse persistée, **pas la véracité juridique des verdicts** ;
- une validation par un tiers : les références réglementaires du moteur sont en cours de
  revue juridique externe (C19) — **toute communication commerciale citant un texte
  précis est suspendue jusqu'à cette validation 🔒**.

## 3. Compte, accès, sécurité

**[À COMPLÉTER — rédaction standard.]** Contraintes réelles : authentification par SSO
OIDC ou mot de passe (scrypt) ; les jetons de session sont stockés hachés ; la
protection CSRF est obligatoire sur les écritures ; les rôles disponibles sont
propriétaire, administrateur, analyste et lecteur.

## 4. Usage acceptable

Le client s'engage à ne téléverser que des documents qu'il est en droit de traiter, et
à ne pas utiliser le service pour produire une allégation trompeuse. **L'analyse d'un
document n'autorise pas à en publier le contenu** : le service qualifie des allégations,
il ne valide ni une communication commerciale ni un étiquetage.

## 5. Prix, plans, quotas, facturation 🔒

Les plans et les quotas sont **appliqués par l'API** : un dépassement produit une
**erreur explicite** (refus de traiter de nouveaux documents, 402/429 selon le cas), et
**jamais une facture surprise**. Les paliers et prix en vigueur figurent au contrat ou
au devis ; le journal d'usage est consultable.

Résiliation : la fin du service prend effet **à la fin de la période payée** ; le client
conserve l'accès à ce qu'il a payé. Un **changement d'offre à la baisse** prend effet à
l'échéance, pas immédiatement (une baisse immédiate priverait le client de ce qu'il a
déjà réglé) 🔒.

## 6. Disponibilité et support

**[À COMPLÉTER — engagements de disponibilité, fenêtres de maintenance, délais de
réponse par offre.]** ⚠️ Aucun SLA n'est aujourd'hui mesuré ni outillé : les chiffres
publiés doivent être ceux qui peuvent être tenus et **constatés**. Le service support
(C15) n'est pas encore livré : ne pas promettre un canal qui n'existe pas.

## 7. Données du client

Le client reste propriétaire de ses documents. VeriClaim agit en **sous-traitant**
(DPA annexé). À la fin du contrat : restitution selon les modalités du DPA et effacement
**[À COMPLÉTER — délai]**.

## 8. Responsabilité 🔒

Plafond **[À COMPLÉTER]** : il ne peut pas être fixé plus bas que ce que le produit
prétend faire. En particulier, exclure toute responsabilité alors que le service produit
des verdicts « CRITIQUES » et des sanctions estimées serait contradictoire avec la
communication commerciale — l'exclusion doit être proportionnée et explicite.

## 9. Résiliation, force majeure, droit applicable

**[À COMPLÉTER — rédaction standard : résiliation pour manquement, réversibilité,
tribunal compétent, médiation le cas échéant.]**
