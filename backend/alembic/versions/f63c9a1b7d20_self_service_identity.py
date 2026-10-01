"""self-service identity: passwords, one-shot tokens, e-mail outbox

Revision ID: f63c9a1b7d20
Revises: e52b7f1a9c34
Create Date: 2026-09-30 19:10:00.000000

The audit found that a customer could not create an account, could not reset a
password, and that an invitation created a membership whose e-mail was never
sent (the README admitted it). This migration adds what the journey needs:

* `users.password_hash`, `password_updated_at`, `email_verified_at` — nullable,
  because an SSO account legitimately has no password.
* `identity_tokens` — single-use tokens for verification, password reset and
  invitations. Only the SHA-256 digest is stored.
* `email_messages` — the transactional outbox, so "the e-mail was sent" is a
  recorded fact rather than a claim.
* `password_login_attempts` — the lockout counter for the password fallback.

No data is back-filled: an existing account has no password until its owner sets
one by reset, which is the honest state and keeps `verify_password` failing closed
on NULL.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f63c9a1b7d20"
down_revision: str | Sequence[str] | None = "e52b7f1a9c34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "identity_tokens",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_identity_tokens_hash_length"),
        sa.CheckConstraint(
            "purpose IN ('email_verification', 'password_reset', 'invitation')",
            name="identity_token_purpose",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_identity_tokens_user_id", "identity_tokens", ["user_id"])
    op.create_index("ix_identity_tokens_organization_id", "identity_tokens", ["organization_id"])
    op.create_index("ix_identity_tokens_purpose", "identity_tokens", ["purpose"])
    op.create_index("ix_identity_tokens_token_hash", "identity_tokens", ["token_hash"], unique=True)
    op.create_index("ix_identity_tokens_expires_at", "identity_tokens", ["expires_at"])
    op.create_index("ix_identity_tokens_used_at", "identity_tokens", ["used_at"])

    op.create_table(
        "email_messages",
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("transport", sa.String(length=64), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'not_configured')",
            name="email_message_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_messages_organization_id", "email_messages", ["organization_id"])
    op.create_index("ix_email_messages_recipient_email", "email_messages", ["recipient_email"])
    op.create_index("ix_email_messages_purpose", "email_messages", ["purpose"])
    op.create_index("ix_email_messages_status", "email_messages", ["status"])

    op.create_table(
        "password_login_attempts",
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("failed_count >= 0", name="ck_password_login_attempts_count_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_normalized"),
    )
    op.create_index(
        "ix_password_login_attempts_email_normalized",
        "password_login_attempts",
        ["email_normalized"],
    )
    op.create_index("ix_password_login_attempts_locked_until", "password_login_attempts", ["locked_until"])


def downgrade() -> None:
    op.drop_index("ix_password_login_attempts_locked_until", table_name="password_login_attempts")
    op.drop_index("ix_password_login_attempts_email_normalized", table_name="password_login_attempts")
    op.drop_table("password_login_attempts")

    for index_name in (
        "ix_email_messages_status",
        "ix_email_messages_purpose",
        "ix_email_messages_recipient_email",
        "ix_email_messages_organization_id",
    ):
        op.drop_index(index_name, table_name="email_messages")
    op.drop_table("email_messages")

    for index_name in (
        "ix_identity_tokens_used_at",
        "ix_identity_tokens_expires_at",
        "ix_identity_tokens_token_hash",
        "ix_identity_tokens_purpose",
        "ix_identity_tokens_organization_id",
        "ix_identity_tokens_user_id",
    ):
        op.drop_index(index_name, table_name="identity_tokens")
    op.drop_table("identity_tokens")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_verified_at")
        batch_op.drop_column("password_updated_at")
        batch_op.drop_column("password_hash")
