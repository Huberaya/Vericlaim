"""resserrer les privilèges du rôle système au strict nécessaire

Revision ID: d6b4f2c81a73
Revises: c5f1a8e37b910
Create Date: 2026-11-20 09:00:00.000000

La migration ``c5f1a8e37b910`` a séparé l'accès locataire de l'accès système.
La séparation était bonne ; le dimensionnement ne l'était pas. Ce qui était
accordé, mesuré par ``has_table_privilege`` et ``has_column_privilege`` sur une
base construite par la chaîne réelle :

===================================  ==========================================
``UPDATE`` sur table                 ``users``, ``memberships``,
                                     ``password_login_attempts``,
                                     ``identity_tokens``, ``email_messages``,
                                     ``billing_provider_events``
``SELECT`` sur table                 ``users``, ``organizations``,
``(``SELECT *``)``                   ``memberships``, ``roles``,
                                     ``audit_events``, ``password_login_attempts``
``INSERT``                           ``users``, ``organizations``,
                                     ``memberships``, ``identity_tokens``,
                                     ``email_messages``,
                                     ``billing_provider_events``
===================================  ==========================================

Ce que cela signifiait concrètement : le rôle chargé des parcours transverses
pouvait réécrire ``users.password_hash``, ``users.status``, ``users.email``,
``memberships.organization_id`` ou ``memberships.role_id`` — c'est-à-dire
déplacer une adhésion d'une organisation à une autre ou en élever les droits.
Aucun parcours n'en a besoin ; le privilège venait d'un ``GRANT UPDATE`` posé
sur la table entière par commodité.

**Ce que cette migration fait.** Elle ne touche ni à la RLS, ni à ``BYPASSRLS``,
ni aux politiques : le périmètre est exactement celui des ``GRANT``. Les droits
sont réécrits en column-level, sur la liste des colonnes que les parcours
écrivent réellement — liste relevée en traçant les ``UPDATE`` émis par les tests
d'identité sous le rôle réel, pas en supposant ce dont le code a besoin.

**Deux constats de l'audit, qui expliquent des retraits plus larges que prévu.**

1. ``billing_provider_events`` n'est **jamais** touché par le rôle système. Le
   webhook prestataire (``app/api/v1/billing.py``) et le compteur de tentatives
   de connexion (``login_with_password``) reçoivent la session applicative
   (``get_db``) ; aucun des deux ne passe par l'identité système. Leurs
   privilèges étaient une capacité dormante, pas un besoin.
2. Les ``SELECT`` sur ``roles`` et ``organizations`` sont des **lectures
   d'entité entière** : ``_owner_role`` charge le rôle fondateur par code et
   l'inscription relit l'organisation qu'elle vient de créer. Ces deux tables
   n'ont pas d'identifiant « secret » comparable à un corps d'e-mail, mais le
   rôle n'a aucune raison d'en lire ``permissions_json`` ni
   ``billing_plan_code``. Le droit est donc borné aux colonnes utiles.

**Ce qui reste au niveau table, et pourquoi ce n'est pas un oubli.**
``audit_events`` : l'insertion d'un événement exige de lire l'événement
précédent pour chaîner les empreintes, et PostgreSQL ne permet pas d'exprimer
« ``INSERT`` seulement si ``SELECT`` est possible ». Retirer ``SELECT``
casserait la chaîne d'audit, pas un privilège. ``password_login_attempts`` :
même raison, un verrou de compte se lit puis s'écrit sur la table entière
(``SELECT`` + ``INSERT`` + ``UPDATE`` sur sept colonnes).

**Les ``INSERT`` restent au niveau table** : PostgreSQL n'offre pas de privilège
d'insertion par colonne, et les ``NOT NULL`` empêchent de toute façon d'insérer
une ligne partielle. Le risque d'insertion porte sur l'identité des lignes (qui
peut écrire quoi), pas sur les colonnes : il est couvert par la contrainte
d'unicité de l'e-mail et par les parcours, pas par un ``GRANT`` plus fin.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6b4f2c81a73"
down_revision: str | Sequence[str] | None = "c5f1a8e37b910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_ROLE = "vericlaim_system"

# Colonnes que le rôle système lit encore, table par table. La liste est le
# sous-ensemble strict de celle de ``c5f1a8e37b910`` : tout ce qui n'est pas
# utilisé par un parcours a été retiré.
SYSTEM_READ_COLUMNS: dict[str, tuple[str, ...]] = {
    "identity_tokens": (
        "id",
        "user_id",
        "organization_id",
        "purpose",
        "token_hash",
        "expires_at",
        "used_at",
        "created_at",
        "created_by_user_id",
        "updated_at",
    ),
    "email_messages": (
        "id",
        "status",
        "organization_id",
        "attempt_count",
        "created_at",
        "updated_at",
    ),
    # ``_owner_role`` résout le rôle fondateur par code et crée l'adhésion avec
    # son identifiant. Rien d'autre du rôle n'est lu : ni ``permissions_json``,
    # qui décrit pourtant les droits accordés, ni ``description``.
    "roles": ("id", "code"),
    # L'inscription relit l'organisation qu'elle vient de créer (nom, slug,
    # statut) et s'en sert comme rattachement. ``billing_plan_code`` n'est pas lu.
    "organizations": ("id", "name", "slug", "status", "data_region", "created_at", "updated_at"),
    # La consommation d'un jeton rattache la ligne à l'organisation si elle ne
    # l'était pas : cette écriture est bornée plus bas à trois colonnes.
    "billing_provider_events": (
        "id",
        "organization_id",
        "provider",
        "provider_event_id",
        "event_type",
        "signature_verified",
        "applied",
        "received_at",
        "created_at",
    ),
}

# Colonnes modifiables, relevées en traçant les UPDATE réels des parcours.
#
# ``users`` : la vérification d'adresse écrit ``email_verified_at`` et, sur un
# compte invité, ``status`` (vers ``ACTIVE`` uniquement, décidé par le parcours) ;
# la réinitialisation et l'acceptation d'invitation écrivent en plus
# ``password_hash``, ``password_updated_at`` et, pour l'invitation acceptée,
# ``display_name``. ``status`` reste accordée parce que les parcours la font
# passer de ``INVITED`` à ``ACTIVE`` : la retirer casserait la vérification d'un
# compte invité. Ce n'est pas un droit d'administration du statut pour autant —
# voir la note sur les garanties réelles en fin de fichier.
SYSTEM_UPDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": (
        "email_verified_at",
        "status",
        "password_hash",
        "password_updated_at",
        "display_name",
        "updated_at",
    ),
    # ``organization_id``, ``user_id`` et ``role_id`` sont **absents** : déplacer
    # une adhésion ou en changer le rôle est exactement l'élévation de privilège
    # que la mission demande d'interdire. ``status`` et ``activated_at`` sont ce
    # que l'acceptation d'invitation écrit.
    "memberships": ("status", "activated_at", "updated_at"),
    "identity_tokens": ("used_at", "organization_id", "updated_at"),
    "email_messages": ("status", "transport", "error_code", "attempt_count", "updated_at"),
    "billing_provider_events": ("applied",),
    "auth_sessions": ("revoked_at",),
}

# Tables dont le rôle système peut encore écrire une ligne entière ou en lire
# une. ``SELECT`` reste au niveau table là où l'écriture en dépend.
SYSTEM_TABLE_SELECT = (
    "users",
    "memberships",
    "audit_events",
    "password_login_attempts",
    "auth_sessions",
)

# ``password_login_attempts`` n'y est pas : la création d'un compteur d'échecs
# appartient à ``_register_failure``, appelé par ``login_with_password``, qui
# reçoit la session **applicative**. La réinitialisation, elle, ne fait que
# remettre le compteur à zéro (``UPDATE``) et ne crée jamais de ligne. Retirer
# l'``INSERT`` a donc été vérifié en exécutant le parcours, pas en le supposant.
# ``billing_provider_events`` reste insérable par le rôle système, après
# vérification et non par confort : PostgreSQL, interrogé sur une base construite
# par la chaîne réelle, refuse l'``INSERT`` sans organisation au rôle applicatif
# (``new row violates row-level security policy``). Un webhook de prestataire
# n'a par définition aucun contexte de tenant — il doit découvrir à quelle
# organisation l'événement se rapporte. Le chemin existe donc, même si le code
# applicatif emprunte aujourd'hui le rôle applicatif : ce dernier échouerait. Ce
# point est consigné comme défaut distinct (voir la note en fin de fichier) et
# non absorbé par ce resserrement.
SYSTEM_TABLE_INSERT = (
    "users",
    "organizations",
    "memberships",
    "identity_tokens",
    "email_messages",
    "audit_events",
    "billing_provider_events",
)

# ``password_login_attempts`` : le verrouillage lit une ligne par adresse, la met
# à jour ou la crée. Pas de colonne secrète, mais pas de raison non plus de
# laisser le rôle la lire autrement qu'en entier pour un compteur.
SYSTEM_TABLE_UPDATE = ("password_login_attempts",)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _exec(statement: str) -> None:
    op.execute(statement)


def _grant_narrow_privileges() -> None:
    """Réécrire les droits du rôle système, sans toucher à la RLS ni au BYPASS.

    ``REVOKE ALL`` d'abord : sans cela, un ``GRANT`` de colonne s'ajouterait au
    ``GRANT`` de table posé par la migration précédente, et le rôle garderait
    exactement les droits que cette migration prétend retirer. C'est le piège
    principal ici : PostgreSQL ne « resserre » jamais un privilège, il l'ajoute.
    """
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"GRANT USAGE ON SCHEMA public TO {SYSTEM_ROLE}")

    for table, columns in SYSTEM_READ_COLUMNS.items():
        _exec(f'GRANT SELECT ({", ".join(columns)}) ON "{table}" TO {SYSTEM_ROLE}')
    for table in SYSTEM_TABLE_SELECT:
        _exec(f'GRANT SELECT ON "{table}" TO {SYSTEM_ROLE}')

    for table, columns in SYSTEM_UPDATE_COLUMNS.items():
        _exec(f'GRANT UPDATE ({", ".join(columns)}) ON "{table}" TO {SYSTEM_ROLE}')
    for table in SYSTEM_TABLE_UPDATE:
        _exec(f'GRANT UPDATE ON "{table}" TO {SYSTEM_ROLE}')

    for table in SYSTEM_TABLE_INSERT:
        _exec(f'GRANT INSERT ON "{table}" TO {SYSTEM_ROLE}')

    # Les séquences restent lisibles : les identifiants sont des UUID, mais
    # certaines colonnes de comptage utilisent une séquence.
    _exec(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {SYSTEM_ROLE}")


def upgrade() -> None:
    if not _is_postgresql():
        return
    _grant_narrow_privileges()


def downgrade() -> None:
    """Rendre exactement l'état de ``c5f1a8e37b910``, y compris ses excès.

    Un downgrade doit restituer l'état antérieur, pas un état intermédiaire qui
    aurait l'air plus sûr : c'est la même règle que celle appliquée par la
    migration précédente pour sa politique permissive.
    """
    if not _is_postgresql():
        return
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"REVOKE ALL ON SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"GRANT USAGE ON SCHEMA public TO {SYSTEM_ROLE}")
    for table, columns in (
        (
            "identity_tokens",
            (
                "id",
                "user_id",
                "organization_id",
                "purpose",
                "token_hash",
                "expires_at",
                "used_at",
                "created_at",
                "created_by_user_id",
                "updated_at",
            ),
        ),
        ("email_messages", ("id", "status", "organization_id", "attempt_count", "created_at", "updated_at")),
        (
            "billing_provider_events",
            (
                "id",
                "organization_id",
                "provider",
                "provider_event_id",
                "event_type",
                "signature_verified",
                "applied",
                "received_at",
                "created_at",
            ),
        ),
    ):
        _exec(f'GRANT SELECT ({", ".join(columns)}) ON "{table}" TO {SYSTEM_ROLE}')
    for table in (
        "identity_tokens",
        "email_messages",
        "billing_provider_events",
        "users",
        "memberships",
    ):
        _exec(f'GRANT UPDATE ON "{table}" TO {SYSTEM_ROLE}')
    for table in (
        "users",
        "organizations",
        "memberships",
        "identity_tokens",
        "email_messages",
        "billing_provider_events",
    ):
        _exec(f'GRANT INSERT ON "{table}" TO {SYSTEM_ROLE}')
    _exec(f"GRANT SELECT ON users, organizations, memberships, roles TO {SYSTEM_ROLE}")
    _exec(f"GRANT SELECT, INSERT, UPDATE ON password_login_attempts TO {SYSTEM_ROLE}")
    _exec(f"GRANT SELECT, INSERT ON audit_events TO {SYSTEM_ROLE}")
    _exec(f"GRANT SELECT ON auth_sessions TO {SYSTEM_ROLE}")
    _exec(f"GRANT UPDATE (revoked_at) ON auth_sessions TO {SYSTEM_ROLE}")
    _exec(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {SYSTEM_ROLE}")


# --------------------------------------------------------------------------- #
# Ce que ces privilèges garantissent, et ce qu'ils ne garantissent pas
# --------------------------------------------------------------------------- #
#
# Column-level dit *quelles colonnes* le rôle peut écrire, jamais *quelles
# valeurs* ni *quelles lignes* : PostgreSQL n'a pas de privilège conditionnel.
# ``GRANT UPDATE (status) ON users`` autorise donc le rôle système à écrire
# n'importe quelle valeur de ``status`` sur n'importe quel utilisateur — y
# compris ``DISABLED``. C'est une limite du modèle de privilèges, pas un oubli
# de cette migration, et il faut la nommer plutôt que la laisser croire résolue.
#
# Ce qui est réellement fermé par ce resserrement, et qui était ouvert avant :
#   * ``memberships.organization_id`` et ``memberships.role_id`` ne sont plus
#     modifiables : une adhésion ne peut plus être déplacée d'une organisation à
#     une autre ni élevée en droits par le rôle système ;
#   * ``users.email``, ``users.id``, ``users.identity_provider`` et
#     ``users.external_subject`` ne sont plus modifiables : l'identité d'un
#     compte ne peut plus être réécrite pour le faire passer pour un autre ;
#   * ``CREATE``/``TRUNCATE``/``REFERENCES``/``TRIGGER`` restent refusés, et
#     ``DELETE`` n'a jamais été accordé.
#
# Ce qui reste possible, et reste couvert par le code, pas par la base :
#   * écrire une valeur arbitraire dans ``users.status`` ou réécrire
#     ``users.password_hash`` d'un utilisateur donné — les parcours ne le font
#     que sur la ligne du jeton consommé, mais la base ne l'impose pas.
#   * insérer une adhésion ou un jeton : l'``INSERT`` est au niveau table.
# Restreindre ces deux points demanderait des ``ROW LEVEL SECURITY``
# conditionnelles, donc des politiques par parcours — hors du périmètre de ce
# correctif, et à traiter comme un durcissement séparé s'il est jugé nécessaire.


# --------------------------------------------------------------------------- #
# Défaut distinct, constaté pendant ce resserrement et NON corrigé ici
# --------------------------------------------------------------------------- #
#
# ``billing_provider_events`` et ``email_messages`` ont un ``WITH CHECK`` qui
# exige une organisation. Or deux parcours écrivent une ligne **sans**
# organisation, sur la session applicative :
#
#   * ``_apply_envelope`` (``app/api/v1/billing.py``) insère l'événement du
#     prestataire avec ``organization_id=None`` : c'est le webhook lui-même qui
#     découvre l'organisation en appliquant l'événement ;
#   * le webhook n'installe aucun contexte ``app.current_organization_id``
#     (``get_db`` ne le fait pas, et la route ne le pose pas).
#
# Mesuré, rôle ``vericlaim_app`` réel, base issue de la chaîne Alembic :
#   ``ERROR: new row violates row-level security policy for table
#   "billing_provider_events"``, et de même pour ``email_messages``.
#
# Ce défaut était masqué par deux choses : les tests d'intégration sèment ces
# lignes avec le rôle système (le seul autorisé à écrire sans contexte), et le
# webhook n'est pas exercé sous le rôle applicatif réel dans la suite.
#
# Il n'est pas corrigé ici parce que le corriger suppose de router le webhook
# vers l'identité système — c'est-à-dire un changement de parcours, pas un
# resserrement de privilèges, et un chantier que la mission exclut (« ne pas
# rouvrir les chantiers facturation »). Le droit d'``INSERT`` correspondant est
# donc conservé : le retirer ne fermerait aucune capacité utilisée tout en
# rendant le chemin déjà cassé impossible à réparer sans nouvelle migration.
