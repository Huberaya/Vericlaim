"""alignement du schéma migré sur les modèles : clés API et dossiers de gel

Revision ID: c4d8b1e60f27
Revises: e91c4b7a25d0
Create Date: 2026-09-30 21:10:00.000000

**Défaut réel trouvé pendant C12**, en produisant la preuve HTTP sur PostgreSQL :

    GET /api/v1/privacy/export  ->  500
    UndefinedColumn: column legal_holds.is_active does not exist

Sur SQLite, la suite de tests construisait le schéma depuis les modèles
(``create_tables``) : les colonnes existaient donc toujours. Sur une base **migrée**
— la seule façon de déployer — deux tables de la migration ``e8f9a1b2c3d4`` ne
correspondaient pas aux modèles qui les utilisent :

``legal_holds`` (C20)
    le modèle déclare ``is_active`` et ``expires_at``, la migration ne les créait pas.
    Conséquence : l'export de données et la vérification de gel légal échouaient en 500,
    c'est-à-dire que le **droit d'accès et le droit à l'effacement** ne fonctionnaient
    pas sur une base migrée.

``api_keys`` (C13, offres Enterprise)
    le modèle déclare ``key_hash``, ``scopes_json``, ``is_active``, ``last_used_at`` et
    ``updated_at`` ; la migration créait ``hashed_secret``, ``scopes`` et ``revoked_at``.
    Conséquence : la création et la vérification d'une clé API échouaient de la même
    façon.

Les deux tables datent de la même migration et d'un produit non déployé : les noms
sont donc alignés sur les modèles, avec reprise des données au lieu d'une recréation.

Ce que cette migration ne fait pas : elle n'invente aucune donnée. ``is_active`` est
déduit de ``revoked_at`` (``NULL`` = active), ``updated_at`` reprend ``created_at``,
et ``last_used_at`` reste vide : ces deux colonnes ne contiennent que des valeurs
déductibles de ce qui existait déjà.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "c4d8b1e60f27"
down_revision: Union[str, Sequence[str], None] = "e91c4b7a25d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _json_type():
    if _is_postgresql():
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    # --- api_keys : les noms du modèle, avec la donnée existante -------------------
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.alter_column("hashed_secret", new_column_name="key_hash")
        batch_op.alter_column("scopes", new_column_name="scopes_json")
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )

    # ``revoked_at`` portait l'état de révocation : il devient ``is_active``, sans perte.
    op.execute("UPDATE api_keys SET is_active = (revoked_at IS NULL)")
    op.execute("UPDATE api_keys SET updated_at = created_at")

    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_column("revoked_at")
        # Le modèle déclare des longueurs plus strictes que la migration d'origine :
        # les tables datent de la même migration et ne portent pas de données de
        # production. Laisser un VARCHAR(255) là où le modèle annonce 100 ne casse rien
        # visiblement, mais c'est la même classe de divergence que le défaut corrigé ici.
        batch_op.alter_column(
            "name", existing_type=sa.String(length=255), type_=sa.String(length=100),
            existing_nullable=False,
        )
        batch_op.create_index(batch_op.f("ix_api_keys_key_hash"), ["key_hash"], unique=True)

    # --- legal_holds : colonnes manquantes -----------------------------------------
    with op.batch_alter_table("legal_holds", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )

    op.execute("UPDATE legal_holds SET updated_at = created_at")

    with op.batch_alter_table("legal_holds", schema=None) as batch_op:
        batch_op.alter_column(
            "case_reference",
            existing_type=sa.String(length=255),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
        batch_op.create_index(
            batch_op.f("ix_legal_holds_case_reference"), ["case_reference"], unique=False
        )

    if _is_postgresql():
        # PostgreSQL garde les valeurs par défaut une fois la reprise faite : elles
        # servent les nouvelles lignes, pas la migration.
        op.execute("ALTER TABLE api_keys ALTER COLUMN is_active SET DEFAULT true")
        op.execute("ALTER TABLE legal_holds ALTER COLUMN is_active SET DEFAULT true")


def downgrade() -> None:
    with op.batch_alter_table("legal_holds", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_legal_holds_case_reference"))
        batch_op.drop_column("updated_at")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("is_active")

    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_keys_key_hash"))
        batch_op.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.drop_column("updated_at")
        batch_op.drop_column("last_used_at")

        # ``CURRENT_TIMESTAMP`` et non ``now()`` : PostgreSQL accepte les deux, SQLite
    # seulement le premier — et une migration qui ne redescend pas n'a pas été essayée.
    op.execute(
        "UPDATE api_keys SET revoked_at = CASE WHEN is_active THEN NULL ELSE CURRENT_TIMESTAMP END"
    )

    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_column("is_active")
        batch_op.alter_column("scopes_json", new_column_name="scopes")
        batch_op.alter_column("key_hash", new_column_name="hashed_secret")
