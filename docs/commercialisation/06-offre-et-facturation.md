# Offre et facturation — cadrage sans promesse

Le dépôt évalué ne démontre pas de facturation, paiement, abonnement, calcul de taxes, gestion d'impayés ou provisioning conditionné au paiement. Ne pas annoncer ces capacités ni inférer leur existence depuis une autre branche ou PR.

Un pilote contractuel éventuel nécessite des décisions explicites : périmètre, nombre d'utilisateurs et documents, accompagnement, responsabilité de revue humaine, prix et taxes, durée, support, sous-traitants, limites de service et restitution. Tous ces paramètres restent à décider ; aucun tarif ni SLA n'est établi par cet audit.

Avant une offre autonome : concevoir les états de souscription, droits et révocation, événements de paiement idempotents, rapprochement comptable, sécurité des webhooks, tests et procédures d'exception ; soumettre l'ensemble à revue produit, finance et juridique. La PR #7 contient des candidats à examiner séparément et n'est pas intégrée.
