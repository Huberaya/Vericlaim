"""Limitation de débit applicative (E-02).

Le modèle de données prévoit ``rate_limit_per_minute`` sur les clés API mais,
avant ce module, rien n'appliquait de limite de débit nulle part : ni sur
``/api/v1/engine/evaluate`` (traitement OCR coûteux), ni sur le flux d'entrée,
ni par clé. Ce module comble l'écart avec un limiteur à fenêtre fixe :

* **par clé API** — toujours actif, y compris lorsque le commutateur global est
  off : la valeur stockée sur la clé est un contrat, pas une décoration ;
* **par session ou IP** — pour les requêtes cookie, borné par le hash du jeton
  de session ; pour les requêtes anonymes, par adresse cliente.

Limites assumées et documentées : l'état est **dans le processus** (pas de
Redis ni de file partagée dans cette architecture). Avec plusieurs réplicas,
chaque réplica applique la limite indépendamment — le plafond réel est donc
``limite × réplicas``. C'est une borne anti-abus et anti-DoS grossière, pas un
quota contractuel distribué.
"""

from __future__ import annotations

import hashlib
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings as module_settings
from app.core.config import settings_of


class RateLimiter:
    """Fenêtre fixe par (seau, clé), sûre pour usage multi-threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], tuple[int, int]] = {}

    def check(self, bucket: str, key: str, *, limit: int, now: float | None = None) -> tuple[bool, int, int]:
        """Retourne ``(autorisé, restant, retry_after_seconds)``.

        Une requête refusée ne consomme rien : elle attend la fenêtre suivante.
        """
        if limit <= 0:
            return True, 0, 0
        instant = int(time.time() if now is None else now)
        window = instant // 60
        with self._lock:
            state = self._windows.get((bucket, key))
            if state is None or state[0] != window:
                self._windows[(bucket, key)] = (window, 1)
                self._sweep_locked(window)
                return True, limit - 1, 0
            used = state[1]
            if used >= limit:
                retry_after = 60 - (instant % 60)
                return False, 0, retry_after
            self._windows[(bucket, key)] = (window, used + 1)
            return True, limit - used - 1, 0

    def _sweep_locked(self, current_window: int) -> None:
        # Les entrées des fenêtres précédentes ne servent plus. Le balayage est
        # amorti par le renouvellement naturel des fenêtres, sans minuteur.
        if len(self._windows) > 4096:
            self._windows = {
                (bucket, key): (window, used)
                for (bucket, key), (window, used) in self._windows.items()
                if window >= current_window
            }

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


#: Unique par processus : le middleware et l'authentification par clé API
#: partagent la même horloge pour qu'un même appelant ne dispose pas de deux
#: budgets indépendants sur la même route.
LIMITER = RateLimiter()


def _client_ip(request: Request) -> str:
    # Derrière le proxy de plateforme (Vercel, ingress), le couple
    # X-Forwarded-For est la seule source utile ; en direct, le peer socket.
    forwarded = request.headers.get("x-forwarded-for", "")
    candidate = forwarded.split(",", 1)[0].strip()
    return candidate or (request.client.host if request.client else "unknown")


def _session_bucket_key(request: Request) -> str:
    """Préfère le jeton de session (budget personnel) à l'IP (budget partagé)."""
    from app.identity.security import SESSION_COOKIE_NAME

    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        return "session:" + hashlib.sha256(raw_token.encode("utf-8")).hexdigest()[:32]
    return "ip:" + _client_ip(request)


def _classify(request: Request) -> tuple[str, str, str] | None:
    """Retourne ``(seau, clé, paramètre de limite)`` ou ``None`` si non borné.

    Les lectures GET ne sont pas bornées : la pagination est cursor-based et
    bornée en taille ; seules les routes mutantes et les traitements coûteux
    consomment un budget.
    """
    path = request.url.path
    if request.method == "OPTIONS" or request.method in {"GET", "HEAD"}:
        return None
    if path.startswith("/api/v1/auth/"):
        return "auth", "ip:" + _client_ip(request), "api_rate_limit_auth_per_minute"
    if path.startswith("/api/v1/"):
        if path.startswith("/api/v1/engine/evaluate"):
            return "evaluate", _session_bucket_key(request), "api_rate_limit_evaluate_per_minute"
        return "write", _session_bucket_key(request), "api_rate_limit_write_per_minute"
    return None


def _limited_response(retry_after: int, limit: int, remaining: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Trop de requêtes; réessayez plus tard."},
        headers={
            "Retry-After": str(max(1, retry_after)),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, remaining)),
            "Cache-Control": "no-store",
        },
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Borne les requêtes cookie/anonymes ; les clés API sont bornées à
    l'authentification (``app.identity.api_keys``), par ``rate_limit_per_minute``."""

    async def dispatch(self, request: Request, call_next) -> Response:
        active = settings_of(getattr(request, "app", None), fallback=module_settings)
        if not getattr(active, "api_rate_limit_enabled", False):
            return await call_next(request)
        plan = _classify(request)
        if plan is None:
            return await call_next(request)
        bucket, key, limit_attr = plan
        limit = int(getattr(active, limit_attr))
        allowed, remaining, retry_after = LIMITER.check(bucket, key, limit=limit)
        if not allowed:
            return _limited_response(retry_after, limit, remaining)
        response = await call_next(request)
        try:
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        except Exception:  # noqa: BLE001 - jamais bloquant
            pass
        return response
