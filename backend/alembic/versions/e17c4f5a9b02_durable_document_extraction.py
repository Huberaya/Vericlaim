"""durable document extraction

Revision ID: e17c4f5a9b02
Revises: c91d2e7f4a3
Create Date: 2026-09-24 20:15:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e17c4f5a9b02"
down_revision: str | Sequence[str] | None = "c91d2e7f4a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "document_extraction_jobs",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "completed",
                "failed",
                name="document_extraction_job_status",
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
        sa.CheckConstraint("attempt_count >= 0", name="ck_document_extraction_jobs_attempt_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_document_extraction_jobs_max_attempts_positive"),
        sa.CheckConstraint("max_attempts >= attempt_count", name="ck_document_extraction_jobs_attempts_within_max"),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_document_extraction_jobs_document_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_document_extraction_jobs_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_document_extraction_jobs_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_extraction_jobs")),
        sa.UniqueConstraint("document_version_id", name=op.f("uq_document_extraction_jobs_document_version_id")),
    )
    with op.batch_alter_table("document_extraction_jobs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_document_extraction_jobs_available_at"),
            ["available_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_document_extraction_jobs_claim",
            ["organization_id", "status", "available_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_document_extraction_jobs_lease_expires_at"),
            ["lease_expires_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_document_extraction_jobs_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_document_extraction_jobs_requested_by_user_id"),
            ["requested_by_user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_document_extraction_jobs_status"),
            ["status"],
            unique=False,
        )

    if _is_postgresql():
        op.execute('ALTER TABLE "document_extraction_jobs" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "document_extraction_jobs" FORCE ROW LEVEL SECURITY')
        op.execute(
            '''CREATE POLICY "p_document_extraction_jobs_tenant" ON "document_extraction_jobs"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute('DROP POLICY IF EXISTS "p_document_extraction_jobs_tenant" ON "document_extraction_jobs"')
        op.execute('ALTER TABLE "document_extraction_jobs" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "document_extraction_jobs" DISABLE ROW LEVEL SECURITY')

    with op.batch_alter_table("document_extraction_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_extraction_jobs_status"))
        batch_op.drop_index(batch_op.f("ix_document_extraction_jobs_requested_by_user_id"))
        batch_op.drop_index(batch_op.f("ix_document_extraction_jobs_organization_id"))
        batch_op.drop_index(batch_op.f("ix_document_extraction_jobs_lease_expires_at"))
        batch_op.drop_index("ix_document_extraction_jobs_claim")
        batch_op.drop_index(batch_op.f("ix_document_extraction_jobs_available_at"))
    op.drop_table("document_extraction_jobs")
