"""Politique de recherche d'authentification pour les clés API (E-03).

Revision ID: b5c6d7e8f901
Revises: a7d4c9e2b510
Create Date: 2026-11-21

``api_keys`` est protégée par RLS (``FORCE``) avec une politique tenant : la
recherche par empreinte SHA-256 — qui précède la connaissance de
l'organisation — ne voyait aucune ligne, et l'authentification par clé était
impossible sans brèche.

Cette migration ajoute une politique de SELECT **complémentaire** (les
politiques permissives s'additionnent), bornée par le réglage transactionnel
``app.api_key_lookup`` : posé uniquement par le module
``app.identity.api_keys`` pour la durée d'une recherche par empreinte, il
n'ouvre aucune autre lecture. Le préfixe ``_`` n'est pas concerné : aucune
clé n'est exposée, seule la ligne correspondant à un secret déjà connu est
lisible.

Les politiques existantes ne sont pas modifiées : sans ce réglage, la
sémantique reste « aucune ligne sans contexte tenant ».
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f901"
down_revision: Union[str, Sequence[str], None] = "a7d4c9e2b510"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgresql():
        op.execute(
            '''CREATE POLICY "p_api_keys_auth_lookup" ON "api_keys"
                FOR SELECT
                USING (current_setting('app.api_key_lookup', true) = 'on')'''
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute('DROP POLICY IF EXISTS "p_api_keys_auth_lookup" ON "api_keys"')
