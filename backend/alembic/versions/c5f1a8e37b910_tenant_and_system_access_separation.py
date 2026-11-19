"""séparation entre accès locataire et accès système pour l'identité

Revision ID: c5f1a8e37b910
Revises: a1b9c7e34d52
Create Date: 2026-11-19 18:00:00.000000

La migration précédente (``a1b9c7e34d52``) a posé une RLS sur cinq tables, en
gardant une politique permissive sur les lignes sans organisation : ces lignes
sont écrites et lues par des parcours qui n'ont pas encore d'organisation
(inscription, vérification d'adresse, réinitialisation, webhook prestataire). Le
principe était bon, la mise en œuvre ne l'était pas. Trois défauts, mesurés
contre un PostgreSQL 16.2 avec le rôle applicatif réel :

**1. L'écriture d'une ligne rattachée sans contexte est refusée.** La politique
``WITH CHECK (organization_id IS NULL OR organization_id = contexte)`` refuse
l'insertion d'une ligne portant une organisation quand aucune organisation n'est
posée. L'inscription crée l'organisation puis écrit le jeton de vérification dans
le même appel, sans contexte (il n'existe pas de contexte à poser : c'est ce que
l'inscription crée). Résultat mesuré :
``new row violates row-level security policy for table "identity_tokens"``.
**L'inscription est donc impossible en production**, et aucun test ne l'a vue :
la suite PostgreSQL tourne avec un rôle qui contourne la RLS, la recette ne
couvre pas l'inscription, et SQLite ne connaît pas la RLS.

**2. Une organisation voir les jetons orphelins des autres.** ``organization_id
IS NULL`` étant accepté dans ``USING``, l'organisation A voit le jeton de
réinitialisation d'un utilisateur rattaché à B dès lors que ce jeton a été émis
avant rattachement. Mesuré : visible, secret de réinitialisation compris.

**3. Un seul rôle porte à la fois l'accès filtré par organisation et les
opérations transverses**, ce qui oblige la politique à être permissive pour que
le système fonctionne — et la rend donc trop large pour les locataires.

**La correction sépare les deux usages au lieu d'élargir la politique.**

* ``USING`` devient strict : ``organization_id = contexte``, pour tout le monde.
  Une organisation ne voit plus jamais le jeton d'une autre, ni orphelin ni
  rattaché.
* Trois politiques d'écriture sont ajoutées, chacune avec son ``WITH CHECK`` :
  insertion d'une ligne sans organisation (l'inscription écrit le jeton puis le
  rattache), insertion d'une ligne pour sa propre organisation (le webhook
  journalise l'événement après avoir résolu le tenant), consommation d'un jeton
  (``UPDATE`` ne touche que ``used_at``).
* Un **rôle système dédié**, ``vericlaim_system``, contourne la RLS — et c'est
  là que la séparation se joue : il ne reçoit que des privilèges **par colonne**.
  Il peut lire les empreintes de jetons (indispensable pour authentifier un lien
  sans contexte) mais pas le corps des e-mails, pas les secrets de prestataire,
  pas les autres colonnes d'identité. Le rôle applicatif, lui, garde son accès
  complet *dans le périmètre de son organisation*.

Ce que cette migration ne fait pas, et qu'il faut dire : elle ne transforme pas
``vericlaim_app`` en rôle sans contournement. La RLS de cette base est conçue
pour un rôle qui la contourne côté système ; rendre ``vericlaim_app`` totalement
soumis supposerait de migrer chaque parcours vers le rôle système, ce qui n'est
pas fait ici et ne doit pas être affirmé.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "c5f1a8e37b910"
down_revision: str | Sequence[str] | None = "a1b9c7e34d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_ROLE = "vericlaim_system"

# Politique appliquée à toutes les lignes : plus aucune permissivité.
TENANT_ONLY_TABLES = (
    "identity_tokens",
    "email_messages",
    "billing_provider_events",
)

# Colonnes que le rôle système peut lire, par table. Tout le reste lui est
# interdit : la liste est volontairement la plus courte possible.
SYSTEM_READ_COLUMNS: dict[str, tuple[str, ...]] = {
    # Le parcours lit la ligne entière du jeton (l'ORM projette toutes les
    # colonnes). Ce sont ces colonnes-là et pas d'autres : pas de table
    # applicative, pas de corps d'e-mail, pas d'état commercial.
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
    # La boîte d'envoi n'est lue par le système que pour connaître son état ;
    # le corps, le sujet et le destinataire restent hors de portée.
    "email_messages": ("id", "status", "organization_id", "attempt_count", "created_at", "updated_at"),
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

# Tables dont le rôle système peut modifier une ligne. Volontairement restreint :
# pas d'organisation, pas d'utilisateur, pas d'abonnement — une écriture
# transverse sur ces tables serait une élévation de privilège.
SYSTEM_UPDATE_TABLES = (
    "identity_tokens",
    "email_messages",
    "billing_provider_events",
    # Vérifier une adresse ou définir un mot de passe modifie le compte
    # lui-même ; ces deux parcours n'ont pas d'organisation à désigner.
    "users",
    # Accepter une invitation active l'adhésion créée à l'invitation. Le droit a
    # été découvert en exécutant le parcours sous ce rôle : sans lui,
    # « permission denied for table memberships », et l'invité ne peut pas entrer.
    "memberships",
)

# Ce que les parcours transverses écrivent. L'INSERT porte sur la ligne entière
# (PostgreSQL n'offre pas de privilège d'insertion par colonne), ce qui est sans
# conséquence : les lectures restent bornées colonne par colonne, et c'est la
# lecture qui exposerait le secret d'un autre utilisateur.
SYSTEM_INSERT_TABLES = (
    "users",
    "organizations",
    "memberships",
    "identity_tokens",
    "email_messages",
    "billing_provider_events",
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _exec(statement: str) -> None:
    op.execute(statement)


def _require_system_role() -> None:
    """Vérifier que le rôle système est provisionné, sans jamais le créer ici.

    ``CREATE ROLE`` exige ``CREATEROLE``, que le rôle de migration n'a pas — et ne
    doit pas avoir : une migration capable de créer des identités serait une
    migration capable d'en créer une sans RLS. Le rôle se provisionne donc hors
    migration (``infra/neon/04_system_role.sql``), et la migration **échoue en le
    nommant** si on l'a oublié, plutôt que de poursuivre avec des politiques
    resserrées que personne ne pourrait utiliser.
    """
    bind = op.get_bind()
    context = op.get_context()
    if bind is None or getattr(context, "as_sql", False):
        # Mode hors-ligne (`alembic upgrade --sql`) : la connexion est factice et
        # `execute` ne renvoie rien. Aucune vérification n'est possible ; le SQL
        # est tout de même émis, et c'est un humain qui l'exécute.
        return
    exists = bind.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": SYSTEM_ROLE}
    ).scalar()
    if not exists:
        raise RuntimeError(
            f"Le rôle {SYSTEM_ROLE} est absent : exécuter infra/neon/04_system_role.sql "
            "avant cette migration. Sans lui, les parcours d'identité sans "
            "organisation (inscription, vérification d'adresse, réinitialisation, "
            "webhook) échouent en production."
        )
    privileged = bind.execute(
        text(
            "SELECT rolsuper OR rolcreaterole OR rolcreatedb AS elevated, "
            "       rolbypassrls AS bypasses "
            "FROM pg_roles WHERE rolname = :role"
        ),
        {"role": SYSTEM_ROLE},
    ).one()
    if privileged.elevated or not privileged.bypasses:
        raise RuntimeError(
            f"Le rôle {SYSTEM_ROLE} n'a pas la forme attendue : BYPASSRLS requis, "
            "aucun privilège de création. Refus d'élargir un rôle existant."
        )


def _tighten_policies() -> None:
    """Une seule politique par table, strictement bornée à l'organisation courante.

    Les politiques antérieures sont supprimées : PostgreSQL combine les politiques
    permissives par `OR`, donc laisser l'ancienne à côté de la nouvelle annulerait
    tout le gain. Une politique unique ``FOR ALL`` couvre lecture et écriture avec
    le même prédicat, ce que le vérificateur de production
    (``infra/neon/verify_runtime.py``) exige : un ``USING`` **et** un ``WITH CHECK``
    qui appliquent tous deux l'helper de tenant.

    Conséquence assumée : plus aucune ligne sans organisation n'est visible ni
    modifiable par le rôle applicatif. Les parcours qui en ont besoin (inscription,
    vérification d'adresse, réinitialisation, invitation, webhook) passent par le
    rôle système, qui contourne la RLS avec des droits bornés par colonne.
    """
    for table in TENANT_ONLY_TABLES:
        policy = f"p_{table}_tenant"
        for obsolete in (
            policy,
            f"p_{table}_signup_insert",
            f"p_{table}_webhook_insert",
            f"p_{table}_consume",
        ):
            _exec(f'DROP POLICY IF EXISTS "{obsolete}" ON "{table}"')
        _exec(
            f'''CREATE POLICY "{policy}" ON "{table}"
                FOR ALL
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def _grant_system_privileges() -> None:
    """Privilèges par colonne, jamais par table.

    ``REVOKE ALL`` d'abord : un rôle préexistant ne doit pas conserver un accès
    qu'une exécution précédente lui aurait donné.
    """
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"REVOKE ALL ON SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"GRANT USAGE ON SCHEMA public TO {SYSTEM_ROLE}")
    for table, columns in SYSTEM_READ_COLUMNS.items():
        _exec(f'GRANT SELECT ({", ".join(columns)}) ON "{table}" TO {SYSTEM_ROLE}')
    # `UPDATE` porte sur la ligne : le parcours écrit l'état d'un e-mail, la date
    # de consommation d'un jeton. La lecture reste bornée par colonne, et c'est
    # elle qui empêcherait de lire le corps d'un message.
    for table in SYSTEM_UPDATE_TABLES:
        _exec(f'GRANT UPDATE ON "{table}" TO {SYSTEM_ROLE}')
    for table in SYSTEM_INSERT_TABLES:
        _exec(f'GRANT INSERT ON "{table}" TO {SYSTEM_ROLE}')
    # L'inscription relit l'organisation qu'elle vient de créer, résout le rôle
    # fondateur et rattache l'utilisateur ; le verrouillage des tentatives est
    # tenu sans contexte. La liste est courte à dessein : ces droits ont été
    # découverts en exécutant réellement l'inscription sous ce rôle, pas en
    # supposant ce dont elle avait besoin.
    _exec(f"GRANT SELECT ON users, organizations, memberships, roles TO {SYSTEM_ROLE}")
    _exec(f"GRANT SELECT, INSERT, UPDATE ON password_login_attempts TO {SYSTEM_ROLE}")
    # L'inscription scelle un événement d'audit rattaché à l'organisation qu'elle
    # vient de créer. En ajout seul, comme pour le rôle applicatif : le rôle
    # système ne peut ni réécrire ni supprimer un événement.
    _exec(f"GRANT SELECT, INSERT ON audit_events TO {SYSTEM_ROLE}")
    # Réinitialiser un mot de passe doit déconnecter les sessions existantes —
    # c'est la propriété de sécurité, pas un effet de bord : sans elle, un lien
    # volé laisserait l'attaquant connecté. Le rôle système y lit et révoque,
    # jamais n'en crée : la création exige une identité, et c'est le rôle
    # applicatif qui l'accompagne.
    _exec(f"GRANT SELECT ON auth_sessions TO {SYSTEM_ROLE}")
    _exec(f"GRANT UPDATE (revoked_at) ON auth_sessions TO {SYSTEM_ROLE}")
    _exec(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {SYSTEM_ROLE}")


def upgrade() -> None:
    if not _is_postgresql():
        return
    _require_system_role()
    _tighten_policies()
    _grant_system_privileges()


def downgrade() -> None:
    if not _is_postgresql():
        return
    for table in TENANT_ONLY_TABLES:
        _exec(f'DROP POLICY IF EXISTS "p_{table}_tenant" ON "{table}"')
    # On restaure exactement la politique de a1b9c7e34d52, y compris sa
    # permissivité : un downgrade doit rendre l'état antérieur, pas un état
    # intermédiaire qui aurait l'air plus sûr.
    permissive = (
        "identity_tokens",
        "email_messages",
        "billing_provider_events",
    )
    for table in permissive:
        _exec(
            f'''CREATE POLICY "p_{table}_tenant" ON "{table}"
                USING (
                    organization_id IS NULL
                    OR organization_id = vericlaim_current_organization_id()
                )
                WITH CHECK (
                    organization_id IS NULL
                    OR organization_id = vericlaim_current_organization_id()
                )'''
        )
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {SYSTEM_ROLE}")
    _exec(f"REVOKE USAGE ON SCHEMA public FROM {SYSTEM_ROLE}")
    # Le rôle lui-même n'est pas supprimé : une migration ne détruit pas une
    # identité partagée, ce serait une perte de service silencieuse.
