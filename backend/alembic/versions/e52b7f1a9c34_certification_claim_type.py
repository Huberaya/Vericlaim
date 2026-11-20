"""add the certification claim type

Revision ID: e52b7f1a9c34
Revises: d41a7c05b93f
Create Date: 2026-09-30 17:30:00.000000

The audit measured that the detector had no notion of a certification claim at
all: "certifié ECOLABEL", "ISO 14001", "FSC" produced no fact, so the whole
claim → evidence chain was empty for the most documented claims on the market,
including the audit case "100 % de matière recyclée certifiée + ACV ISO 14044"
which produced zero detections.

`analysis_verdicts.claim_type` is a VARCHAR with a named CHECK listing the
allowed taxonomy, so adding a value is a constraint change, not a data change.
SQLite cannot drop a CHECK in place; the batch rebuild handles it.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e52b7f1a9c34"
down_revision: str | Sequence[str] | None = "d41a7c05b93f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WITH_CERTIFICATION = (
    "claim_type IN ('biodegradable', 'nature_friendly', 'generic_environmental', "
    "'carbon_neutrality', 'comparative', 'quantified_climate', 'recyclable', "
    "'certification')"
)
WITHOUT_CERTIFICATION = (
    "claim_type IN ('biodegradable', 'nature_friendly', 'generic_environmental', "
    "'carbon_neutrality', 'comparative', 'quantified_climate', 'recyclable')"
)


def upgrade() -> None:
    with op.batch_alter_table("analysis_verdicts", schema=None) as batch_op:
        batch_op.drop_constraint("verdict_claim_type", type_="check")
        batch_op.create_check_constraint("verdict_claim_type", WITH_CERTIFICATION)


def downgrade() -> None:
    with op.batch_alter_table("analysis_verdicts", schema=None) as batch_op:
        batch_op.drop_constraint("verdict_claim_type", type_="check")
        batch_op.create_check_constraint("verdict_claim_type", WITHOUT_CERTIFICATION)
