"""Create monitored_targets and monitoring_logs tables.

Revision ID: 004_compliance_watcher
Revises: 003_webhooks_table
Create Date: 2026-09-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004_compliance_watcher'
down_revision: Union[str, Sequence[str], None] = '003_webhooks_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if 'monitored_targets' not in tables:
        op.create_table(
            'monitored_targets',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('organization_id', sa.String(length=36), nullable=False),
            sa.Column('name', sa.String(length=128), nullable=False),
            sa.Column('url', sa.String(length=2048), nullable=False),
            sa.Column('frequency_hours', sa.Integer(), nullable=False, server_default='24'),
            sa.Column('last_checked_at_utc', sa.DateTime(timezone=True), nullable=True),
            sa.Column('next_check_due_utc', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_status', sa.String(length=32), nullable=False, server_default='PENDING'),
            sa.Column('last_risk_score', sa.Integer(), nullable=True),
            sa.Column('last_violations_count', sa.Integer(), nullable=True),
            sa.Column('last_audit_id', sa.String(length=36), nullable=True),
            sa.Column('regression_detected', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_monitored_targets_organization_id'), 'monitored_targets', ['organization_id'], unique=False)
        op.create_index(op.f('ix_monitored_targets_next_check_due_utc'), 'monitored_targets', ['next_check_due_utc'], unique=False)
        op.create_index(op.f('ix_monitored_targets_created_at_utc'), 'monitored_targets', ['created_at_utc'], unique=False)

    if 'monitoring_logs' not in tables:
        op.create_table(
            'monitoring_logs',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('target_id', sa.String(length=36), nullable=False),
            sa.Column('executed_at_utc', sa.DateTime(timezone=True), nullable=False),
            sa.Column('overall_compliance', sa.String(length=32), nullable=False),
            sa.Column('risk_score', sa.Integer(), nullable=False),
            sa.Column('violations_count', sa.Integer(), nullable=False),
            sa.Column('detected_claims', sa.JSON(), nullable=False),
            sa.Column('audit_id', sa.String(length=36), nullable=False),
            sa.Column('delta_status', sa.String(length=32), nullable=False, server_default='UNCHANGED'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_monitoring_logs_target_id'), 'monitoring_logs', ['target_id'], unique=False)
        op.create_index(op.f('ix_monitoring_logs_executed_at_utc'), 'monitoring_logs', ['executed_at_utc'], unique=False)


def downgrade() -> None:
    op.drop_table('monitoring_logs')
    op.drop_table('monitored_targets')
