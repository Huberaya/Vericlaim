"""demandes d'exercice des droits : reçues, datées, suivies jusqu'à la preuve

Revision ID: e91c4b7a25d0
Revises: d4a7c1f30b52
Create Date: 2026-09-30 19:55:00.000000

C20 avait outillé l'**exécution** des droits (export, effacement) côté organisation.
Il manquait la **réception** : rien ne permettait de dire qu'une demande d'une personne
avait été reçue, dans quel délai elle devait être traitée, ni ce qui lui avait été
effectivement remis. Une procédure qui n'est pas datée n'est pas une procédure.

``data_subject_requests`` porte donc :

* le droit exercé, nommé comme dans le règlement (art. 15, 16, 17, 18, 20, 21) ;
* ``received_at`` et ``due_at`` — le délai d'un mois de l'article 12.3 est **calculé**,
  jamais saisi, et une prolongation de deux mois est elle aussi datée et motivée ;
* ``outcome_reference`` : ce qui a été remis (empreinte d'export, référence de
  manifeste d'effacement). Une contrainte refuse un accord sans référence : un accord
  sans preuve est un accord invérifiable.

Aucune donnée n'est reprise. Les demandes traitées hors produit avant cette migration
n'existent pas ici et ne peuvent pas être inventées.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e91c4b7a25d0"
down_revision: str | Sequence[str] | None = "d4a7c1f30b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ("data_subject_requests",)


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _enable_rls() -> None:
    for table_name in RLS_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "p_{table_name}_tenant" ON "{table_name}"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def _disable_rls() -> None:
    for table_name in RLS_TABLES:
        op.execute(f'DROP POLICY IF EXISTS "p_{table_name}_tenant" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "data_subject_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("request_type", sa.String(length=32), nullable=False),
        sa.Column("requester_email", sa.String(length=320), nullable=False),
        sa.Column("requester_name", sa.String(length=255), nullable=True),
        sa.Column("requester_is_member", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extension_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extension_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("outcome_reference", sa.String(length=255), nullable=True),
        sa.Column("outcome_detail", sa.Text(), nullable=True),
        sa.Column("refusal_reason", sa.Text(), nullable=True),
        sa.Column("handled_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["handled_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("due_at > received_at", name="ck_data_subject_requests_due_after_receipt"),
        sa.CheckConstraint(
            "extension_due_at IS NULL OR extension_reason IS NOT NULL",
            name="ck_data_subject_requests_extension_has_reason",
        ),
        sa.CheckConstraint(
            "extension_due_at IS NULL OR extension_due_at > due_at",
            name="ck_data_subject_requests_extension_after_due",
        ),
        sa.CheckConstraint(
            "status NOT IN ('completed', 'refused') OR completed_at IS NOT NULL",
            name="ck_data_subject_requests_closed_has_date",
        ),
        sa.CheckConstraint(
            "status <> 'refused' OR refusal_reason IS NOT NULL",
            name="ck_data_subject_requests_refusal_has_reason",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome = 'refused' OR outcome_reference IS NOT NULL",
            name="ck_data_subject_requests_grant_references_delivery",
        ),
        sa.CheckConstraint(
            "request_type IN ('access', 'rectification', 'erasure', 'restriction', 'portability', 'objection')",
            name="ck_data_subject_requests_type_values",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'in_progress', 'completed', 'refused', 'withdrawn')",
            name="ck_data_subject_requests_status_values",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('granted', 'partially_granted', 'refused')",
            name="ck_data_subject_requests_outcome_values",
        ),
    )
    op.create_index(
        "ix_data_subject_requests_organization_id", "data_subject_requests", ["organization_id"]
    )
    op.create_index("ix_data_subject_requests_request_type", "data_subject_requests", ["request_type"])
    op.create_index("ix_data_subject_requests_received_at", "data_subject_requests", ["received_at"])
    op.create_index("ix_data_subject_requests_due_at", "data_subject_requests", ["due_at"])
    op.create_index("ix_data_subject_requests_status", "data_subject_requests", ["status"])
    op.create_index(
        "ix_data_subject_requests_open",
        "data_subject_requests",
        ["organization_id", "status", "due_at"],
    )
    if _is_postgresql():
        _enable_rls()


def downgrade() -> None:
    if _is_postgresql():
        _disable_rls()
    op.drop_index("ix_data_subject_requests_open", table_name="data_subject_requests")
    op.drop_index("ix_data_subject_requests_status", table_name="data_subject_requests")
    op.drop_index("ix_data_subject_requests_due_at", table_name="data_subject_requests")
    op.drop_index("ix_data_subject_requests_received_at", table_name="data_subject_requests")
    op.drop_index("ix_data_subject_requests_request_type", table_name="data_subject_requests")
    op.drop_index("ix_data_subject_requests_organization_id", table_name="data_subject_requests")
    op.drop_table("data_subject_requests")
