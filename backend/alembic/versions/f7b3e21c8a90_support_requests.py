"""demandes de support : reçues, datées, avec l'objectif de réponse du plan

Revision ID: f7b3e21c8a90
Revises: c4d8b1e60f27
Create Date: 2026-09-30 22:40:00.000000

C15 outille le support comme C12 outille les droits : une demande qui n'est pas
**datée** ne peut pas être suivie, et une demande dont on ne sait pas **ce qui a été
répondu** n'a pas été traitée.

``support_requests`` porte donc, en plus du message : l'écran d'où la demande a été
envoyée et le dernier code d'erreur affiché (**le contexte réel**, pas une description
approximative), le plan de l'organisation et l'objectif de réponse applicable — figés
sur la ligne, pour qu'un changement d'offre ne réécrive pas l'engagement pris le jour du
dépôt — et la réponse écrite, obligatoire pour clore.

Aucune donnée n'est reprise d'un quelconque système précédent : les demandes reçues par
courriel avant cette migration ne sont pas inventées ici.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b3e21c8a90"
down_revision: str | Sequence[str] | None = "c4d8b1e60f27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ("support_requests",)


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


def upgrade() -> None:
    op.create_table(
        "support_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("screen", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("requester_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("requester_email", sa.String(length=320), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("first_response_hours", sa.Integer(), nullable=False),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["closed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("first_response_hours > 0", name="ck_support_requests_target_positive"),
        sa.CheckConstraint(
            "first_response_due_at > created_at", name="ck_support_requests_due_after_creation"
        ),
        sa.CheckConstraint(
            "status = 'open' OR answered_at IS NOT NULL",
            name="ck_support_requests_handled_has_date",
        ),
        sa.CheckConstraint(
            "status <> 'closed' OR resolution IS NOT NULL",
            name="ck_support_requests_closed_has_resolution",
        ),
        sa.CheckConstraint(
            "category IN ('question', 'incident', 'extraction', 'report', 'billing', 'privacy', 'other')",
            name="ck_support_requests_category_values",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'closed')",
            name="ck_support_requests_status_values",
        ),
    )
    op.create_index("ix_support_requests_organization_id", "support_requests", ["organization_id"])
    op.create_index("ix_support_requests_category", "support_requests", ["category"])
    op.create_index("ix_support_requests_status", "support_requests", ["status"])
    op.create_index(
        "ix_support_requests_first_response_due_at", "support_requests", ["first_response_due_at"]
    )
    op.create_index(
        "ix_support_requests_open",
        "support_requests",
        ["organization_id", "status", "first_response_due_at"],
    )
    if _is_postgresql():
        _enable_rls()


def downgrade() -> None:
    if _is_postgresql():
        _disable_rls()
    op.drop_index("ix_support_requests_open", table_name="support_requests")
    op.drop_index("ix_support_requests_first_response_due_at", table_name="support_requests")
    op.drop_index("ix_support_requests_status", table_name="support_requests")
    op.drop_index("ix_support_requests_category", table_name="support_requests")
    op.drop_index("ix_support_requests_organization_id", table_name="support_requests")
    op.drop_table("support_requests")
