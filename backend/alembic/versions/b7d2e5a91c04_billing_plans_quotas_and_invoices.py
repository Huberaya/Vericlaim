"""plans, quotas et facturation : abonnements, journal d'usage, factures

Revision ID: b7d2e5a91c04
Revises: f63c9a1b7d20
Create Date: 2026-09-30 18:10:00.000000

Le produit vendait des offres affichées sur sa page publique et n'appliquait
aucune limite : ``organizations.billing_plan_code`` existait et n'était lu par
aucun code. Cette migration ajoute ce que l'application des quotas exige.

* ``billing_subscriptions`` — un abonnement par organisation, écrit uniquement
  par l'application d'un événement prestataire. Une organisation sans ligne ici
  est en essai dérivé de sa date de création : **aucune ligne n'est créée par une
  lecture**, un essai ne peut donc pas être relancé par un appel d'API.
* ``billing_usage_events`` — le journal d'usage, append-only, avec une clé
  d'idempotence par organisation. Les compteurs affichés sont la somme de ce
  journal ; il n'existe volontairement aucune colonne « compteur » à incrémenter.
  La période de comptage est identifiée par ``period_key`` (ISO-8601 UTC du début
  de période) et non par une date : deux périodes payées peuvent commencer le même
  jour, et une date les confondrait.
* ``billing_provider_events`` — les événements reçus des prestataires, avec leur
  identifiant unique : un rejeu de webhook n'applique pas deux fois la transition.
* ``billing_invoices`` et ``billing_checkout_sessions`` — le miroir local des
  objets du prestataire, pour que le portail client n'ait pas besoin de
  l'interroger pour afficher une facture.

RLS : ``billing_usage_events`` et ``billing_invoices`` sont des tables de
locataire et reçoivent la politique standard. ``billing_subscriptions`` et
``billing_provider_events`` sont, comme les tables d'identité, consultées par un
appel non authentifié (webhook) **avant** qu'une organisation ne soit résolue :
elles n'ont pas de politique par locataire, et le code ne les lit que par
identifiant prestataire.

Aucune donnée n'est reprise : un abonnement ne peut pas être inventé pour une
organisation existante. Les organisations déjà présentes continuent sur l'essai
dérivé de leur date de création, ce qui est l'état réel — pas un état fabriqué.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e5a91c04"
down_revision: str | Sequence[str] | None = "f63c9a1b7d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ("billing_usage_events", "billing_invoices")


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _enable_rls() -> None:
    for table_name in RLS_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "p_{table_name}_tenant" ON "{table_name}"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def _disable_rls() -> None:
    for table_name in RLS_TABLES:
        op.execute(f'DROP POLICY IF EXISTS "p_{table_name}_tenant" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "billing_subscriptions",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("plan_code", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("trialing", "active", "past_due", "canceled", name="billing_subscription_status"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_customer_id", sa.String(length=255), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("provider_price_id", sa.String(length=255), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("past_due_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_plan_code", sa.String(length=64), nullable=True),
        sa.Column("pending_plan_effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_provider_event_id", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "current_period_end > current_period_start", name="ck_billing_subscriptions_period_ordered"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_billing_subscriptions_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_subscriptions"),
        sa.UniqueConstraint(
            "organization_id", name="uq_billing_subscriptions_organization_id"
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_subscription_id",
            name="uq_billing_subscriptions_provider_subscription_id",
        ),
    )

    op.create_table(
        "billing_usage_events",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_billing_usage_events_quantity_positive"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_billing_usage_events_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name="fk_billing_usage_events_recorded_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_usage_events"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_billing_usage_events_organization_idempotency",
        ),
    )
    op.create_index(
        "ix_billing_usage_events_period",
        "billing_usage_events",
        ["organization_id", "metric", "period_key"],
    )
    op.create_index(
        "ix_billing_usage_events_organization_id", "billing_usage_events", ["organization_id"]
    )

    op.create_table(
        "billing_provider_events",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("apply_result", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_billing_provider_events_organization_id_organizations",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_provider_events"),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_billing_provider_events_provider_event_id"
        ),
    )
    op.create_index(
        "ix_billing_provider_events_organization_id", "billing_provider_events", ["organization_id"]
    )

    op.create_table(
        "billing_invoices",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_invoice_id", sa.String(length=128), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("plan_code", sa.String(length=64), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hosted_url", sa.String(length=512), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents >= 0", name="ck_billing_invoices_amount_non_negative"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_billing_invoices_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_invoices"),
        sa.UniqueConstraint(
            "provider", "provider_invoice_id", name="uq_billing_invoices_provider_invoice_id"
        ),
    )
    op.create_index("ix_billing_invoices_organization_id", "billing_invoices", ["organization_id"])

    op.create_table(
        "billing_checkout_sessions",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255), nullable=False),
        sa.Column("plan_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_billing_checkout_sessions_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_billing_checkout_sessions_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_checkout_sessions"),
        sa.UniqueConstraint(
            "provider", "provider_session_id", name="uq_billing_checkout_sessions_provider_session_id"
        ),
    )
    op.create_index(
        "ix_billing_checkout_sessions_organization_id", "billing_checkout_sessions", ["organization_id"]
    )

    if _is_postgresql():
        _enable_rls()


def downgrade() -> None:
    if _is_postgresql():
        _disable_rls()
    op.drop_index("ix_billing_checkout_sessions_organization_id", table_name="billing_checkout_sessions")
    op.drop_table("billing_checkout_sessions")
    op.drop_index("ix_billing_invoices_organization_id", table_name="billing_invoices")
    op.drop_table("billing_invoices")
    op.drop_index("ix_billing_provider_events_organization_id", table_name="billing_provider_events")
    op.drop_table("billing_provider_events")
    op.drop_index("ix_billing_usage_events_organization_id", table_name="billing_usage_events")
    op.drop_index("ix_billing_usage_events_period", table_name="billing_usage_events")
    op.drop_table("billing_usage_events")
    op.drop_table("billing_subscriptions")
