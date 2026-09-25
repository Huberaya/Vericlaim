"""durable analysis claim detection

Revision ID: f6a2d9b41c07
Revises: e17c4f5a9b02
Create Date: 2026-09-25 11:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a2d9b41c07"
down_revision: str | Sequence[str] | None = "e17c4f5a9b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _analysis_risk_level_type() -> sa.Enum:
    return sa.Enum(
        "low",
        "medium",
        "high",
        "review_required",
        "insufficient_information",
        name="analysis_risk_level",
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    # Preserve the existing analysis-version history while making request
    # replay explicit and tenant-scoped. NULL is retained for foundation rows.
    with op.batch_alter_table("analysis_versions", schema=None) as batch_op:
        # C5 only detects lexical facts and must not manufacture a persistent
        # legal/risk conclusion. Historical rows retain their existing value.
        batch_op.alter_column(
            "overall_risk_level",
            existing_type=_analysis_risk_level_type(),
            existing_nullable=False,
            nullable=True,
        )
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("request_sha256", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_analysis_versions_organization_idempotency_key",
            ["organization_id", "idempotency_key"],
        )
        batch_op.create_check_constraint(
            "ck_analysis_versions_request_sha256_length",
            "request_sha256 IS NULL OR length(request_sha256) = 64",
        )

    op.create_table(
        "analysis_detection_jobs",
        sa.Column("analysis_version_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "completed",
                "failed",
                name="analysis_detection_job_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_analysis_detection_jobs_attempt_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_analysis_detection_jobs_max_attempts_positive"),
        sa.CheckConstraint("max_attempts >= attempt_count", name="ck_analysis_detection_jobs_attempts_within_max"),
        sa.ForeignKeyConstraint(
            ["analysis_version_id"],
            ["analysis_versions.id"],
            name=op.f("fk_analysis_detection_jobs_analysis_version_id_analysis_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_analysis_detection_jobs_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_analysis_detection_jobs_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_detection_jobs")),
        sa.UniqueConstraint("analysis_version_id", name=op.f("uq_analysis_detection_jobs_analysis_version_id")),
    )
    with op.batch_alter_table("analysis_detection_jobs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_analysis_detection_jobs_available_at"),
            ["available_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_analysis_detection_jobs_claim",
            ["organization_id", "status", "available_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_analysis_detection_jobs_lease_expires_at"),
            ["lease_expires_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_analysis_detection_jobs_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_analysis_detection_jobs_requested_by_user_id"),
            ["requested_by_user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_analysis_detection_jobs_status"),
            ["status"],
            unique=False,
        )

    if _is_postgresql():
        op.execute('ALTER TABLE "analysis_detection_jobs" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "analysis_detection_jobs" FORCE ROW LEVEL SECURITY')
        op.execute(
            '''CREATE POLICY "p_analysis_detection_jobs_tenant" ON "analysis_detection_jobs"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute('DROP POLICY IF EXISTS "p_analysis_detection_jobs_tenant" ON "analysis_detection_jobs"')
        op.execute('ALTER TABLE "analysis_detection_jobs" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "analysis_detection_jobs" DISABLE ROW LEVEL SECURITY')

    with op.batch_alter_table("analysis_detection_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_analysis_detection_jobs_status"))
        batch_op.drop_index(batch_op.f("ix_analysis_detection_jobs_requested_by_user_id"))
        batch_op.drop_index(batch_op.f("ix_analysis_detection_jobs_organization_id"))
        batch_op.drop_index(batch_op.f("ix_analysis_detection_jobs_lease_expires_at"))
        batch_op.drop_index("ix_analysis_detection_jobs_claim")
        batch_op.drop_index(batch_op.f("ix_analysis_detection_jobs_available_at"))
    op.drop_table("analysis_detection_jobs")

    # A downgrade restores the original non-null foundation contract without
    # dropping any historical row created by C5.
    op.execute(
        "UPDATE analysis_versions SET overall_risk_level = 'insufficient_information' "
        "WHERE overall_risk_level IS NULL"
    )
    with op.batch_alter_table("analysis_versions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_analysis_versions_request_sha256_length", type_="check")
        batch_op.drop_constraint("uq_analysis_versions_organization_idempotency_key", type_="unique")
        batch_op.drop_column("request_sha256")
        batch_op.drop_column("idempotency_key")
        batch_op.alter_column(
            "overall_risk_level",
            existing_type=_analysis_risk_level_type(),
            existing_nullable=True,
            nullable=False,
        )
