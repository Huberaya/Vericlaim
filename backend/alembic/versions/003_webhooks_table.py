"""Create webhooks table for event notifications.

Revision ID: 003_webhooks_table
Revises: 002_multi_tenant_and_api_keys
Create Date: 2026-09-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003_webhooks_table'
down_revision: Union[str, Sequence[str], None] = '002_multi_tenant_and_api_keys'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if 'webhooks' not in tables:
        op.create_table(
            'webhooks',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('organization_id', sa.String(length=36), nullable=False),
            sa.Column('url', sa.String(length=2048), nullable=False),
            sa.Column('secret', sa.String(length=64), nullable=False),
            sa.Column('description', sa.String(length=256), nullable=False),
            sa.Column('events', sa.JSON(), nullable=False),
            sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_triggered_at_utc', sa.DateTime(timezone=True), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_webhooks_organization_id'), 'webhooks', ['organization_id'], unique=False)
        op.create_index(op.f('ix_webhooks_created_at_utc'), 'webhooks', ['created_at_utc'], unique=False)


def downgrade() -> None:
    op.drop_table('webhooks')
