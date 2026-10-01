"""file de génération des rapports : le PDF et le ZIP ne sont plus rendus dans la requête

Revision ID: d4a7c1f30b52
Revises: b7d2e5a91c04
Create Date: 2026-09-30 19:40:00.000000

La génération d'un rapport PDF ou d'un dossier ZIP se faisait **dans la requête
HTTP** : le client attendait le rendu, un redémarrage au mauvais moment perdait le
travail sans trace exploitable, et dimensionner le service revenait à allonger un
timeout. ``reports`` portait déjà ``status``, ``storage_key`` et ``failure_code`` —
des colonnes écrites nulle part — parce que le modèle attendait cette file.

``report_jobs`` est la file elle-même :

* une ligne par demande, avec ``status``, ``attempt_count``/``max_attempts``,
  ``available_at``, un bail (``lease_expires_at``) et le worker qui l'a prise ;
* ``options_json`` fige la demande telle qu'elle a été faite, pour qu'un rapport
  régénéré soit reproductible à l'identique ;
* un index ``(organization_id, status, available_at)`` pour la réclamation, et une
  politique RLS standard : la file d'un locataire ne regarde que lui.

Aucune donnée n'est reprise. Les rapports déjà émis restent lisibles : ils ont
``status = ready`` et un ``sha256``, mais **aucun binaire n'est stocké** — c'est un
manque connu avant cette migration, et il est publié comme tel par
``/api/v1/reports/queue`` plutôt que masqué par une colonne remplie à vide.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a7c1f30b52"
down_revision: str | Sequence[str] | None = "b7d2e5a91c04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ("report_jobs",)


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
    # Le binaire généré est désormais stocké : la taille et le type de contenu
    # deviennent des colonnes du rapport, pas des suppositions du lecteur.
    # ``batch_alter_table`` parce que SQLite ne sait pas ajouter une contrainte par
    # ALTER : la migration doit rester exécutable sur les deux moteurs.
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("size_bytes", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("content_type", sa.String(length=128), nullable=True))
        batch_op.create_check_constraint(
            "ck_reports_size_positive", "size_bytes IS NULL OR size_bytes > 0"
        )
    op.create_table(
        "report_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("analysis_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("report_format", sa.String(length=32), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_version_id"], ["analysis_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_report_jobs_attempt_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_report_jobs_max_attempts_positive"),
        sa.CheckConstraint("max_attempts >= attempt_count", name="ck_report_jobs_attempts_within_max"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_report_jobs_status_values",
        ),
    )
    op.create_index("ix_report_jobs_organization_id", "report_jobs", ["organization_id"])
    op.create_index("ix_report_jobs_analysis_version_id", "report_jobs", ["analysis_version_id"])
    op.create_index("ix_report_jobs_report_id", "report_jobs", ["report_id"])
    op.create_index("ix_report_jobs_status", "report_jobs", ["status"])
    op.create_index("ix_report_jobs_available_at", "report_jobs", ["available_at"])
    op.create_index("ix_report_jobs_lease_expires_at", "report_jobs", ["lease_expires_at"])
    op.create_index("ix_report_jobs_claim", "report_jobs", ["organization_id", "status", "available_at"])
    if _is_postgresql():
        _enable_rls()


def downgrade() -> None:
    if _is_postgresql():
        _disable_rls()
    op.drop_index("ix_report_jobs_claim", table_name="report_jobs")
    op.drop_index("ix_report_jobs_lease_expires_at", table_name="report_jobs")
    op.drop_index("ix_report_jobs_available_at", table_name="report_jobs")
    op.drop_index("ix_report_jobs_status", table_name="report_jobs")
    op.drop_index("ix_report_jobs_report_id", table_name="report_jobs")
    op.drop_index("ix_report_jobs_analysis_version_id", table_name="report_jobs")
    op.drop_index("ix_report_jobs_organization_id", table_name="report_jobs")
    op.drop_table("report_jobs")
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_constraint("ck_reports_size_positive", type_="check")
        batch_op.drop_column("content_type")
        batch_op.drop_column("size_bytes")
