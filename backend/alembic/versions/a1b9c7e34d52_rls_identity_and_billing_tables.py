"""isolation des tables d'identité et de facturation oubliées par la RLS

Revision ID: a1b9c7e34d52
Revises: f7b3e21c8a90
Create Date: 2026-11-19 00:00:00.000000

Cinq tables portent ``organization_id`` sans aucune politique de sécurité au
niveau des lignes :

* ``identity_tokens`` — jetons de vérification d'adresse et de réinitialisation
  de mot de passe ;
* ``email_messages`` — boîte d'envoi interne ;
* ``billing_subscriptions``, ``billing_provider_events``,
  ``billing_checkout_sessions`` — état commercial et événements du prestataire.

Ce n'est pas une hypothèse : ``infra/neon/verify_runtime.py`` refuse une base où
une table portant ``organization_id`` n'a pas de politique, et la vérification
exécutée contre un PostgreSQL jetable listait exactement ces cinq noms. Le
vérificateur avait raison — la couche applicative filtre bien par organisation,
mais la base était la seule barrière qui tienne en cas de requête oubliée, de
jointure mal écrite ou d'accès direct avec le rôle applicatif.

Les trois tables dont ``organization_id`` est **nullable** gardent volontairement
une politique permissive sur les lignes sans organisation : un jeton émis avant
la création de l'organisation, ou un événement de prestataire reçu avant
rattachement, doit rester lisible par le service. Ce qui est interdit, c'est de
lire la ligne **d'une autre organisation** quand elle en a une :

    organization_id IS NULL OR organization_id = vericlaim_current_organization_id()

Une politique qui exigerait l'égalité stricte rendrait ces lignes invisibles au
service lui-même, et l'application échouerait à réclamer un jeton qu'elle vient
d'émettre — un correctif qui casse la connexion n'en est pas un.

``FORCE ROW LEVEL SECURITY`` est appliqué partout : sans lui, le propriétaire de
la table (le rôle de migration) contourne les politiques, et une preuve RLS
exécutée avec ce rôle ne prouverait rien.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b9c7e34d52"
down_revision: str | None = "f7b3e21c8a90"
branch_labels = None
depends_on = None

# (table, organization_id autorise NULL)
_TABLES: tuple[tuple[str, bool], ...] = (
    ("identity_tokens", True),
    ("email_messages", True),
    ("billing_subscriptions", False),
    ("billing_provider_events", True),
    ("billing_checkout_sessions", False),
)


def _is_postgresql() -> bool:
    """La RLS est une fonctionnalité PostgreSQL ; SQLite ne doit pas échouer ici."""

    return op.get_bind().dialect.name == "postgresql"


def _policy_body(column_nullable: bool) -> str:
    if column_nullable:
        # Ligne sans organisation = appartenance inconnue : visible du service,
        # jamais attribuable à un client.
        return (
            "USING (organization_id IS NULL "
            "OR organization_id = vericlaim_current_organization_id()) "
            "WITH CHECK (organization_id IS NULL "
            "OR organization_id = vericlaim_current_organization_id())"
        )
    return (
        "USING (organization_id = vericlaim_current_organization_id()) "
        "WITH CHECK (organization_id = vericlaim_current_organization_id())"
    )


def upgrade() -> None:
    if not _is_postgresql():
        return
    for table_name, nullable in _TABLES:
        policy = f"p_{table_name}_tenant"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{policy}" ON "{table_name}" {_policy_body(nullable)}'
        )


def downgrade() -> None:
    if not _is_postgresql():
        return
    for table_name, _nullable in _TABLES:
        policy = f"p_{table_name}_tenant"
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
