"""enterprise api keys and legal holds

Revision ID: e8f9a1b2c3d4
Revises: d3c8a6e1b409
Create Date: 2026-09-29 17:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f9a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d3c8a6e1b409"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _json_type():
    if _is_postgresql():
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("hashed_secret", sa.String(length=128), nullable=False),
        sa.Column("scopes", _json_type(), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_api_keys_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_api_keys_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
    )
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_api_keys_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_api_keys_prefix"), ["prefix"], unique=False)

    op.create_table(
        "legal_holds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("case_reference", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_legal_holds_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_legal_holds_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_holds")),
    )
    with op.batch_alter_table("legal_holds", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_legal_holds_organization_id"), ["organization_id"], unique=False)

    if _is_postgresql():
        op.execute('ALTER TABLE "api_keys" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "api_keys" FORCE ROW LEVEL SECURITY')
        op.execute(
            '''CREATE POLICY "p_api_keys_tenant" ON "api_keys"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )

        op.execute('ALTER TABLE "legal_holds" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "legal_holds" FORCE ROW LEVEL SECURITY')
        op.execute(
            '''CREATE POLICY "p_legal_holds_tenant" ON "legal_holds"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute('DROP POLICY IF EXISTS "p_legal_holds_tenant" ON "legal_holds"')
        op.execute('ALTER TABLE "legal_holds" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "legal_holds" DISABLE ROW LEVEL SECURITY')

        op.execute('DROP POLICY IF EXISTS "p_api_keys_tenant" ON "api_keys"')
        op.execute('ALTER TABLE "api_keys" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "api_keys" DISABLE ROW LEVEL SECURITY')

    with op.batch_alter_table("legal_holds", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_legal_holds_organization_id"))
    op.drop_table("legal_holds")

    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_keys_prefix"))
        batch_op.drop_index(batch_op.f("ix_api_keys_organization_id"))
    op.drop_table("api_keys")
