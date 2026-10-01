"""persist report verification reference and signature

Revision ID: c8d1f6a02e57
Revises: b2c7e4d9a310
Create Date: 2026-09-30 15:00:00.000000

Additive. Reports generated before this revision have no signature, which is the
honest state: they cannot be verified retroactively, and the verification
endpoint must say so rather than treating an absent signature as valid.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d1f6a02e57"
down_revision: str | Sequence[str] | None = "b2c7e4d9a310"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("verification_reference", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("signature", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("signature_key_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("signature_schema_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("signed_result_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("signed_rulebook_version", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("signed_engine_version", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_reports_verification_reference", ["verification_reference"]
        )
        batch_op.create_check_constraint(
            "ck_reports_signature_length", "signature IS NULL OR length(signature) = 64"
        )
        batch_op.create_check_constraint(
            "ck_reports_signed_result_sha256_length",
            "signed_result_sha256 IS NULL OR length(signed_result_sha256) = 64",
        )


def downgrade() -> None:
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_constraint("ck_reports_signed_result_sha256_length", type_="check")
        batch_op.drop_constraint("ck_reports_signature_length", type_="check")
        batch_op.drop_constraint("uq_reports_verification_reference", type_="unique")
        batch_op.drop_column("signed_engine_version")
        batch_op.drop_column("signed_rulebook_version")
        batch_op.drop_column("signed_result_sha256")
        batch_op.drop_column("signature_schema_version")
        batch_op.drop_column("signature_key_id")
        batch_op.drop_column("signature")
        batch_op.drop_column("verification_reference")
