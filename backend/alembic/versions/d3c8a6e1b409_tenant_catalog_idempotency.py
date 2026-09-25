"""tenant catalog idempotency

Revision ID: d3c8a6e1b409
Revises: f6a2d9b41c07
Create Date: 2026-09-25 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3c8a6e1b409"
down_revision: str | Sequence[str] | None = "f6a2d9b41c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Suppliers and products already have tenant RLS from the identity migration.
    # These nullable fields preserve foundation rows while making public create
    # requests replay-safe per organisation.
    with op.batch_alter_table("suppliers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("request_sha256", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_suppliers_organization_idempotency_key",
            ["organization_id", "idempotency_key"],
        )
        batch_op.create_check_constraint(
            "ck_suppliers_request_sha256_length",
            "request_sha256 IS NULL OR length(request_sha256) = 64",
        )

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("request_sha256", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_products_organization_idempotency_key",
            ["organization_id", "idempotency_key"],
        )
        batch_op.create_check_constraint(
            "ck_products_request_sha256_length",
            "request_sha256 IS NULL OR length(request_sha256) = 64",
        )


def downgrade() -> None:
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_constraint("ck_products_request_sha256_length", type_="check")
        batch_op.drop_constraint(
            "uq_products_organization_idempotency_key", type_="unique"
        )
        batch_op.drop_column("request_sha256")
        batch_op.drop_column("idempotency_key")

    with op.batch_alter_table("suppliers", schema=None) as batch_op:
        batch_op.drop_constraint("ck_suppliers_request_sha256_length", type_="check")
        batch_op.drop_constraint(
            "uq_suppliers_organization_idempotency_key", type_="unique"
        )
        batch_op.drop_column("request_sha256")
        batch_op.drop_column("idempotency_key")
