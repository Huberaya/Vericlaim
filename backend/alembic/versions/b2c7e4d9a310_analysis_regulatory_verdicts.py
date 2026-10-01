"""persist regulatory verdicts per analysis version

Revision ID: b2c7e4d9a310
Revises: e8f9a1b2c3d4
Create Date: 2026-09-30 12:00:00.000000

Additive only. Versions produced before this revision keep
``overall_compliance`` and ``evaluation_context_json`` at NULL, which is the
signal that no regulatory conclusion exists for them. The reader must render
"verdict non disponible (pipeline v1)" instead of inferring compliance from an
absence of rows.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c7e4d9a310"
down_revision: str | Sequence[str] | None = "e8f9a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _verdict_enum(name: str, values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    with op.batch_alter_table("analysis_versions", schema=None) as batch_op:
        # Declared as a constrained VARCHAR rather than an Enum column: SQLite
        # recreates the whole table to drop a column, and a table-level CHECK
        # that survives the column would make the downgrade impossible. A named
        # constraint can be dropped explicitly, which keeps this migration
        # reversible on both backends.
        batch_op.add_column(sa.Column("overall_compliance", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("evaluation_context_json", sa.JSON(), nullable=True))
        batch_op.create_check_constraint(
            "ck_analysis_versions_overall_compliance",
            "overall_compliance IS NULL OR overall_compliance IN "
            "('COMPLIANT', 'NON_COMPLIANT', 'CONDITIONAL_REJECT', "
            "'REVIEW_REQUIRED', 'UPCOMING_REQUIREMENTS', 'NO_CLAIMS_DETECTED')",
        )

    op.create_table(
        "analysis_verdicts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("analysis_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("claim_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("document_segment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column(
            "claim_type",
            _verdict_enum(
                "verdict_claim_type",
                (
                    "biodegradable",
                    "nature_friendly",
                    "generic_environmental",
                    "carbon_neutrality",
                    "comparative",
                    "quantified_climate",
                    "recyclable",
                ),
            ),
            nullable=False,
        ),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("trigger_text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("rule_title", sa.Text(), nullable=False),
        sa.Column("law_reference", sa.Text(), nullable=False),
        sa.Column(
            "legal_force",
            _verdict_enum(
                "verdict_legal_force",
                (
                    "BINDING_FR",
                    "EU_DIRECTIVE_DATE_GATED",
                    "PROPOSAL_ONLY",
                    "VOLUNTARY_STANDARD",
                    "INTERNAL_EVIDENCE_CONTROL",
                ),
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            _verdict_enum("verdict_severity", ("LOW", "MEDIUM", "HIGH", "CRITICAL")),
            nullable=False,
        ),
        sa.Column(
            "verdict",
            _verdict_enum(
                "verdict_value",
                (
                    "STRICTLY_PROHIBITED",
                    "NON_COMPLIANT",
                    "CONDITIONAL_REJECT",
                    "REVIEW_REQUIRED",
                    "COMPLIANT",
                    "NOT_APPLICABLE",
                    "UPCOMING",
                    "ADVISORY_ONLY",
                ),
            ),
            nullable=False,
        ),
        sa.Column("is_legal_violation", sa.Boolean(), nullable=False),
        sa.Column("safe_harbor_applicable", sa.Boolean(), nullable=False),
        sa.Column("safe_harbor_reason", sa.Text(), nullable=True),
        sa.Column("legal_caveat", sa.Text(), nullable=True),
        sa.Column("source_urls_json", sa.JSON(), nullable=False),
        sa.Column("required_evidence_json", sa.JSON(), nullable=False),
        sa.Column("evidence_checks_json", sa.JSON(), nullable=False),
        sa.Column("reasoning_steps_json", sa.JSON(), nullable=False),
        sa.Column("remediation_json", sa.JSON(), nullable=True),
        sa.Column("sanction_json", sa.JSON(), nullable=True),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("rulebook_version", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_version_id"], ["analysis_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_segment_id"], ["document_segments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_version_id", "sequence_number", name="uq_analysis_verdicts_version_sequence"
        ),
        sa.UniqueConstraint(
            "analysis_version_id", "claim_id", "rule_id", name="uq_analysis_verdicts_version_claim_rule"
        ),
        sa.CheckConstraint("sequence_number >= 0", name="ck_analysis_verdicts_sequence"),
    )
    with op.batch_alter_table("analysis_verdicts", schema=None) as batch_op:
        batch_op.create_index("ix_analysis_verdicts_organization_id", ["organization_id"], unique=False)
        batch_op.create_index("ix_analysis_verdicts_analysis_version_id", ["analysis_version_id"], unique=False)
        batch_op.create_index("ix_analysis_verdicts_claim_id", ["claim_id"], unique=False)
        batch_op.create_index("ix_analysis_verdicts_rule_id", ["rule_id"], unique=False)
        batch_op.create_index("ix_analysis_verdicts_verdict", ["verdict"], unique=False)

    if _is_postgresql():
        op.execute('ALTER TABLE "analysis_verdicts" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "analysis_verdicts" FORCE ROW LEVEL SECURITY')
        op.execute(
            '''CREATE POLICY "p_analysis_verdicts_tenant" ON "analysis_verdicts"
                USING (organization_id = vericlaim_current_organization_id())
                WITH CHECK (organization_id = vericlaim_current_organization_id())'''
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute('DROP POLICY IF EXISTS "p_analysis_verdicts_tenant" ON "analysis_verdicts"')
        op.execute('ALTER TABLE "analysis_verdicts" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "analysis_verdicts" DISABLE ROW LEVEL SECURITY')

    with op.batch_alter_table("analysis_verdicts", schema=None) as batch_op:
        batch_op.drop_index("ix_analysis_verdicts_verdict")
        batch_op.drop_index("ix_analysis_verdicts_rule_id")
        batch_op.drop_index("ix_analysis_verdicts_claim_id")
        batch_op.drop_index("ix_analysis_verdicts_analysis_version_id")
        batch_op.drop_index("ix_analysis_verdicts_organization_id")
    op.drop_table("analysis_verdicts")

    with op.batch_alter_table("analysis_versions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_analysis_versions_overall_compliance", type_="check")
        batch_op.drop_column("evaluation_context_json")
        batch_op.drop_column("overall_compliance")
