"""Centre d'aide et engagements de réponse — écrits depuis le produit, pas à côté.

C15 demandait quatre choses : un canal de contact, un centre d'aide, un SLA affiché par
plan, et un bouton de contact contextualisé. Ce module porte les deux premières, avec une
règle : **le contenu d'aide ne peut pas décrire un produit qui n'existe pas**.

Deux mécanismes la tiennent :

1. **Les erreurs citées existent.** Chaque entrée de ``FREQUENT_ERRORS`` porte un
   ``error_code`` et un ``api_path`` ; un test vérifie que le code est réellement produit
   par le fichier de route cité. Une aide qui explique une erreur disparue envoie
   l'utilisateur chercher un message qu'il ne verra jamais.
2. **Les délais sont des objectifs, et le disent.** Aucune disponibilité n'est mesurée :
   publier « 99,9 % » serait un mensonge vérifiable. ``SLA_POLICY`` publie des objectifs
   de première réponse **indicatifs, non contractuels**, plus une phrase qui l'explique.
   Le test de surface interdit par ailleurs tout chiffre de disponibilité.
"""

from __future__ import annotations

from typing import Final

from app.billing.plans import PLANS

#: Objectifs de première réponse, par plan et par catégorie — **indicatifs**.
#:
#: Ce ne sont pas des engagements contractuels : rien ne mesure aujourd'hui le temps de
#: première réponse réellement tenu (aucun ordonnanceur, aucune alerte d'échéance). Les
#: publier comme un SLA serait promettre une mesure qui n'existe pas.
SLA_POLICY: Final[dict[str, dict[str, int]]] = {
    "starter": {"default": 48, "incident": 24},
    "pro": {"default": 24, "incident": 8},
    "enterprise": {"default": 8, "incident": 4},
}

#: Phrase publiée à côté des délais. Sans elle, un délai affiché devient une promesse.
SLA_STATEMENT: Final[str] = (
    "Ces délais sont des **objectifs de première réponse**, indicatifs et non "
    "contractuels. Aucun engagement de disponibilité n'est pris : la disponibilité du "
    "service n'est ni mesurée ni garantie aujourd'hui. Un délai affiché sans mesure est "
    "un objectif, pas un SLA."
)

#: Ce que le support ne peut pas faire, écrit plutôt que découvert.
SLA_EXCLUSIONS: Final[tuple[str, ...]] = (
    "Aucune astreinte : un incident signalé hors des heures ouvrées est traité au "
    "premier créneau, pas dans la minute.",
    "Aucune reprise sur incident payante : le support ne se substitue pas à la "
    "restauration de sauvegarde — qui n'existe pas encore pour le stockage objet.",
    "Aucun conseil juridique : l'interprétation d'un texte réglementaire relève d'un "
    "conseil, pas du support produit.",
)


def policy_for(plan_code: str) -> dict[str, int]:
    """Objectifs applicables à un plan d'abonnement.

    Un plan inconnu n'obtient pas silencieusement le délai le plus court : la fonction
    lève, et l'appelant décide. C'est le même choix que pour la facturation : une
    organisation sans plan connu n'est pas servie au jugé.
    """

    policy = SLA_POLICY.get(plan_code)
    if policy is None:
        raise KeyError(f"aucun objectif de réponse publié pour le plan {plan_code!r}")
    return policy


def first_response_hours(plan_code: str, category: str) -> int:
    """Délai applicable : celui de la catégorie, sinon l'objectif par défaut du plan."""

    policy = policy_for(plan_code)
    return int(policy.get(category, policy["default"]))


def published_sla() -> list[dict[str, object]]:
    """Les objectifs, tels qu'ils sont publiés : par plan, avec le libellé du catalogue."""

    rows: list[dict[str, object]] = []
    for plan in PLANS:
        policy = SLA_POLICY.get(plan.code)
        if policy is None:
            # Un plan vendu sans objectif publié est une incohérence, pas une donnée
            # manquante : on refuse de publier une table partielle.
            raise KeyError(f"le plan {plan.code!r} est vendu sans objectif de réponse publié")
        rows.append(
            {
                "plan_code": plan.code,
                "plan_name": plan.name,
                "first_response_hours": int(policy["default"]),
                "incident_response_hours": int(policy["incident"]),
            }
        )
    return rows


#: Erreurs fréquentes. ``error_code`` et ``api_path`` sont **vérifiés** par un test
#: contre le code des routes : citer une erreur qui n'existe plus rendrait l'aide fausse.
FREQUENT_ERRORS: Final[tuple[dict[str, str], ...]] = (
    {
        "error_code": "quota_exceeded",
        "api_path": "app/billing/enforcement.py",
        "title": "Quota de documents atteint",
        "explanation": (
            "Le plan en cours limite le nombre de documents ou de pages analysées par "
            "mois. L'API refuse la nouvelle opération **avant** de la traiter : aucun "
            "dépassement n'est facturé en silence."
        ),
        "fix": (
            "Attendre le renouvellement du mois, ou changer d'offre. Le journal d'usage "
            "de votre espace donne la consommation exacte."
        ),
    },
    {
        "error_code": "subscription_inactive",
        "api_path": "app/billing/enforcement.py",
        "title": "Abonnement inactif",
        "explanation": (
            "L'accès aux fonctions payantes est suspendu : essai terminé, paiement en "
            "échec ou résiliation en fin de période."
        ),
        "fix": "Régulariser l'abonnement depuis l'espace client ; les données restent en place.",
    },
    {
        "error_code": "report_not_ready",
        "api_path": "app/api/v1/reports.py",
        "title": "Rapport en cours de rendu (409)",
        "explanation": (
            "Depuis C22, la génération d'un PDF ou d'un dossier se fait en arrière-plan : "
            "la requête répond immédiatement avec un travail et l'API renvoie 409 tant que "
            "le fichier n'est pas disponible. Ce n'est pas une erreur, c'est un état."
        ),
        "fix": (
            "Suivre le travail via le lien de file renvoyé par la requête, ou recharger "
            "l'analyse : le dernier rapport prêt est alors servi."
        ),
    },
    {
        "error_code": "report_object_missing",
        "api_path": "app/api/v1/reports.py",
        "title": "Fichier de rapport absent du stockage (409)",
        "explanation": (
            "La base référence un rapport dont l'objet a disparu du stockage. L'API refuse "
            "de servir un fichier vide qui aurait l'air d'un rapport."
        ),
        "fix": "Relancer la génération du rapport depuis l'analyse ; l'ancien reste tracé.",
    },
    {
        "error_code": "rights_close_refused",
        "api_path": "app/api/v1/data_rights.py",
        "title": "Clôture de demande de droits refusée (400)",
        "explanation": (
            "Une demande d'accès ne peut pas être close « accordée » sans la référence de "
            "ce qui a été remis à la personne (empreinte d'export, manifeste d'effacement)."
        ),
        "fix": "Joindre la référence produite par l'export ou l'effacement avant de clore.",
    },
    {
        "error_code": "erasure_refused",
        "api_path": "app/api/v1/privacy.py",
        "title": "Effacement refusé (409)",
        "explanation": (
            "Un dossier de gel légal protège le contenu : le refus cite le dossier "
            "concerné. Le gel empêche la suppression, il n'autorise rien d'autre."
        ),
        "fix": "Lever le gel si la procédure est close, puis relancer l'effacement.",
    },
    {
        "error_code": "storage_unavailable",
        "api_path": "app/api/v1/privacy.py",
        "title": "Stockage indisponible pendant un effacement (500)",
        "explanation": (
            "La suppression d'un objet a échoué : **rien n'a été effacé** (lignes "
            "conservées, aucune trace d'effacement écrite). L'API préfère échouer plutôt "
            "que d'annoncer un effacement incomplet."
        ),
        "fix": "Réessayer une fois le stockage joignable ; la demande de droits peut rester ouverte.",
    },
    {
        "error_code": "invalid_received_at",
        "api_path": "app/api/v1/data_rights.py",
        "title": "Date de réception illisible (422)",
        "explanation": (
            "Une demande de droits est datée par sa **réception réelle** (courrier, "
            "courriel), au format ISO 8601. Une date approximative ferait courir le délai "
            "du mauvais jour."
        ),
        "fix": "Saisir la date de réception, par exemple 2026-09-12T09:30:00+02:00.",
    },
)


def help_centre() -> dict[str, object]:
    """Le centre d'aide publié : parcours, erreurs, objectifs, exclusions."""

    # `api_path` n'est pas publié : il sert au test qui vérifie que l'erreur citée existe
    # toujours dans le fichier qui la produit. Exposer un chemin de fichier interne dans
    # une réponse publique n'apporte rien à l'utilisateur.
    errors = [
        {key: value for key, value in item.items() if key != "api_path"}
        for item in FREQUENT_ERRORS
    ]
    return {
        "start_here": [
            {
                "title": "1. Créer un espace et inviter l'équipe",
                "body": (
                    "L'inscription crée l'organisation et le compte propriétaire. Les "
                    "invitations se font depuis l'espace équipe ; chaque membre reçoit un "
                    "rôle (propriétaire, administrateur, analyste, lecteur) qui décide de "
                    "ce qu'il peut modifier."
                ),
            },
            {
                "title": "2. Déposer un premier document",
                "body": (
                    "Le téléversement passe par une URL présignée : le fichier va "
                    "directement au stockage, il n'est pas relu par l'API. Il est analysé "
                    "par un code antivirus avant d'être promu, puis le texte est extrait "
                    "(natif ou OCR, page par page)."
                ),
            },
            {
                "title": "3. Lancer une analyse et lire les verdicts",
                "body": (
                    "L'analyse produit des allégations détectées, confrontées à un Rule "
                    "Book versionné et aux preuves que vous avez enregistrées. Chaque "
                    "verdict publie sa règle, son niveau de confiance et ce qui a déclenché "
                    "la détection."
                ),
            },
            {
                "title": "4. Générer un rapport et le vérifier",
                "body": (
                    "Le rapport PDF est signé (HMAC-SHA-256) et porte l'empreinte du "
                    "fichier. N'importe qui peut vérifier sa validité sans compte, y "
                    "compris un client ou un contrôleur."
                ),
            },
        ],
        "frequent_errors": errors,
        "sla": {
            "rows": published_sla(),
            "statement": SLA_STATEMENT,
            "exclusions": list(SLA_EXCLUSIONS),
        },
        "honesty": (
            "Ce centre d'aide décrit le produit qui tourne : les erreurs citées sont "
            "vérifiées contre le code, et ce qui n'existe pas (astreinte, sauvegarde du "
            "stockage, disponibilité garantie) est écrit comme inexistant."
        ),
    }
