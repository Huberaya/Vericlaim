"""C13 — plans, quotas et journal d'usage.

Ce paquet contient la seule source de vérité des limites commerciales
(:mod:`app.billing.plans`), le journal d'usage qui les mesure
(:mod:`app.billing.usage`), l'état d'abonnement (:mod:`app.billing.subscriptions`),
l'application des quotas côté API (:mod:`app.billing.enforcement`) et les
adaptateurs de prestataire de paiement (:mod:`app.billing.providers`).

Deux règles gouvernent tout le paquet :

1. **Aucun dépassement n'est facturé.** Un quota atteint produit une erreur
   explicite (HTTP 402) qui nomme la métrique, la consommation, la limite, la
   date de remise à zéro et le chemin de mise à niveau. Il n'existe dans ce code
   aucune facturation à l'usage, aucun dépassement silencieux et aucune montée de
   gamme automatique.
2. **Rien n'est simulé silencieusement.** Le catalogue publie l'état de ses prix
   (``pricing_status``) ; le prestataire de paiement en vigueur est retourné par
   l'API ; l'adaptateur « local » refuse de démarrer en environnement de
   production.
"""
