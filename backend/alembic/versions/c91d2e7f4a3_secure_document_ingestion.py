"""secure document ingestion

Revision ID: c91d2e7f4a3
Revises: a4f6b2d8e901
Create Date: 2026-09-24 18:40:00.000000

"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c91d2e7f4a3"
down_revision: Union[str, Sequence[str], None] = "a4f6b2d8e901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _add_document_permissions_to_system_roles() -> None:
    """Merge, rather than replace, the new baseline permissions on system roles.

    The original role-seeding revision may already have run in a customer
    database.  A data migration is therefore required in addition to the
    startup helper: deployments must have correct RBAC before the first request
    reaches a newly started application. Custom/non-system roles are untouched.
    """
    # ``alembic upgrade --sql`` has no live result set to inspect. Emit a
    # PostgreSQL merge statement so an offline deployment script remains
    # semantically complete; regular online upgrades use typed JSON binding.
    if op.get_context().as_sql:
        if _is_postgresql():
            op.execute(
                """
                UPDATE roles AS r
                SET permissions_json = (
                    SELECT jsonb_agg(permission ORDER BY permission)::json
                    FROM (
                        SELECT DISTINCT item.value AS permission
                        FROM jsonb_array_elements_text(
                            COALESCE(r.permissions_json::jsonb, '[]'::jsonb) ||
                            CASE
                                WHEN r.code = 'viewer' THEN '[\"documents:read\"]'::jsonb
                                ELSE '[\"documents:read\", \"documents:manage\"]'::jsonb
                            END
                        ) AS item(value)
                    ) AS merged_permissions
                ),
                updated_at = CURRENT_TIMESTAMP
                WHERE r.is_system_role = true
                  AND r.code IN ('owner', 'admin', 'analyst', 'viewer')
                """
            )
        return

    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("is_system_role", sa.Boolean()),
        sa.column("permissions_json", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    additions = {
        "owner": ("documents:read", "documents:manage"),
        "admin": ("documents:read", "documents:manage"),
        "analyst": ("documents:read", "documents:manage"),
        "viewer": ("documents:read",),
    }
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(roles.c.id, roles.c.code, roles.c.permissions_json).where(
            roles.c.is_system_role.is_(True),
            roles.c.code.in_(tuple(additions)),
        )
    ).mappings()
    for row in rows:
        existing = row["permissions_json"]
        if isinstance(existing, str):
            try:
                existing = json.loads(existing)
            except json.JSONDecodeError:
                existing = []
        permissions = [item for item in (existing or []) if isinstance(item, str)]
        merged = list(dict.fromkeys([*permissions, *additions[row["code"]]]))
        if merged != permissions:
            connection.execute(
                roles.update()
                .where(roles.c.id == row["id"])
                .values(permissions_json=merged, updated_at=datetime.now(timezone.utc))
            )


def upgrade() -> None:
    op.create_table(
        "document_uploads",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("finalized_document_version_id", sa.Uuid(), nullable=True),
        sa.Column("quarantine_storage_key", sa.String(length=1024), nullable=False),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("declared_content_type", sa.String(length=255), nullable=False),
        sa.Column("declared_size_bytes", sa.Integer(), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=True),
        sa.Column("uploaded_content_type", sa.String(length=255), nullable=True),
        sa.Column("uploaded_size_bytes", sa.Integer(), nullable=True),
        sa.Column("uploaded_sha256", sa.String(length=64), nullable=True),
        sa.Column("object_etag", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending_upload",
                "uploaded",
                "scanning",
                "clean",
                "rejected",
                "failed",
                "expired",
                name="document_upload_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_engine", sa.String(length=128), nullable=True),
        sa.Column("scan_signature_version", sa.String(length=255), nullable=True),
        sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_error_code", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("declared_size_bytes > 0", name="ck_document_uploads_declared_size_positive"),
        sa.CheckConstraint(
            "uploaded_size_bytes IS NULL OR uploaded_size_bytes > 0",
            name="ck_document_uploads_uploaded_size_positive",
        ),
        sa.CheckConstraint(
            "expected_sha256 IS NULL OR length(expected_sha256) = 64",
            name="ck_document_uploads_expected_sha256_length",
        ),
        sa.CheckConstraint(
            "uploaded_sha256 IS NULL OR length(uploaded_sha256) = 64",
            name="ck_document_uploads_uploaded_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_uploads_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_document_uploads_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["finalized_document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_document_uploads_finalized_document_version_id_document_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_document_uploads_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_uploads")),
        sa.UniqueConstraint("finalized_document_version_id", name=op.f("uq_document_uploads_finalized_document_version_id")),
        sa.UniqueConstraint("quarantine_storage_key", name=op.f("uq_document_uploads_quarantine_storage_key")),
    )
    with op.batch_alter_table("document_uploads", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_document_uploads_document_id"), ["document_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_document_uploads_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_document_uploads_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_document_uploads_requested_by_user_id"), ["requested_by_user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_document_uploads_status"), ["status"], unique=False)

    _add_document_permissions_to_system_roles()

    if _is_postgresql():
        op.execute('ALTER TABLE "document_uploads" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "document_uploads" FORCE ROW LEVEL SECURITY')
        op.execute(
            '''CREATE POLICY "p_document_uploads_tenant" ON "document_uploads"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute('DROP POLICY IF EXISTS "p_document_uploads_tenant" ON "document_uploads"')
        op.execute('ALTER TABLE "document_uploads" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "document_uploads" DISABLE ROW LEVEL SECURITY')

    with op.batch_alter_table("document_uploads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_uploads_status"))
        batch_op.drop_index(batch_op.f("ix_document_uploads_requested_by_user_id"))
        batch_op.drop_index(batch_op.f("ix_document_uploads_organization_id"))
        batch_op.drop_index(batch_op.f("ix_document_uploads_expires_at"))
        batch_op.drop_index(batch_op.f("ix_document_uploads_document_id"))
    op.drop_table("document_uploads")
