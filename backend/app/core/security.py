from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import ApiKey, Organization, get_db

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass
class TenantContext:
    organization: Organization
    api_key: ApiKey | None
    is_authenticated: bool
    scopes: list[str]


def hash_api_key(key: str) -> str:
    """Calcule l'empreinte SHA-256 d'une clé API secrète."""
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def generate_api_key(is_live: bool = True) -> tuple[str, str, str]:
    """
    Génère un triplet de clé API cryptographique :
    - raw_key: chaîne secrète retournée une unique fois au client (ex: vk_live_3f92...)
    - key_prefix: préfixe public pour affichage sécurisé (ex: vk_live_3f92)
    - hashed_key: empreinte SHA-256 stockée en base de données
    """
    prefix = "vk_live_" if is_live else "vk_test_"
    token = secrets.token_hex(20)
    raw_key = f"{prefix}{token}"
    key_prefix = raw_key[:12]
    hashed_key = hash_api_key(raw_key)
    return raw_key, key_prefix, hashed_key


def get_tenant_context(
    request: Request,
    api_key: str | None = Security(api_key_header),
    db: Session = Depends(get_db),
) -> TenantContext:
    """
    Résout le contexte organisationnel / tenant à partir de l'en-tête X-API-Key
    ou Authorization Bearer. Si aucun identifiant n'est fourni, bascule sur
    l'organisation par défaut 'default' en mode public / démo pour garantir
    la rétrocompatibilité absolue.
    """
    extracted_key = api_key
    auth_header = request.headers.get("Authorization")
    if not extracted_key and auth_header:
        if auth_header.startswith("Bearer "):
            extracted_key = auth_header[7:].strip()
        elif auth_header.startswith("ApiKey "):
            extracted_key = auth_header[7:].strip()

    if extracted_key:
        hashed = hash_api_key(extracted_key)
        key_record = db.scalar(
            select(ApiKey).where(ApiKey.hashed_key == hashed, ApiKey.is_active == True)
        )
        if not key_record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Clé API invalide, expirée ou révoquée.",
            )

        org = db.scalar(
            select(Organization).where(Organization.id == key_record.organization_id, Organization.is_active == True)
        )
        if not org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organisation rattachée inactive, suspendue ou introuvable.",
            )

        try:
            key_record.last_used_at_utc = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            pass

        return TenantContext(
            organization=org,
            api_key=key_record,
            is_authenticated=True,
            scopes=key_record.scopes or [],
        )

    # Mode démo public non-authentifié (isolation sur 'default')
    default_org = db.scalar(select(Organization).where(Organization.id == "default"))
    if not default_org:
        default_org = Organization(
            id="default",
            name="Organisation Principale (Démo)",
            slug="default-demo",
            tier="enterprise",
            created_at_utc=datetime.now(timezone.utc),
            is_active=True,
        )
        db.add(default_org)
        db.commit()

    return TenantContext(
        organization=default_org,
        api_key=None,
        is_authenticated=False,
        scopes=["audit:read", "audit:write", "batch:run", "admin"],
    )


def require_auth(tenant: TenantContext = Depends(get_tenant_context)) -> TenantContext:
    """Exige explicitement une clé API valide."""
    if not tenant.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise via l'en-tête 'X-API-Key'.",
        )
    return tenant


def require_scopes(required_scopes: Sequence[str]):
    """Vérifie la présence des scopes nécessaires dans la clé API active."""
    def scope_checker(tenant: TenantContext = Depends(require_auth)) -> TenantContext:
        user_scopes = set(tenant.scopes)
        if "admin" in user_scopes:
            return tenant
        for req in required_scopes:
            if req not in user_scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission insuffisante. Scope manquant : {req}",
                )
        return tenant

    return scope_checker
