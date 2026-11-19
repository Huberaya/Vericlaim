# Politique de confidentialité

> **MODÈLE NON PUBLIÉ.** À faire relire (C12/C19) avant toute publication. Les champs
> `[À COMPLÉTER]` sont des obligations d'information (art. 13 et 14 RGPD) et ne peuvent
> être remplis que par l'entité qui exploite le service. Les affirmations techniques,
> elles, sont vérifiées dans le code et citées.

## 1. Qui est responsable de quoi

- Pour **votre compte, votre facturation et les journaux techniques** : VeriClaim est
  **responsable de traitement**. Contact : **[À COMPLÉTER — adresse protection des données]**.
- Pour **les documents que vous téléversez et les analyses produites** : **vous** êtes
  responsable de traitement, et VeriClaim agit comme **sous-traitant**, sur vos
  instructions. Ce partage est détaillé dans le DPA (`docs/legal/dpa.md`).

## 2. Quelles données sont traitées

| Catégorie | Exemples | Pourquoi |
| --- | --- | --- |
| Compte et organisation | nom, adresse e-mail, rôle, organisation | fournir le service et gérer les accès |
| Sécurité | identifiant de session (haché), empreinte d'agent utilisateur (SHA-256), horodatages | authentifier, détecter les accès anormaux. **Aucune adresse IP n'est stockée** |
| Facturation | plan, journal d'usage (documents, pages, analyses), factures | facturer, respecter les obligations comptables |
| Documents et analyses (sous-traitance) | documents téléversés, textes extraits, allégations, verdicts, rapports | produire l'analyse demandée |
| Demandes de droits | identité du demandeur, texte de la demande, décision | répondre et le prouver |

**Aucune donnée sensible au sens de l'art. 9 RGPD n'est demandée.** Aucun profilage
publicitaire, aucune revente, aucune donnée transmise à un service d'IA (voir §5).

## 3. Combien de temps

Les durées de conservation des **documents et analyses** ne sont pas fixées par
VeriClaim : elles sont **déclarées par votre organisation**, puis plafonnées par votre
offre (la durée appliquée est la plus courte des deux, et l'API publie les deux). Si vous
ne déclarez rien, **rien n'est purgé** : le produit ne choisit pas une durée à votre
place.

Durées propres à VeriClaim : **[À COMPLÉTER — sessions, journaux d'exploitation,
factures (durée comptable légale)]**, cohérentes avec le registre.

## 4. Qui y a accès

- Les membres de **votre** organisation, selon leur rôle (le cloisonnement est appliqué
  par la base de données elle-même, pas seulement par l'interface) ;
- l'éditeur, pour l'exploitation et le support, **sur autorisation** et avec trace dans
  le journal d'audit ;
- les sous-traitants techniques listés dans
  `docs/legal/sous-traitants-et-transferts.md` (hébergement, stockage objet, relais
  e-mail, prestataire de paiement).

## 5. Où sont les données, et sont-elles transférées

**[À COMPLÉTER — dépend du déploiement.]** À ce jour, l'environnement de référence est
auto-hébergé et la région par défaut déclarée est `eu`.

**Aucun transfert de document vers un tiers d'intelligence artificielle**: il n'existe
aucune dépendance d'inférence dans le produit, et l'extraction de texte comme l'analyse
sont exécutées localement. Deux tests l'interdisent et échouent si cela change
(`tests/test_data_subject_rights.py`).

## 6. Vos droits

Accès, rectification, effacement, limitation, portabilité, opposition — et le droit de
saisir l'autorité de contrôle. La procédure complète, avec ce qui est outillé et ce qui
reste manuel, est décrite dans `docs/legal/procedure-droits-des-personnes.md`.

Ce qui est **réellement** possible aujourd'hui :

| Droit | État |
| --- | --- |
| Accès | outillé : export complet, avec empreinte de ce qui est remis |
| Portabilité | outillé (JSON structuré) ; pas de format interopérable dédié |
| Effacement | outillé, refusé (409, motivé) si un gel légal protège le contenu |
| Rectification, limitation, opposition | **non outillés** : demandes enregistrées, suivies, traitées à la main |

Délai de réponse : **un mois**, prolongeable de deux mois motivés. Le délai est calculé
par le système à partir de la date de réception.

## 7. Sécurité

Chiffrement des documents au repos (obligatoirement activé hors développement),
cloisonnement par organisation appliqué en base, mots de passe hachés (scrypt), sessions
opaques avec protection CSRF, journal d'audit chaîné et vérifiable, rapports scellés par
signature HMAC-SHA-256.

**Ce qui manque, et qui est écrit ici plutôt que découvert plus tard** : il n'existe
**aucune sauvegarde du stockage objet** ; la purge des données est **manuelle** (aucun
ordonnanceur) ; les artefacts de rapports ne sont pas encore couverts par une politique
de rétention.

## 8. Modifications

Toute modification substantielle sera notifiée **[À COMPLÉTER — modalité : courriel,
bannière, préavis]**. Dernière mise à jour : **[À COMPLÉTER — date de publication]**.
