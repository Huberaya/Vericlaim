"""Initial schema for audit_records table.

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-09-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial_schema'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if 'audit_records' not in tables:
        op.create_table(
            'audit_records',
            sa.Column('sequence', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('audit_id', sa.String(length=36), nullable=False),
            sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
            sa.Column('source_sha256', sa.String(length=64), nullable=False),
            sa.Column('evidence_manifest_sha256', sa.String(length=64), nullable=False),
            sa.Column('report_sha256', sa.String(length=64), nullable=False),
            sa.Column('previous_record_hash', sa.String(length=64), nullable=True),
            sa.Column('record_hash', sa.String(length=64), nullable=False),
            sa.Column('summary_json', sa.JSON(), nullable=False),
            sa.Column('supplier_name', sa.String(length=128), nullable=True),
            sa.Column('product_identifier', sa.String(length=128), nullable=True),
            sa.Column('report_json', sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint('sequence')
        )
        op.create_index(op.f('ix_audit_records_audit_id'), 'audit_records', ['audit_id'], unique=True)
        op.create_index(op.f('ix_audit_records_created_at_utc'), 'audit_records', ['created_at_utc'], unique=False)
        op.create_index(op.f('ix_audit_records_product_identifier'), 'audit_records', ['product_identifier'], unique=False)
        op.create_index(op.f('ix_audit_records_record_hash'), 'audit_records', ['record_hash'], unique=True)
        op.create_index(op.f('ix_audit_records_source_sha256'), 'audit_records', ['source_sha256'], unique=False)
        op.create_index(op.f('ix_audit_records_supplier_name'), 'audit_records', ['supplier_name'], unique=False)


def downgrade() -> None:
    op.drop_table('audit_records')
