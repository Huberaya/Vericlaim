"""Authentification par clé API (E-03).

Avant ce module, les clés API étaient **créables mais jamais acceptées** : le
modèle les stockait (préfixe, empreinte SHA-256, périmètres, expiration,
``rate_limit_per_minute``) et l'interface les fabriquait, mais aucun chemin de
code n'authentifiait une requête avec — une intégration machine croyait
tourner alors qu'elle n'avait aucune portée.

Ce module est le chemin d'authentification manquant. Règles :

* **transport** — en-tête ``X-API-Key: vc_live_...`` ou
  ``Authorization: Bearer vc_live_...``. Une requête qui présente une clé
  (même invalide) n'est jamais réauthentifiée par cookie : pas de bascule
  silencieuse entre deux identités.
* **vérification** — recherche par l'empreinte SHA-256 du secret, puis contrôle
  ``is_active``, expiration, organisation active. Le secret brut n'est jamais
  stocké ni journalisé ; la réponse à une clé inconnue est identique à celle
  d'une clé révoquée.
* **périmètres** — le vocabulaire *machine* exclut l'administration humaine
  (``organization:*``, ``members:*``, ``validation:*``,
  ``evidence_requests:*``) : une clé ne gère ni les membres, ni les
  validations humaines, quels que soient les scopes stockés. Les périmètres
  non reconnus sont refusés à la création comme à l'usage.
* **RLS** — la lecture de ``api_keys`` est elle-même protégée par RLS
  (``FORCE``), ce qui interdirait la recherche par empreinte avant que
  l'organisation ne soit connue. La migration associée ouvre une politique de
  SELECT bornée par le réglage transactionnel ``app.api_key_lookup`` : posé
  par ce module pour la seule recherche, il ne donne accès à aucune autre
  lecture. Après résolution, le contexte tenant classique prend le relais.
* **débit** — ``rate_limit_per_minute`` est un contrat de la clé : il est
  toujours appliqué (indépendamment du commutateur global de limitation),
  par fenêtre fixe en mémoire (même réserve de multi-réplicas que E-02).
* **CSRF** — inapplicable : une requête portant un en-tête personnalisé
  n'est pas émettable en navigation simple cross-origin ; le jeton CSRF
  n'exige que les sessions cookie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.rate_limit import LIMITER
from app.models.domain import ApiKey, Organization, OrganizationStatus

API_KEY_HEADER: Final[str] = "X-API-Key"
API_KEY_PREFIX: Final[str] = "vc_live_"

#: Vocabulaire machine : les seuls périmètres qu'une clé peut porter et
#: exercer. Il correspond aux besoins d'intégration (import, analyse, lecture
#: probatoire) et exclut l'administration d'organisation et les workflows de
#: revue humaine, qui restent réservés aux sessions SSO.
MACHINE_SCOPES: Final[frozenset[str]] = frozenset(
    {
        "documents:read",
        "documents:manage",
        "catalog:read",
        "catalog:manage",
        "evidence:read",
        "evidence:manage",
        "audit:run",
        "audit:read",
        "rules:read",
    }
)


def is_machine_scope(scope: str) -> bool:
    return scope in MACHINE_SCOPES


@dataclass(frozen=True)
class ApiKeyPrincipal:
    """Principal machine : l'organisation de la clé, ses périmètres, pas de rôle."""

    key_id: UUID
    organization_id: UUID
    organization: Organization
    permissions: frozenset[str]
    #: Attribution d'audit : le membre ayant créé la clé (jamais un faux
    #: utilisateur). ``None`` pour une clé sans créateur enregistré.
    user_id: UUID | None
    rate_limit_per_minute: int

    @property
    def role_code(self) -> str:
        # Aucune opération de gestion de membres n'est joignable avec une clé
        # (périmètres humains interdits) ; cette valeur ne sert qu'au refus.
        return "api_key"


def _hash_key(raw_key: str) -> str:
    import hashlib

    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def extract_raw_api_key(headers: "dict[str, str]") -> str | None:
    """Lit la clé depuis ``X-API-Key`` ou ``Authorization: Bearer``.

    La recherche est insensible à la casse : les en-têtes HTTP le sont, et un
    dictionnaire de serveur (``dict(request.headers)``) livre des clés
    minuscules.
    """
    lookup = {str(name).lower(): value for name, value in headers.items()}
    candidate = (lookup.get("x-api-key") or "").strip()
    if candidate:
        return candidate
    authorization = lookup.get("authorization") or ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip().startswith(API_KEY_PREFIX):
        return value.strip()
    return None


def _lookup_api_key(db: Session, *, key_hash: str) -> ApiKey | None:
    """Recherche par empreinte, avec la brèche RLS assumée et bornée.

    Sur PostgreSQL, la politique tenant de ``api_keys`` masque toute ligne tant
    que l'organisation n'est pas connue — la recherche par empreinte précède
    cette connaissance. Le réglage transactionnel ``app.api_key_lookup``
    déverrouille le SELECT pour la seule durée de cette recherche (politique
    ``p_api_keys_auth_lookup``), puis est effacé. Le contexte tenant est posé
    juste après, sur l'organisation de la clé.
    """
    is_postgresql = db.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        db.execute(text("SELECT set_config('app.api_key_lookup', 'on', true)"))
    try:
        return db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    finally:
        if is_postgresql:
            db.execute(text("SELECT set_config('app.api_key_lookup', '', true)"))


def resolve_api_key_principal(db: Session, *, raw_key: str) -> ApiKeyPrincipal:
    """Valide une clé et retourne son principal, ou refuse 401/403/429.

    Toute clé présentée et invalide est un **401** : une requête ne bascule
    jamais sur la session cookie quand elle a explicitement présenté une clé.
    """
    if not raw_key.startswith(API_KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API invalide.",
        )
    api_key = _lookup_api_key(db, key_hash=_hash_key(raw_key))
    now = datetime.now(timezone.utc)
    if api_key is None or not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API invalide.",
        )
    if api_key.expires_at is not None:
        expires_at = api_key.expires_at if api_key.expires_at.tzinfo else api_key.expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Clé API expirée.",
            )

    # Le contexte tenant est posé avant toute lecture suivante : organisation,
    # puis éventuelles requêtes métier partagées avec la route appelante.
    from app.identity.service import set_db_request_context

    set_db_request_context(
        db,
        user_id=api_key.created_by_user_id or api_key.id,
        organization_id=api_key.organization_id,
    )
    organization = db.get(Organization, api_key.organization_id)
    if organization is None or organization.status != OrganizationStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API invalide.",
        )

    # Intersection, jamais l'union : une clé dont le stockage contiendrait un
    # périmètre humain (données antérieures à cette règle, écriture directe en
    # base) ne l'exerce pas.
    permissions = frozenset(scope for scope in (api_key.scopes_json or []) if is_machine_scope(scope))

    # Contractuel et toujours actif : la valeur stockée sur la clé est la
    # limite de cette clé, indépendamment du commutateur global (E-02).
    limit = int(api_key.rate_limit_per_minute or 120)
    allowed, _remaining, retry_after = LIMITER.check(f"api_key:{api_key.id}", "self", limit=limit)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Quota de la clé API atteint; réessayez plus tard.",
            headers={"Retry-After": str(max(1, retry_after)), "X-RateLimit-Limit": str(limit)},
        )

    api_key.last_used_at = now
    db.flush()

    return ApiKeyPrincipal(
        key_id=api_key.id,
        organization_id=api_key.organization_id,
        organization=organization,
        permissions=permissions,
        user_id=api_key.created_by_user_id,
        rate_limit_per_minute=limit,
    )


def validate_requested_scopes(scopes: list[str]) -> list[str]:
    """Refuse à la création les périmètres inconnus ou humains.

    Avant cette garde, n'importe quelle chaîne était acceptée comme « scope »,
    y compris une permission d'administration — stockée sans jamais être
    vérifiée, ce qui aurait ouvert la porte à un élargissement silencieux le
    jour où l'authentification par clé deviendrait réelle.
    """
    unknown = [scope for scope in scopes if not is_machine_scope(scope)]
    if unknown:
        raise ValueError(
            "Périmètre(s) de clé API non autorisé(s): "
            + ", ".join(sorted(unknown))
            + ". Les clés machine ne portent que: "
            + ", ".join(sorted(MACHINE_SCOPES))
            + "."
        )
    return list(dict.fromkeys(scopes))
