from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Role


@dataclass(frozen=True)
class SystemRole:
    code: str
    name: str
    description: str
    permissions: tuple[str, ...]


SYSTEM_ROLES: Final[tuple[SystemRole, ...]] = (
    SystemRole(
        code="owner",
        name="Propriétaire",
        description="Responsable de l’organisation, des membres et de ses paramètres.",
        permissions=(
            "organization:read",
            "organization:manage",
            "members:read",
            "members:manage",
            "documents:read",
            "documents:manage",
            "catalog:read",
            "catalog:manage",
            "audit:run",
            "audit:read",
            "rules:read",
        ),
    ),
    SystemRole(
        code="admin",
        name="Administrateur",
        description="Administration opérationnelle des membres, documents et audits.",
        permissions=(
            "organization:read",
            "members:read",
            "members:manage",
            "documents:read",
            "documents:manage",
            "catalog:read",
            "catalog:manage",
            "audit:run",
            "audit:read",
            "rules:read",
        ),
    ),
    SystemRole(
        code="analyst",
        name="Analyste",
        description="Exécute les analyses et gère les documents de son organisation.",
        permissions=(
            "organization:read",
            "documents:read",
            "documents:manage",
            "catalog:read",
            "catalog:manage",
            "audit:run",
            "audit:read",
            "rules:read",
        ),
    ),
    SystemRole(
        code="viewer",
        name="Lecteur",
        description="Consulte les documents et éléments autorisés sans pouvoir lancer une analyse.",
        permissions=("organization:read", "documents:read", "catalog:read", "audit:read", "rules:read"),
    ),
)

SYSTEM_ROLE_BY_CODE: Final[dict[str, SystemRole]] = {role.code: role for role in SYSTEM_ROLES}


def ensure_system_roles(db: Session) -> None:
    """Create missing system roles and add newly introduced baseline permissions.

    Existing reviewed permissions are never removed or overwritten: for a
    system role, only missing permissions from the immutable baseline are
    appended.  This keeps local ``create_all`` databases and migrated databases
    aligned when a new product capability gains its own permission.
    """
    existing = {
        role.code: role
        for role in db.scalars(select(Role).where(Role.code.in_(SYSTEM_ROLE_BY_CODE))).all()
    }
    for definition in SYSTEM_ROLES:
        role = existing.get(definition.code)
        if role is None:
            db.add(
                Role(
                    code=definition.code,
                    name=definition.name,
                    description=definition.description,
                    is_system_role=True,
                    permissions_json=list(definition.permissions),
                )
            )
            continue
        if role.is_system_role:
            merged_permissions = list(dict.fromkeys([*role.permissions_json, *definition.permissions]))
            if merged_permissions != role.permissions_json:
                role.permissions_json = merged_permissions
    db.flush()
