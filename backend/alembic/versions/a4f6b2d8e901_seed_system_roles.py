"""seed system roles

Revision ID: a4f6b2d8e901
Revises: 7b3b4c985738
Create Date: 2026-09-24 18:10:00.000000

"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import context, op


# revision identifiers, used by Alembic.
revision: str = "a4f6b2d8e901"
down_revision: Union[str, Sequence[str], None] = "7b3b4c985738"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Stable IDs are useful for administration and make this idempotent data seed
# auditable. They are not credentials or authorization secrets.
SYSTEM_ROLE_ROWS = (
    {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "code": "owner",
        "name": "Propriétaire",
        "description": "Responsable de l’organisation, des membres et de ses paramètres.",
        "permissions_json": [
            "organization:read",
            "organization:manage",
            "members:read",
            "members:manage",
            "audit:run",
            "audit:read",
            "rules:read",
        ],
    },
    {
        "id": UUID("00000000-0000-0000-0000-000000000002"),
        "code": "admin",
        "name": "Administrateur",
        "description": "Administration opérationnelle des membres et des audits.",
        "permissions_json": [
            "organization:read",
            "members:read",
            "members:manage",
            "audit:run",
            "audit:read",
            "rules:read",
        ],
    },
    {
        "id": UUID("00000000-0000-0000-0000-000000000003"),
        "code": "analyst",
        "name": "Analyste",
        "description": "Exécute et consulte les analyses de son organisation.",
        "permissions_json": ["organization:read", "audit:run", "audit:read", "rules:read"],
    },
    {
        "id": UUID("00000000-0000-0000-0000-000000000004"),
        "code": "viewer",
        "name": "Lecteur",
        "description": "Consulte les éléments autorisés sans pouvoir lancer une analyse.",
        "permissions_json": ["organization:read", "audit:read", "rules:read"],
    },
)


def _roles_table() -> sa.Table:
    return sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_system_role", sa.Boolean()),
        sa.column("permissions_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _row_with_timestamps(row: dict[str, object], occurred_at: datetime) -> dict[str, object]:
    return {
        **row,
        "is_system_role": True,
        "created_at": occurred_at,
        "updated_at": occurred_at,
    }


def upgrade() -> None:
    roles = _roles_table()
    occurred_at = datetime.now(timezone.utc)
    rows = [_row_with_timestamps(row, occurred_at) for row in SYSTEM_ROLE_ROWS]

    if context.is_offline_mode():
        # SQLAlchemy's generic JSON type has no literal renderer for a Python
        # list in Alembic's ``--sql`` mode. Emit carefully escaped static JSON
        # instead; ON CONFLICT keeps the generated deployment script safe when
        # a reviewed role was already present.
        def sql_literal(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        for row in rows:
            permissions = json.dumps(row["permissions_json"], ensure_ascii=False, separators=(",", ":"))
            occurred = row["created_at"]
            assert isinstance(occurred, datetime)
            op.execute(
                "INSERT INTO roles "
                "(id, code, name, description, is_system_role, permissions_json, created_at, updated_at) "
                "VALUES ("
                f"{sql_literal(str(row['id']))}, "
                f"{sql_literal(str(row['code']))}, "
                f"{sql_literal(str(row['name']))}, "
                f"{sql_literal(str(row['description']))}, "
                "TRUE, "
                f"{sql_literal(permissions)}, "
                f"{sql_literal(occurred.isoformat())}, "
                f"{sql_literal(occurred.isoformat())}"
                ") ON CONFLICT (code) DO NOTHING"
            )
        return

    connection = op.get_bind()
    for row in rows:
        existing = connection.execute(
            sa.select(roles.c.code).where(roles.c.code == row["code"])
        ).scalar_one_or_none()
        if existing is None:
            connection.execute(sa.insert(roles).values(**row))


def downgrade() -> None:
    # Do not delete roles: memberships may reference them and role data can
    # have been reviewed or extended after the seed. The preceding schema
    # migration still drops the table when a full downgrade continues.
    pass
