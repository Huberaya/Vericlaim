"""identity and tenant security

Revision ID: 7b3b4c985738
Revises: f3efc9c81c8d
Create Date: 2026-09-24 16:08:18.631358

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7b3b4c985738"
down_revision: Union[str, Sequence[str], None] = "f3efc9c81c8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables that are directly owned by an organisation and can safely use the
# same RLS predicate. Identity lookup tables intentionally remain service-only:
# a request first resolves its opaque session and active membership, then the
# tenant context is installed before any business-data access.
TENANT_TABLES = (
    "audit_records",
    "suppliers",
    "products",
    "documents",
    "document_versions",
    "document_segments",
    "certificates",
    "evidence",
    "analyses",
    "analysis_documents",
    "analysis_versions",
    "claims",
    "evidence_links",
    "risks",
    "recommendations",
    "validations",
    "reports",
    "evidence_requests",
    "audit_events",
)
GLOBAL_OR_TENANT_RULE_TABLES = ("rules", "rule_versions")


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _enable_postgresql_rls() -> None:
    # The function returns NULL rather than raising when the request context is
    # absent/malformed. A missing context therefore exposes no tenant rows.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vericlaim_current_organization_id()
        RETURNS uuid
        LANGUAGE plpgsql
        STABLE
        AS $$
        DECLARE
            context_value text;
        BEGIN
            context_value := current_setting('app.current_organization_id', true);
            IF context_value IS NULL OR context_value = '' THEN
                RETURN NULL;
            END IF;
            RETURN context_value::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN NULL;
        END;
        $$;
        """
    )
    for table_name in TENANT_TABLES:
        policy_name = f"p_{table_name}_tenant"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{policy_name}" ON "{table_name}"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )
    for table_name in GLOBAL_OR_TENANT_RULE_TABLES:
        policy_name = f"p_{table_name}_global_or_tenant"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{policy_name}" ON "{table_name}"
                USING (
                    organization_id IS NULL
                    OR organization_id = vericlaim_current_organization_id()
                )
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def _disable_postgresql_rls() -> None:
    for table_name in GLOBAL_OR_TENANT_RULE_TABLES:
        policy_name = f"p_{table_name}_global_or_tenant"
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
    for table_name in TENANT_TABLES:
        policy_name = f"p_{table_name}_tenant"
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
    op.execute("DROP FUNCTION IF EXISTS vericlaim_current_organization_id()")


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("active_organization_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_auth_sessions_token_hash_length"),
        sa.CheckConstraint("length(csrf_token_hash) = 64", name="ck_auth_sessions_csrf_token_hash_length"),
        sa.CheckConstraint(
            "user_agent_hash IS NULL OR length(user_agent_hash) = 64",
            name="ck_auth_sessions_user_agent_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["active_organization_id"],
            ["organizations.id"],
            name=op.f("fk_auth_sessions_active_organization_id_organizations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_auth_sessions_active_organization_id"), ["active_organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_auth_sessions_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_auth_sessions_revoked_at"), ["revoked_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_auth_sessions_token_hash"), ["token_hash"], unique=True)
        batch_op.create_index(batch_op.f("ix_auth_sessions_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("audit_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("organization_id", sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f("ix_audit_records_organization_id"), ["organization_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_audit_records_organization_id_organizations"),
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "identity_provider",
            existing_type=sa.VARCHAR(length=100),
            type_=sa.String(length=255),
            existing_nullable=True,
        )

    if _is_postgresql():
        _enable_postgresql_rls()


def downgrade() -> None:
    if _is_postgresql():
        _disable_postgresql_rls()

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "identity_provider",
            existing_type=sa.String(length=255),
            type_=sa.VARCHAR(length=100),
            existing_nullable=True,
        )

    with op.batch_alter_table("audit_records", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_audit_records_organization_id_organizations"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_audit_records_organization_id"))
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_token_hash"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_revoked_at"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_expires_at"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_active_organization_id"))
    op.drop_table("auth_sessions")
