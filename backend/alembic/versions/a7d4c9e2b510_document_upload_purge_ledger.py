"""document upload purge ledger

Revision ID: a7d4c9e2b510
Revises: e8f9a1b2c3d4
Create Date: 2026-10-20 21:40:00.000000

La libération d'un objet de quarantaine est un geste hors transaction: le stockage
d'objets et PostgreSQL ne partagent pas de commit. Sans registre, un échec de
suppression - ou un krach entre le commit de la transition et la suppression - laissait
les octets en quarantaine sans aucune trace à rejouer, et le service pouvait annoncer
`quarantine_deleted: true` sans l'avoir prouvé. Ce maillon ajoute l'état durable qui
rend la reprise possible et le résultat vérifiable.

Les contraintes sont posées en `batch_alter_table`: SQLite ne sait pas ajouter une
contrainte par ALTER, et le mode batch recopie la table sur ce dialecte seulement -
PostgreSQL reçoit un ALTER TABLE ordinaire, donc aucune réécriture de table en staging.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4c9e2b510"
down_revision: Union[str, Sequence[str], None] = "e8f9a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    ("purge_required", lambda: sa.Column("purge_required", sa.Boolean(), server_default=sa.text("false"), nullable=False)),
    ("purge_kind", lambda: sa.Column("purge_kind", sa.String(length=32), nullable=True)),
    ("purge_reason", lambda: sa.Column("purge_reason", sa.String(length=64), nullable=True)),
    ("purge_attempts", lambda: sa.Column("purge_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False)),
    ("purge_last_error", lambda: sa.Column("purge_last_error", sa.String(length=64), nullable=True)),
    ("purge_requested_at", lambda: sa.Column("purge_requested_at", sa.DateTime(timezone=True), nullable=True)),
)


def upgrade() -> None:
    for _name, factory in _COLUMNS:
        op.add_column("document_uploads", factory())
    with op.batch_alter_table("document_uploads") as batch_op:
        batch_op.create_check_constraint(
            "ck_document_uploads_purge_attempts_nonnegative",
            "purge_attempts >= 0",
        )
        batch_op.create_check_constraint(
            "ck_document_uploads_purge_kind_required",
            "purge_required = false OR purge_kind IS NOT NULL",
        )
    # La passe de reprise ne cherche que les registres ouverts: cet index evite de
    # scanner un historique de libérations closes a chaque tempo du worker.
    op.create_index(
        "ix_document_uploads_purge_open",
        "document_uploads",
        ["organization_id", "purge_requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_uploads_purge_open", table_name="document_uploads")
    with op.batch_alter_table("document_uploads") as batch_op:
        batch_op.drop_constraint("ck_document_uploads_purge_kind_required", type_="check")
        batch_op.drop_constraint("ck_document_uploads_purge_attempts_nonnegative", type_="check")
    for name, _factory in reversed(_COLUMNS):
        op.drop_column("document_uploads", name)
