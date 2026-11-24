"""Réconciliation api_keys/legal_holds vers le modèle applicatif (E-09).

Revision ID: c6d7e8f9a012
Revises: b5c6d7e8f901
Create Date: 2026-11-21

Constat (revue de sécurité du 21/11/2026) : la migration d'origine
(``e8f9a1b2c3d4``) et le modèle ORM ont divergé. La chaîne Alembic créait
``api_keys(hashed_secret, scopes, revoked_at)`` et ``legal_holds`` sans
``is_active``/``expires_at``/``updated_at``, alors que toute la couche
applicative (``app/models/domain.py``, ``app/enterprise/service.py``) écrit
``key_hash``/``scopes_json``/``is_active``/``last_used_at``/``updated_at``.
Sur SQLite, ``create_all`` construit le schéma depuis le ORM et masque
l'écart ; sur toute base migrée (staging, production, fixtures PostgreSQL),
chaque opération ORM sur ces tables échoue sur une colonne inexistante —
le même genre de défaut qu'A-01.

Cette migration aligne le schéma migré sur le modèle applicatif :

* ``api_keys`` : ``hashed_secret`` → ``key_hash`` (contrainte d'unicité
  ajoutée, conforme au ORM), ``scopes`` → ``scopes_json``, colonnes
  ``is_active`` (dérivée de ``revoked_at IS NULL``), ``last_used_at`` et
  ``updated_at`` (remplies à partir de ``created_at`` pour les lignes
  existantes). ``revoked_at`` est **conservé** comme trace historique de
  l'horodatage de révocation : l'application ne le lit plus, et il n'est pas
  réécrit ; en cas de retour arrière, il reste la seule mémoire des
  révocations antérieures.
* ``legal_holds`` : colonnes ``is_active``, ``expires_at``, ``updated_at``.

Les deux tables étant vides sur tout déploiement réel (l'écriture ORM y
échouait), aucune donnée n'est à migrer au-delà des lignes de test ; les
remplissages conditionnels ci-dessous restent exécutés pour honorer le
contrat de la migration.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6d7e8f9a012"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column("hashed_secret", new_column_name="key_hash")
        batch_op.alter_column("scopes", new_column_name="scopes_json")

    op.execute("UPDATE api_keys SET is_active = (revoked_at IS NULL)")
    op.execute("UPDATE api_keys SET updated_at = created_at WHERE updated_at IS NULL")

    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.create_unique_constraint("uq_api_keys_key_hash", ["key_hash"])

    with op.batch_alter_table("legal_holds") as batch_op:
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE legal_holds SET updated_at = created_at WHERE updated_at IS NULL")

    with op.batch_alter_table("legal_holds") as batch_op:
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    # Retour arrière : les colonnes applicatives disparaissent. Les
    # révocations consignées uniquement dans ``is_active`` (postérieures à la
    # migration) ne sont pas restituées dans ``revoked_at`` : la trace
    # historique reste ce qu'elle est, l'opérateur doit les réaffecter si un
    # tel retour arrière devait avoir lieu.
    with op.batch_alter_table("legal_holds") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("is_active")

    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_constraint("uq_api_keys_key_hash", type_="unique")
        batch_op.alter_column("key_hash", new_column_name="hashed_secret")
        batch_op.alter_column("scopes_json", new_column_name="scopes")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("last_used_at")
        batch_op.drop_column("is_active")
