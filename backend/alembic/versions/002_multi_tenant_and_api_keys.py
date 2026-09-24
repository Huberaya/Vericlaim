"""Multi-tenant isolation and API keys schema.

Revision ID: 002_multi_tenant_and_api_keys
Revises: 001_initial_schema
Create Date: 2026-09-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_multi_tenant_and_api_keys'
down_revision: Union[str, Sequence[str], None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if 'organizations' not in tables:
        op.create_table(
            'organizations',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('name', sa.String(length=128), nullable=False),
            sa.Column('slug', sa.String(length=64), nullable=False),
            sa.Column('tier', sa.String(length=32), nullable=False, server_default='standard'),
            sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_organizations_slug'), 'organizations', ['slug'], unique=True)
        op.create_index(op.f('ix_organizations_created_at_utc'), 'organizations', ['created_at_utc'], unique=False)

    if 'api_keys' not in tables:
        op.create_table(
            'api_keys',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('organization_id', sa.String(length=36), nullable=False),
            sa.Column('name', sa.String(length=128), nullable=False),
            sa.Column('key_prefix', sa.String(length=16), nullable=False),
            sa.Column('hashed_key', sa.String(length=64), nullable=False),
            sa.Column('scopes', sa.JSON(), nullable=False),
            sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_used_at_utc', sa.DateTime(timezone=True), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_api_keys_hashed_key'), 'api_keys', ['hashed_key'], unique=True)
        op.create_index(op.f('ix_api_keys_organization_id'), 'api_keys', ['organization_id'], unique=False)
        op.create_index(op.f('ix_api_keys_key_prefix'), 'api_keys', ['key_prefix'], unique=False)

    # Ajout de organization_id sur audit_records si non présent
    columns = [c['name'] for c in inspector.get_columns('audit_records')] if 'audit_records' in tables else []
    if 'organization_id' not in columns:
        op.add_column('audit_records', sa.Column('organization_id', sa.String(length=36), nullable=False, server_default='default'))
        op.create_index(op.f('ix_audit_records_organization_id'), 'audit_records', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_table('api_keys')
    op.drop_table('organizations')
    try:
        op.drop_column('audit_records', 'organization_id')
    except Exception:
        pass
