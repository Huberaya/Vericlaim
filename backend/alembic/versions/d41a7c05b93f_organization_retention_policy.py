"""organization-declared retention policy

Revision ID: d41a7c05b93f
Revises: c8d1f6a02e57
Create Date: 2026-09-30 16:00:00.000000

Additive and deliberately empty on creation. `GET /pilot/retention-policy` used to
return the publisher's literals for every tenant (a DPO address, an encryption
standard, a hosting region, 5/10 year durations, a review date). None of them were
agreed with any client, and a hosting region displayed by the publisher is a
contractual commitment nobody made. The table starts empty: no row means the
organization has declared nothing, and the API answers null.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d41a7c05b93f"
down_revision: str | Sequence[str] | None = "c8d1f6a02e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "organization_retention_policies",
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("documents_retention_years", sa.Integer(), nullable=True),
        sa.Column("audit_trail_retention_years", sa.Integer(), nullable=True),
        sa.Column("evidence_archive_retention_years", sa.Integer(), nullable=True),
        sa.Column("gdpr_contact_email", sa.String(length=320), nullable=True),
        sa.Column("encryption_standard", sa.String(length=64), nullable=True),
        sa.Column("storage_region", sa.String(length=128), nullable=True),
        sa.Column("export_formats_supported", sa.JSON(), nullable=True),
        sa.Column("last_policy_review", sa.Date(), nullable=True),
        sa.Column("configured_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["configured_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_organization_retention_policies_org"),
        sa.CheckConstraint(
            "documents_retention_years IS NULL OR documents_retention_years > 0",
            name="ck_org_retention_documents_positive",
        ),
        sa.CheckConstraint(
            "audit_trail_retention_years IS NULL OR audit_trail_retention_years > 0",
            name="ck_org_retention_audit_positive",
        ),
        sa.CheckConstraint(
            "evidence_archive_retention_years IS NULL OR evidence_archive_retention_years > 0",
            name="ck_org_retention_evidence_positive",
        ),
        sa.CheckConstraint(
            "storage_region IS NULL OR length(storage_region) > 0",
            name="ck_org_retention_region_not_blank",
        ),
    )
    with op.batch_alter_table("organization_retention_policies", schema=None) as batch_op:
        batch_op.create_index(
            "ix_organization_retention_policies_organization_id",
            ["organization_id"],
            unique=False,
        )

    if _is_postgresql():
        # Same FORCE RLS pattern as every other tenant table: a retention
        # declaration is customer data and must not leak across organizations,
        # including through a table owner connection.
        op.execute('ALTER TABLE "organization_retention_policies" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "organization_retention_policies" FORCE ROW LEVEL SECURITY')
        op.execute(
            '''CREATE POLICY "p_organization_retention_policies_tenant" ON "organization_retention_policies"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute(
            'DROP POLICY IF EXISTS "p_organization_retention_policies_tenant" '
            'ON "organization_retention_policies"'
        )
        op.execute('ALTER TABLE "organization_retention_policies" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "organization_retention_policies" DISABLE ROW LEVEL SECURITY')

    with op.batch_alter_table("organization_retention_policies", schema=None) as batch_op:
        batch_op.drop_index("ix_organization_retention_policies_organization_id")
    op.drop_table("organization_retention_policies")
