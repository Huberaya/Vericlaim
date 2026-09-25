from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import ApiKey, Organization, get_db

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_clerk_jwks_client: jwt.PyJWKClient | None = None


def get_clerk_jwks_client() -> jwt.PyJWKClient:
    global _clerk_jwks_client
    if _clerk_jwks_client is None:
        _clerk_jwks_client = jwt.PyJWKClient(
            settings.clerk_jwks_url,
            cache_jwk_set=True,
            lifespan=3600,
        )
    return _clerk_jwks_client


def verify_clerk_jwt(token: str) -> dict[str, Any] | None:
    """Valide la signature cryptographique du JWT émis par Clerk via JWKS."""
    try:
        client = get_clerk_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True, "verify_aud": False},
        )
        return payload
    except Exception:
        return None


def provision_clerk_tenant(payload: dict[str, Any], db: Session) -> TenantContext:
    """
    Provisionne automatiquement une organisation dédiée pour l'utilisateur Clerk
    dans la base managée (Neon PostgreSQL), assurant l'isolation multi-tenant.
    """
    clerk_user_id = str(payload.get("sub", "user_unknown")).strip()
    org_id = payload.get("org_id") or f"clerk_{clerk_user_id}"
    base_slug = payload.get("org_slug") or f"org-{clerk_user_id[-8:]}"
    org_name = (
        payload.get("org_name")
        or payload.get("email")
        or f"Espace {clerk_user_id[-8:]}"
    )

    org = db.scalar(select(Organization).where(Organization.id == org_id))
    if not org:
        # Éviter tout conflit de slug unique
        existing_slug = db.scalar(select(Organization).where(Organization.slug == base_slug))
        if existing_slug and existing_slug.id != org_id:
            org_slug = f"{base_slug[:56]}-{clerk_user_id[-6:]}"
        else:
            org_slug = base_slug

        org = Organization(
            id=org_id,
            name=str(org_name)[:128],
            slug=str(org_slug)[:64],
            tier="standard",
            created_at_utc=datetime.now(timezone.utc),
            is_active=True,
        )
        db.add(org)
        db.commit()
        db.refresh(org)

    return TenantContext(
        organization=org,
        api_key=None,
        is_authenticated=True,
        scopes=["audit:read", "audit:write", "batch:run", "admin"],
        user_id=clerk_user_id,
    )


@dataclass
class TenantContext:
    organization: Organization
    api_key: ApiKey | None
    is_authenticated: bool
    scopes: list[str]
    user_id: str | None = None


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
    ou Authorization Bearer (Clerk JWT ou API Key). Si aucun identifiant n'est fourni,
    bascule sur l'organisation par défaut 'default' en mode public / démo.
    """
    extracted_key = api_key
    auth_header = request.headers.get("Authorization")
    if not extracted_key and auth_header:
        if auth_header.startswith("Bearer "):
            extracted_key = auth_header[7:].strip()
        elif auth_header.startswith("ApiKey "):
            extracted_key = auth_header[7:].strip()

    if extracted_key:
        # 1. Vérification si jeton JWT Clerk (3 segments séparés par des points)
        if extracted_key.startswith("eyJ") or (extracted_key.count(".") == 2 and not extracted_key.startswith("vk_")):
            clerk_payload = verify_clerk_jwt(extracted_key)
            if clerk_payload:
                return provision_clerk_tenant(clerk_payload, db)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session Clerk invalide, expirée ou signature non reconnue.",
            )

        # 2. Vérification par clé API VeriClaim
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
            user_id=None,
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
        try:
            db.add(default_org)
            db.commit()
            db.refresh(default_org)
        except Exception:
            pass

    return TenantContext(
        organization=default_org,
        api_key=None,
        is_authenticated=False,
        scopes=["audit:read", "audit:write", "batch:run", "admin"],
        user_id=None,
    )


def require_auth(context: TenantContext = Depends(get_tenant_context)) -> TenantContext:
    """Exige que l'appel soit authentifié par clé API ou session Clerk."""
    if not context.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise : fournissez un en-tête X-API-Key ou Bearer (Clerk).",
        )
    return context


def require_scopes(required_scopes: Sequence[str]):
    """Décorateur/Dépendance validant les permissions de l'appelant."""
    def _checker(context: TenantContext = Depends(require_auth)) -> TenantContext:
        user_scopes = set(context.scopes)
        missing = [scope for scope in required_scopes if scope not in user_scopes]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permissions insuffisantes. Scopes manquants : {', '.join(missing)}",
            )
        return context
    return _checker
